from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import psycopg2.extras


STORAGE_LEGACY = "LEGACY_PDF"
STORAGE_SNAPSHOT = "SNAPSHOT"
STORAGE_HYBRID = "HYBRID"
STORAGE_MODES = (STORAGE_LEGACY, STORAGE_SNAPSHOT, STORAGE_HYBRID)
RECEIPT_TEMPLATE_VERSION = "receipt_html_v1"
RECEIPT_SNAPSHOT_SCHEMA_VERSION = 1


class ReceiptDocumentError(RuntimeError):
    pass


class SnapshotMissingError(ReceiptDocumentError):
    pass


class SnapshotHashError(ReceiptDocumentError):
    pass


class UnknownTemplateError(ReceiptDocumentError):
    pass


RECEIPT_DOCUMENT_MIGRATION_SQL = """
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS document_storage_mode TEXT;

UPDATE recibos
SET document_storage_mode='LEGACY_PDF'
WHERE document_storage_mode IS NULL
   OR BTRIM(document_storage_mode)='';

ALTER TABLE recibos
  ALTER COLUMN document_storage_mode SET DEFAULT 'SNAPSHOT';
ALTER TABLE recibos
  ALTER COLUMN document_storage_mode SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname='recibos_document_storage_mode_check'
      AND conrelid='recibos'::regclass
  ) THEN
    ALTER TABLE recibos
      ADD CONSTRAINT recibos_document_storage_mode_check
      CHECK(document_storage_mode IN ('LEGACY_PDF','SNAPSHOT','HYBRID'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS recibo_document_versions(
  id BIGSERIAL PRIMARY KEY,
  recibo_id INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  estado_documento TEXT NOT NULL,
  estado_facturacion TEXT NOT NULL,
  snapshot_jsonb JSONB NOT NULL,
  snapshot_hash TEXT NOT NULL CHECK(snapshot_hash ~ '^[0-9a-f]{64}$'),
  template_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by TEXT NOT NULL,
  is_current BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT recibo_document_versions_receipt_fk
    FOREIGN KEY(recibo_id) REFERENCES recibos(id) ON DELETE RESTRICT,
  CONSTRAINT recibo_document_versions_receipt_version_uq
    UNIQUE(recibo_id,version)
);

CREATE INDEX IF NOT EXISTS idx_recibo_document_versions_receipt
  ON recibo_document_versions(recibo_id);
CREATE INDEX IF NOT EXISTS idx_recibo_document_versions_receipt_version
  ON recibo_document_versions(recibo_id,version DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_recibo_document_versions_current
  ON recibo_document_versions(recibo_id)
  WHERE is_current=TRUE;
CREATE INDEX IF NOT EXISTS idx_recibo_document_versions_receipt_current
  ON recibo_document_versions(recibo_id,is_current);

ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_deleted_at TIMESTAMPTZ;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_checksum TEXT;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_size BIGINT;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_backup_reference TEXT;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_deletion_batch TEXT;

ALTER TABLE pdf_storage ADD COLUMN IF NOT EXISTS document_type TEXT;
ALTER TABLE pdf_storage ADD COLUMN IF NOT EXISTS owner_receipt_id INTEGER;
UPDATE pdf_storage p
SET document_type='RECEIPT_LEGACY',owner_receipt_id=r.id
FROM recibos r
WHERE r.pdf_filename=p.filename
  AND COALESCE(p.document_type,'')='';
UPDATE pdf_storage
SET document_type='UNKNOWN'
WHERE COALESCE(document_type,'')='';
ALTER TABLE pdf_storage ALTER COLUMN document_type SET DEFAULT 'UNKNOWN';
ALTER TABLE pdf_storage ALTER COLUMN document_type SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pdf_storage_document_type
  ON pdf_storage(document_type);
CREATE INDEX IF NOT EXISTS idx_pdf_storage_owner_receipt
  ON pdf_storage(owner_receipt_id)
  WHERE owner_receipt_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS recibo_document_migration(
  recibo_id INTEGER PRIMARY KEY,
  migration_status TEXT NOT NULL DEFAULT 'PENDING',
  classification TEXT,
  source_pdf_filename TEXT,
  source_pdf_size BIGINT,
  source_pdf_hash TEXT,
  backup_location TEXT,
  backup_hash TEXT,
  backup_verified BOOLEAN NOT NULL DEFAULT FALSE,
  snapshot_version INTEGER,
  snapshot_hash TEXT,
  render_verified BOOLEAN NOT NULL DEFAULT FALSE,
  validation_jsonb JSONB,
  deletion_batch_id TEXT,
  pdf_deleted_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  verified_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT recibo_document_migration_receipt_fk
    FOREIGN KEY(recibo_id) REFERENCES recibos(id) ON DELETE RESTRICT,
  CONSTRAINT recibo_document_migration_status_check CHECK(
    migration_status IN(
      'PENDING','ANALYZING','RECONSTRUCTIBLE','MIGRATED_HYBRID',
      'RENDER_VERIFIED','BACKED_UP','DELETION_CANDIDATE',
      'PDF_DELETED','VERIFIED','NEEDS_REVIEW','FAILED','RESTORED'
    )
  ),
  CONSTRAINT recibo_document_migration_classification_check CHECK(
    classification IS NULL OR classification IN(
      'COMPLETE','WITHOUT_ITEMS','TOTAL_MISMATCH','MISSING_HEADER_DATA',
      'MISSING_PDF','INVALID_STATE','SNAPSHOT_EXISTS','ALREADY_MIGRATED'
    )
  )
);
CREATE INDEX IF NOT EXISTS idx_recibo_document_migration_status
  ON recibo_document_migration(migration_status,recibo_id);
CREATE INDEX IF NOT EXISTS idx_recibo_document_migration_classification
  ON recibo_document_migration(classification,recibo_id);
ALTER TABLE recibo_document_migration ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION enforce_receipt_document_version_immutable()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Las versiones documentales de recibos son inmutables';
  END IF;
  IF NEW.recibo_id IS DISTINCT FROM OLD.recibo_id
     OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.estado_documento IS DISTINCT FROM OLD.estado_documento
     OR NEW.estado_facturacion IS DISTINCT FROM OLD.estado_facturacion
     OR NEW.snapshot_jsonb IS DISTINCT FROM OLD.snapshot_jsonb
     OR NEW.snapshot_hash IS DISTINCT FROM OLD.snapshot_hash
     OR NEW.template_version IS DISTINCT FROM OLD.template_version
     OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
    RAISE EXCEPTION 'El contenido de una versión documental no puede modificarse';
  END IF;
  RETURN NEW;
END;
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname='trg_receipt_document_version_immutable'
      AND tgrelid='recibo_document_versions'::regclass
      AND NOT tgisinternal
  ) THEN
    CREATE TRIGGER trg_receipt_document_version_immutable
    BEFORE UPDATE OR DELETE ON recibo_document_versions
    FOR EACH ROW EXECUTE FUNCTION enforce_receipt_document_version_immutable();
  END IF;
END $$;

ALTER TABLE recibo_document_versions ENABLE ROW LEVEL SECURITY;
"""


def apply_receipt_document_migration(connection) -> None:
    connection.executescript(RECEIPT_DOCUMENT_MIGRATION_SQL)


def canonical_snapshot_json(snapshot: dict[str, Any]) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def calculate_snapshot_hash(snapshot: dict[str, Any]) -> str:
    canonical = canonical_snapshot_json(snapshot)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _float(value) -> float:
    return float(value or 0)


def _int(value) -> int:
    return int(value or 0)


def build_receipt_snapshot(
    connection,
    receipt_id: int,
    *,
    document_context: dict | None = None,
) -> dict[str, Any]:
    context = dict(document_context or {})
    row = connection.execute(
        """SELECT r.id,r.numero,r.nombre,r.fecha,r.created_at,r.username,
                  r.admission_username_snapshot,r.dx,r.sala,r.total,r.ars,
                  r.tipo_cobertura,r.numero_autorizacion,r.estado_documento,
                  r.estado_facturacion,r.admission_atencion_id,
                  r.admission_paciente_id,r.admission_nss_snapshot,
                  r.admission_cedula_snapshot,r.admission_ars_snapshot,
                  r.admission_source_instance_id,r.turno_origen_id,
                  r.turno_procesamiento_id,r.herencia_estado,
                  r.revision_version,r.service_type,r.specialty_snapshot,
                  r.document_storage_mode,
                  COALESCE(NULLIF(u.full_name,''),r.username,'Sistema')
                    AS visible_user
           FROM recibos r
           LEFT JOIN users u ON u.username=r.username
           WHERE r.id=%s""",
        (int(receipt_id),),
    ).fetchone()
    if not row:
        raise SnapshotMissingError("El recibo no existe.")

    item_rows = connection.execute(
        """SELECT id,categoria,nombre,precio_unit,cantidad,total,ars
           FROM recibo_items
           WHERE recibo_id=%s
           ORDER BY id""",
        (int(receipt_id),),
    ).fetchall()
    items = []
    subtotals: dict[str, float] = {}
    for position, raw_item in enumerate(item_rows, start=1):
        category = str(raw_item["categoria"] or "")
        item_total = _float(raw_item["total"])
        subtotals[category] = subtotals.get(category, 0.0) + item_total
        items.append(
            {
                "order": position,
                "category": category,
                "name": str(raw_item["nombre"] or ""),
                "quantity": _int(raw_item["cantidad"]),
                "unit_price": _float(raw_item["precio_unit"]),
                "subtotal": item_total,
                "ars": str(raw_item["ars"] or ""),
            }
        )

    generated_at = str(row["created_at"] or "")
    visible_user = str(
        context.get("visible_user") or row["visible_user"] or row["username"] or "Sistema"
    )
    snapshot = {
        "schema_version": RECEIPT_SNAPSHOT_SCHEMA_VERSION,
        "template_version": RECEIPT_TEMPLATE_VERSION,
        "header": {
            "receipt_id": int(row["id"]),
            "receipt_number": int(row["numero"]),
            "service_date": str(row["fecha"] or ""),
            "generated_at": generated_at,
            "generated_by": str(row["username"] or ""),
            "admission_user": str(row["admission_username_snapshot"] or ""),
            "diagnosis": str(row["dx"] or ""),
            "room_charge": _float(row["sala"]),
            "ars": str(row["ars"] or ""),
            "coverage": str(row["tipo_cobertura"] or ""),
            "authorization_number": str(row["numero_autorizacion"] or ""),
            "document_state": str(row["estado_documento"] or ""),
            "billing_status": str(row["estado_facturacion"] or ""),
            "service_type": str(row["service_type"] or "EMERGENCIA"),
            "specialty": str(row["specialty_snapshot"] or ""),
            "revision_version": _int(row["revision_version"]),
        },
        "patient": {
            "name": str(row["nombre"] or ""),
            "nss": str(row["admission_nss_snapshot"] or ""),
            "cedula": str(row["admission_cedula_snapshot"] or ""),
            "admission_patient_id": (
                int(row["admission_paciente_id"])
                if row["admission_paciente_id"] is not None
                else None
            ),
            "administrative_ars": str(row["admission_ars_snapshot"] or ""),
        },
        "items": items,
        "totals": {
            "by_category": {
                key: round(value, 2)
                for key, value in sorted(subtotals.items(), key=lambda pair: pair[0])
            },
            "items_subtotal": round(sum(subtotals.values()), 2),
            "room": round(_float(row["sala"]), 2),
            "general": round(_float(row["total"]), 2),
            "currency": "DOP",
            "currency_label": "RD$",
        },
        "document": {
            "document_state": str(row["estado_documento"] or ""),
            "billing_status": str(row["estado_facturacion"] or ""),
            "authorization_number": str(row["numero_autorizacion"] or ""),
            "visible_user": visible_user,
            "hospital_line_1": str(
                context.get("hospital_line_1") or "HOSPITAL PROVINCIAL"
            ),
            "hospital_line_2": str(
                context.get("hospital_line_2") or "DR. ÁNGEL CONTRERAS MEJÍA"
            ),
            "document_title": str(
                context.get("document_title")
                or "DETALLE DE FACTURACIÓN DE EMERGENCIA"
            ),
            "template_version": RECEIPT_TEMPLATE_VERSION,
            "schema_version": RECEIPT_SNAPSHOT_SCHEMA_VERSION,
        },
        "links": {
            "admission_attention_id": (
                int(row["admission_atencion_id"])
                if row["admission_atencion_id"] is not None
                else None
            ),
            "admission_source_instance_id": str(
                row["admission_source_instance_id"] or ""
            ),
            "origin_turn_id": (
                int(row["turno_origen_id"])
                if row["turno_origen_id"] is not None
                else None
            ),
            "processing_turn_id": (
                int(row["turno_procesamiento_id"])
                if row["turno_procesamiento_id"] is not None
                else None
            ),
            "inheritance_status": str(row["herencia_estado"] or ""),
        },
    }
    return snapshot


def save_receipt_document_snapshot(
    connection,
    receipt_id: int,
    created_by: str,
    *,
    document_context: dict | None = None,
    target_storage_mode: str | None = None,
) -> dict[str, Any]:
    receipt = connection.execute(
        """SELECT id,document_storage_mode
           FROM recibos WHERE id=%s FOR UPDATE""",
        (int(receipt_id),),
    ).fetchone()
    if not receipt:
        raise SnapshotMissingError("El recibo no existe.")

    snapshot = build_receipt_snapshot(
        connection,
        int(receipt_id),
        document_context=document_context,
    )
    digest = calculate_snapshot_hash(snapshot)
    version_row = connection.execute(
        """SELECT COALESCE(MAX(version),0)+1 AS next_version
           FROM recibo_document_versions
           WHERE recibo_id=%s""",
        (int(receipt_id),),
    ).fetchone()
    version = int(version_row["next_version"] or 1)
    connection.execute(
        """UPDATE recibo_document_versions
           SET is_current=FALSE
           WHERE recibo_id=%s AND is_current=TRUE""",
        (int(receipt_id),),
    )
    connection.execute(
        """INSERT INTO recibo_document_versions(
               recibo_id,version,estado_documento,estado_facturacion,
               snapshot_jsonb,snapshot_hash,template_version,schema_version,
               created_at,created_by,is_current
           ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,TRUE)""",
        (
            int(receipt_id),
            version,
            str(snapshot["header"]["document_state"]),
            str(snapshot["header"]["billing_status"]),
            psycopg2.extras.Json(snapshot, dumps=lambda value: canonical_snapshot_json(value)),
            digest,
            RECEIPT_TEMPLATE_VERSION,
            RECEIPT_SNAPSHOT_SCHEMA_VERSION,
            str(created_by or "Sistema"),
        ),
    )
    current_mode = str(receipt["document_storage_mode"] or STORAGE_LEGACY)
    requested_mode = str(target_storage_mode or "").strip().upper()
    if requested_mode and requested_mode not in STORAGE_MODES:
        raise ReceiptDocumentError(
            f"Modo documental no válido: {requested_mode}"
        )
    next_mode = requested_mode or (
        STORAGE_HYBRID
        if current_mode in (STORAGE_LEGACY, STORAGE_HYBRID)
        else STORAGE_SNAPSHOT
    )
    connection.execute(
        """UPDATE recibos
           SET document_storage_mode=%s,pdf_sync_error=NULL
           WHERE id=%s""",
        (next_mode, int(receipt_id)),
    )
    return {
        "receipt_id": int(receipt_id),
        "version": version,
        "snapshot": snapshot,
        "snapshot_hash": digest,
        "template_version": RECEIPT_TEMPLATE_VERSION,
        "schema_version": RECEIPT_SNAPSHOT_SCHEMA_VERSION,
        "storage_mode": next_mode,
    }


def load_current_receipt_snapshot(connection, receipt_id: int) -> dict[str, Any]:
    row = connection.execute(
        """SELECT v.recibo_id,v.version,v.estado_documento,v.estado_facturacion,
                  v.snapshot_jsonb,v.snapshot_hash,v.template_version,
                  v.schema_version,v.created_at,v.created_by,
                  r.numero,r.document_storage_mode,r.pdf_filename
           FROM recibo_document_versions v
           JOIN recibos r ON r.id=v.recibo_id
           WHERE v.recibo_id=%s AND v.is_current=TRUE""",
        (int(receipt_id),),
    ).fetchone()
    if not row:
        raise SnapshotMissingError("El recibo no tiene una versión documental reconstruible.")
    snapshot = dict(row["snapshot_jsonb"] or {})
    expected = str(row["snapshot_hash"] or "")
    actual = calculate_snapshot_hash(snapshot)
    if not expected or actual != expected:
        raise SnapshotHashError(
            f"El snapshot documental del recibo {int(receipt_id)} no superó la validación de integridad."
        )
    template_version = str(row["template_version"] or "")
    if template_version != RECEIPT_TEMPLATE_VERSION:
        raise UnknownTemplateError(
            f"La plantilla documental {template_version or '(vacía)'} no está disponible."
        )
    return {
        "receipt_id": int(row["recibo_id"]),
        "receipt_number": int(row["numero"]),
        "version": int(row["version"]),
        "snapshot": snapshot,
        "snapshot_hash": expected,
        "template_version": template_version,
        "schema_version": int(row["schema_version"]),
        "created_at": str(row["created_at"]),
        "created_by": str(row["created_by"] or ""),
        "storage_mode": str(row["document_storage_mode"] or STORAGE_LEGACY),
        "pdf_filename": str(row["pdf_filename"] or ""),
    }


def load_latest_receipt_snapshot(connection, receipt_id: int) -> dict[str, Any]:
    """Load the newest valid stored version, including pre-current history.

    Older deployments did not always leave the ``is_current`` marker aligned
    with the newest version.  This is a read-only compatibility path; it never
    promotes or rewrites a stored historical version.
    """
    row = connection.execute(
        """SELECT v.recibo_id,v.version,v.estado_documento,v.estado_facturacion,
                  v.snapshot_jsonb,v.snapshot_hash,v.template_version,
                  v.schema_version,v.created_at,v.created_by,
                  r.numero,r.document_storage_mode,r.pdf_filename
           FROM recibo_document_versions v
           JOIN recibos r ON r.id=v.recibo_id
           WHERE v.recibo_id=%s
           ORDER BY v.is_current DESC,v.version DESC
           LIMIT 1""",
        (int(receipt_id),),
    ).fetchone()
    if not row:
        raise SnapshotMissingError(
            "El recibo no contiene una versión estructurada histórica."
        )
    snapshot = dict(row["snapshot_jsonb"] or {})
    expected = str(row["snapshot_hash"] or "")
    actual = calculate_snapshot_hash(snapshot)
    if not expected or actual != expected:
        raise SnapshotHashError(
            "La versión estructurada histórica del recibo no superó la "
            "validación de integridad."
        )
    template_version = str(row["template_version"] or "")
    if template_version != RECEIPT_TEMPLATE_VERSION:
        raise UnknownTemplateError(
            f"La plantilla documental {template_version or '(vacía)'} no está disponible."
        )
    return {
        "receipt_id": int(row["recibo_id"]),
        "receipt_number": int(row["numero"]),
        "version": int(row["version"]),
        "snapshot": snapshot,
        "snapshot_hash": expected,
        "template_version": template_version,
        "schema_version": int(row["schema_version"]),
        "created_at": str(row["created_at"]),
        "created_by": str(row["created_by"] or ""),
        "storage_mode": str(row["document_storage_mode"] or STORAGE_LEGACY),
        "pdf_filename": str(row["pdf_filename"] or ""),
    }


def audit_historical_receipts(connection, sample_limit: int = 20) -> dict[str, Any]:
    rows = connection.execute(
        """SELECT r.id,r.numero,r.nombre,r.fecha,r.total,r.sala,r.ars,
                  r.estado_documento,r.estado_facturacion,r.pdf_filename,
                  r.document_storage_mode,
                  COUNT(ri.id) AS item_count,
                  COALESCE(SUM(ri.total),0) AS items_total,
                  EXISTS(
                    SELECT 1 FROM recibo_document_versions v
                    WHERE v.recibo_id=r.id AND v.is_current=TRUE
                  ) AS snapshot_exists,
                  EXISTS(
                    SELECT 1 FROM pdf_storage p
                    WHERE p.filename=r.pdf_filename
                  ) AS pdf_exists
           FROM recibos r
           LEFT JOIN recibo_items ri ON ri.recibo_id=r.id
           GROUP BY r.id
           ORDER BY r.id""",
    ).fetchall()
    counts: Counter[str] = Counter()
    samples: dict[str, list[int]] = {}
    valid_document_states = {"PRELIMINAR", "LISTO_AUDITORIA", "FINAL"}
    valid_billing_states = {
        "PENDIENTE",
        "FACTURADO",
        "NO_FACTURADO",
        "SIN_CLASIFICAR",
    }
    for row in rows:
        calculated = _float(row["items_total"]) + _float(row["sala"])
        total = _float(row["total"])
        tolerance = max(0.01, abs(total) * 0.001)
        if bool(row["snapshot_exists"]):
            classification = "SNAPSHOT_EXISTENTE"
        elif _int(row["item_count"]) <= 0:
            classification = "SIN_ITEMS"
        elif abs(calculated - total) > tolerance:
            classification = "TOTAL_NO_CUADRA"
        elif (
            not str(row["nombre"] or "").strip()
            or not str(row["fecha"] or "").strip()
            or str(row["estado_documento"] or "") not in valid_document_states
            or str(row["estado_facturacion"] or "") not in valid_billing_states
        ):
            classification = "DATOS_INCOMPLETOS"
        elif not str(row["pdf_filename"] or "").strip() or not bool(row["pdf_exists"]):
            classification = "PDF_NO_ENCONTRADO"
        else:
            classification = "RECONSTRUIBLE"
        counts[classification] += 1
        bucket = samples.setdefault(classification, [])
        if len(bucket) < max(0, int(sample_limit)):
            bucket.append(int(row["id"]))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "total_receipts": len(rows),
        "counts": dict(sorted(counts.items())),
        "sample_receipt_ids": samples,
    }
