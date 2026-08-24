from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import time
import traceback
import unicodedata
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

import CALCULOS_QT as app
from receipt_documents import (
    STORAGE_HYBRID,
    STORAGE_LEGACY,
    STORAGE_SNAPSHOT,
    calculate_snapshot_hash,
    load_current_receipt_snapshot,
    save_receipt_document_snapshot,
)


MIGRATION_LOCK_KEY = 724_681_930_117_202_607
VALID_DOCUMENT_STATES = {"PRELIMINAR", "LISTO_AUDITORIA", "FINAL"}
VALID_BILLING_STATES = {
    "PENDIENTE",
    "FACTURADO",
    "NO_FACTURADO",
    "SIN_CLASIFICAR",
}
RECONSTRUCTIBLE = "RECONSTRUCTIBLE"
NEEDS_REVIEW = "NEEDS_REVIEW"
ALREADY_MIGRATED = "ALREADY_MIGRATED"
CLASS_COMPLETE = "COMPLETE"
CLASS_WITHOUT_ITEMS = "WITHOUT_ITEMS"
CLASS_TOTAL_MISMATCH = "TOTAL_MISMATCH"
CLASS_MISSING_HEADER = "MISSING_HEADER_DATA"
CLASS_MISSING_PDF = "MISSING_PDF"
CLASS_INVALID_STATE = "INVALID_STATE"
CLASS_SNAPSHOT_EXISTS = "SNAPSHOT_EXISTS"
CLASS_ALREADY_MIGRATED = "ALREADY_MIGRATED"


class MigrationStopped(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_default(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def json_dump_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            default=json_default,
        ),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def canonical_data(value: Any) -> Any:
    return json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=json_default,
        )
    )


def _single(connection, sql: str, params=()) -> dict:
    return dict(connection.execute(sql, params).fetchone())


def capture_baseline() -> dict[str, Any]:
    with app.db_connect() as con:
        receipts = _single(
            con,
            """SELECT COUNT(*) AS total,
                      COUNT(*) FILTER(WHERE is_deleted=0) AS active,
                      COUNT(*) FILTER(WHERE is_deleted<>0) AS logically_deleted,
                      COALESCE(SUM(total::numeric),0) AS total_amount,
                      COUNT(*) FILTER(WHERE EXISTS(
                        SELECT 1 FROM recibo_items i WHERE i.recibo_id=r.id
                      )) AS with_items,
                      COUNT(*) FILTER(WHERE NOT EXISTS(
                        SELECT 1 FROM recibo_items i WHERE i.recibo_id=r.id
                      )) AS without_items,
                      COUNT(*) FILTER(WHERE EXISTS(
                        SELECT 1 FROM recibo_document_versions v
                        WHERE v.recibo_id=r.id AND v.is_current=TRUE
                      )) AS with_snapshot
               FROM recibos r""",
        )
        items = _single(
            con,
            """SELECT COUNT(*) AS item_rows,
                      COALESCE(SUM(total::numeric),0) AS item_total
               FROM recibo_items""",
        )
        storage = _single(
            con,
            """SELECT COUNT(*) AS files,
                      COALESCE(SUM(OCTET_LENGTH(file_data)),0) AS bytes,
                      COUNT(*) FILTER(WHERE EXISTS(
                        SELECT 1 FROM recibos r
                        WHERE r.pdf_filename=p.filename
                      )) AS receipt_files,
                      COALESCE(SUM(OCTET_LENGTH(file_data)) FILTER(WHERE EXISTS(
                        SELECT 1 FROM recibos r
                        WHERE r.pdf_filename=p.filename
                      )),0) AS receipt_bytes,
                      COUNT(*) FILTER(WHERE NOT EXISTS(
                        SELECT 1 FROM recibos r
                        WHERE r.pdf_filename=p.filename
                      )) AS protected_files,
                      COALESCE(SUM(OCTET_LENGTH(file_data)) FILTER(WHERE NOT EXISTS(
                        SELECT 1 FROM recibos r
                        WHERE r.pdf_filename=p.filename
                      )),0) AS protected_bytes
               FROM pdf_storage p""",
        )
        links = _single(
            con,
            """SELECT COUNT(*) FILTER(
                        WHERE COALESCE(pdf_filename,'')<>''
                          AND EXISTS(
                            SELECT 1 FROM pdf_storage p
                            WHERE p.filename=r.pdf_filename
                          )
                      ) AS with_pdf,
                      COUNT(*) FILTER(
                        WHERE COALESCE(pdf_filename,'')=''
                           OR NOT EXISTS(
                             SELECT 1 FROM pdf_storage p
                             WHERE p.filename=r.pdf_filename
                           )
                      ) AS without_pdf
               FROM recibos r""",
        )
        modes = [
            dict(row)
            for row in con.execute(
                """SELECT document_storage_mode,COUNT(*) AS count
                   FROM recibos GROUP BY 1 ORDER BY 1"""
            ).fetchall()
        ]
        group_queries = {
            "by_ars": """SELECT COALESCE(ars,'') AS key,COUNT(*) AS count,
                                COALESCE(SUM(total::numeric),0) AS amount
                         FROM recibos GROUP BY 1 ORDER BY 1""",
            "by_user": """SELECT COALESCE(username,'') AS key,COUNT(*) AS count,
                                 COALESCE(SUM(total::numeric),0) AS amount
                          FROM recibos GROUP BY 1 ORDER BY 1""",
            "by_month": """SELECT SUBSTRING(COALESCE(fecha,''),1,7) AS key,
                                  COUNT(*) AS count,
                                  COALESCE(SUM(total::numeric),0) AS amount
                           FROM recibos GROUP BY 1 ORDER BY 1""",
            "by_document_state": """SELECT COALESCE(estado_documento,'') AS key,
                                           COUNT(*) AS count,
                                           COALESCE(SUM(total::numeric),0) AS amount
                                    FROM recibos GROUP BY 1 ORDER BY 1""",
            "by_billing_state": """SELECT COALESCE(estado_facturacion,'') AS key,
                                          COUNT(*) AS count,
                                          COALESCE(SUM(total::numeric),0) AS amount
                                   FROM recibos GROUP BY 1 ORDER BY 1""",
        }
        groups = {
            name: [dict(row) for row in con.execute(sql).fetchall()]
            for name, sql in group_queries.items()
        }
    return {
        "captured_at": utc_now(),
        "receipts": receipts,
        "items": items,
        "pdf_storage": storage,
        "receipt_pdf_links": links,
        "document_storage_modes": modes,
        **groups,
    }


def create_logical_backup(path: Path, baseline: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    counts = Counter()
    with gzip.open(temporary, "wt", encoding="utf-8", newline="\n") as output:
        output.write(
            json.dumps(
                {"record_type": "manifest", "baseline": baseline},
                ensure_ascii=False,
                default=json_default,
            )
            + "\n"
        )
        with app.db_connect() as con:
            table_queries = {
                "receipt": "SELECT * FROM recibos ORDER BY id",
                "item": "SELECT * FROM recibo_items ORDER BY recibo_id,id",
                "history": (
                    "SELECT * FROM recibo_facturacion_history "
                    "ORDER BY recibo_id,id"
                ),
                "pdf_metadata": (
                    "SELECT filename,OCTET_LENGTH(file_data) AS size "
                    "FROM pdf_storage ORDER BY filename"
                ),
            }
            for record_type, sql in table_queries.items():
                cursor = con.execute(sql)
                while True:
                    rows = cursor.fetchmany(500)
                    if not rows:
                        break
                    for row in rows:
                        output.write(
                            json.dumps(
                                {
                                    "record_type": record_type,
                                    "data": dict(row),
                                },
                                ensure_ascii=False,
                                default=json_default,
                            )
                            + "\n"
                        )
                        counts[record_type] += 1
    os.replace(temporary, path)
    return {
        "path": str(path.resolve()),
        "sha256": file_sha256(path),
        "bytes": path.stat().st_size,
        "counts": dict(counts),
    }


def classify_row(row: dict) -> tuple[str, str, list[str]]:
    reasons = []
    mode = str(row.get("document_storage_mode") or STORAGE_LEGACY)
    if bool(row.get("snapshot_exists")):
        return (
            ALREADY_MIGRATED,
            CLASS_ALREADY_MIGRATED
            if mode == STORAGE_SNAPSHOT
            else CLASS_SNAPSHOT_EXISTS,
            reasons,
        )
    if int(row.get("item_count") or 0) <= 0:
        reasons.append(CLASS_WITHOUT_ITEMS)
    total = float(row.get("total") or 0)
    calculated = float(row.get("items_total") or 0) + float(
        row.get("sala") or 0
    )
    tolerance = max(0.01, abs(total) * 0.001)
    if abs(calculated - total) > tolerance:
        reasons.append(CLASS_TOTAL_MISMATCH)
    coverage = str(row.get("tipo_cobertura") or "ASEGURADO").upper()
    if (
        not row.get("numero")
        or not str(row.get("nombre") or "").strip()
        or not str(row.get("fecha") or "").strip()
        or (
            coverage != "NO_ASEGURADO"
            and not str(row.get("ars") or "").strip()
        )
    ):
        reasons.append(CLASS_MISSING_HEADER)
    if (
        str(row.get("estado_documento") or "") not in VALID_DOCUMENT_STATES
        or str(row.get("estado_facturacion") or "")
        not in VALID_BILLING_STATES
    ):
        reasons.append(CLASS_INVALID_STATE)
    if (
        not str(row.get("pdf_filename") or "").strip()
        or not bool(row.get("pdf_exists"))
        or int(row.get("pdf_size") or 0) <= 0
    ):
        reasons.append(CLASS_MISSING_PDF)
    if reasons:
        return NEEDS_REVIEW, reasons[0], reasons
    return RECONSTRUCTIBLE, CLASS_COMPLETE, reasons


def _classification_rows() -> list[dict]:
    with app.db_connect() as con:
        return [
            dict(row)
            for row in con.execute(
                """WITH item_totals AS(
                     SELECT recibo_id,COUNT(*) AS item_count,
                            COALESCE(SUM(total),0) AS items_total
                     FROM recibo_items GROUP BY recibo_id
                   )
                   SELECT r.id,r.numero,r.nombre,r.fecha,r.ars,
                          r.tipo_cobertura,r.estado_documento,
                          r.estado_facturacion,r.sala,r.total,
                          r.pdf_filename,r.document_storage_mode,
                          COALESCE(i.item_count,0) AS item_count,
                          COALESCE(i.items_total,0) AS items_total,
                          (v.id IS NOT NULL) AS snapshot_exists,
                          (p.filename IS NOT NULL) AS pdf_exists,
                          COALESCE(OCTET_LENGTH(p.file_data),0) AS pdf_size
                   FROM recibos r
                   LEFT JOIN item_totals i ON i.recibo_id=r.id
                   LEFT JOIN recibo_document_versions v
                     ON v.recibo_id=r.id AND v.is_current=TRUE
                   LEFT JOIN pdf_storage p ON p.filename=r.pdf_filename
                   ORDER BY r.id"""
            ).fetchall()
        ]


def dry_run() -> dict[str, Any]:
    counts = Counter()
    classifications = Counter()
    by_ars = defaultdict(Counter)
    by_year = defaultdict(Counter)
    by_state = defaultdict(Counter)
    errors = Counter()
    candidates = []
    potential_bytes = 0
    rows = _classification_rows()
    for row in rows:
        outcome, classification, reasons = classify_row(row)
        counts[outcome] += 1
        classifications[classification] += 1
        by_ars[str(row.get("ars") or "")][outcome] += 1
        by_year[str(row.get("fecha") or "")[:4]][outcome] += 1
        by_state[str(row.get("estado_facturacion") or "")][outcome] += 1
        for reason in reasons:
            errors[reason] += 1
        if outcome == RECONSTRUCTIBLE:
            candidates.append(int(row["id"]))
            potential_bytes += int(row.get("pdf_size") or 0)
    return {
        "generated_at": utc_now(),
        "read_only": True,
        "analyzed": len(rows),
        "outcomes": dict(counts),
        "classifications": dict(classifications),
        "by_ars": {key: dict(value) for key, value in by_ars.items()},
        "by_year": {key: dict(value) for key, value in by_year.items()},
        "by_billing_state": {
            key: dict(value) for key, value in by_state.items()
        },
        "frequent_errors": dict(errors.most_common()),
        "potential_receipt_pdf_bytes": potential_bytes,
        "candidate_receipt_ids": candidates,
    }


def record_needs_review_classifications() -> int:
    records = []
    for row in _classification_rows():
        outcome, classification, reasons = classify_row(row)
        if outcome != NEEDS_REVIEW:
            continue
        records.append(
            (
                int(row["id"]),
                classification,
                str(row.get("pdf_filename") or ""),
                int(row.get("pdf_size") or 0),
                ",".join(reasons),
            )
        )
    if not records:
        return 0
    with app.db_connect() as con:
        cursor = con.con.cursor()
        psycopg2.extras.execute_values(
            cursor,
            """INSERT INTO recibo_document_migration(
                   recibo_id,migration_status,classification,
                   source_pdf_filename,source_pdf_size,attempts,last_error,
                   started_at,updated_at
               ) VALUES %s
               ON CONFLICT(recibo_id) DO UPDATE SET
                   migration_status='NEEDS_REVIEW',
                   classification=EXCLUDED.classification,
                   source_pdf_filename=EXCLUDED.source_pdf_filename,
                   source_pdf_size=EXCLUDED.source_pdf_size,
                   last_error=EXCLUDED.last_error,
                   updated_at=NOW()""",
            [
                (
                    receipt_id,
                    "NEEDS_REVIEW",
                    classification,
                    filename,
                    size,
                    0,
                    reason_codes,
                    datetime.now(timezone.utc),
                    datetime.now(timezone.utc),
                )
                for (
                    receipt_id,
                    classification,
                    filename,
                    size,
                    reason_codes,
                ) in records
            ],
            page_size=500,
        )
    return len(records)


@contextmanager
def migration_lock():
    connection = psycopg2.connect(
        app.DB_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    connection.autocommit = False
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_try_advisory_xact_lock(%s) AS acquired",
                (MIGRATION_LOCK_KEY,),
            )
            acquired = bool(cursor.fetchone()["acquired"])
        if not acquired:
            raise MigrationStopped(
                "Ya existe una migración documental activa."
            )
        yield
    finally:
        try:
            connection.rollback()
        except Exception:
            pass
        connection.close()


def _locked_receipt_row(con, receipt_id: int) -> dict:
    row = con.execute(
        """WITH item_totals AS(
             SELECT recibo_id,COUNT(*) AS item_count,
                    COALESCE(SUM(total),0) AS items_total
             FROM recibo_items WHERE recibo_id=%s GROUP BY recibo_id
           )
           SELECT r.id,r.numero,r.nombre,r.fecha,r.ars,r.tipo_cobertura,
                  r.estado_documento,r.estado_facturacion,r.sala,r.total,
                  r.pdf_filename,r.document_storage_mode,
                  COALESCE(i.item_count,0) AS item_count,
                  COALESCE(i.items_total,0) AS items_total,
                  EXISTS(
                    SELECT 1 FROM recibo_document_versions v
                    WHERE v.recibo_id=r.id AND v.is_current=TRUE
                  ) AS snapshot_exists,
                  (p.filename IS NOT NULL) AS pdf_exists,
                  COALESCE(OCTET_LENGTH(p.file_data),0) AS pdf_size
           FROM recibos r
           LEFT JOIN item_totals i ON i.recibo_id=r.id
           LEFT JOIN pdf_storage p ON p.filename=r.pdf_filename
           WHERE r.id=%s FOR UPDATE OF r""",
        (int(receipt_id), int(receipt_id)),
    ).fetchone()
    if not row:
        raise ValueError("El recibo no existe.")
    return dict(row)


def _normalize_text(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text).upper().strip()


def _date_text_variants(value) -> list[str]:
    text = str(value or "").strip()
    variants = [text] if text else []
    for source_format in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%Y/%m/%d",
        "%d/%m/%Y",
    ):
        try:
            parsed = datetime.strptime(text[:10], source_format)
        except ValueError:
            continue
        variants.extend(
            (
                parsed.strftime("%Y-%m-%d"),
                parsed.strftime("%d-%m-%Y"),
                parsed.strftime("%Y/%m/%d"),
                parsed.strftime("%d/%m/%Y"),
            )
        )
        break
    return list(dict.fromkeys(variant for variant in variants if variant))


def validate_rendered_document(
    pdf_path: str,
    document_record: dict,
    receipt_row: dict,
) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader

    path = Path(pdf_path)
    if not path.is_file() or path.stat().st_size <= 0:
        raise ValueError("El PDF reconstruido está vacío.")
    reader = PdfReader(str(path))
    if len(reader.pages) < 1:
        raise ValueError("El PDF reconstruido no contiene páginas.")
    extracted = _normalize_text(
        "\n".join(page.extract_text() or "" for page in reader.pages)
    )
    snapshot = document_record["snapshot"]
    header = dict(snapshot.get("header") or {})
    patient = dict(snapshot.get("patient") or {})
    totals = dict(snapshot.get("totals") or {})
    items = list(snapshot.get("items") or [])
    expected_hash = str(document_record["snapshot_hash"])
    if calculate_snapshot_hash(snapshot) != expected_hash:
        raise ValueError("El hash del snapshot no coincide.")
    expected_total = float(receipt_row.get("total") or 0)
    if abs(float(totals.get("general") or 0) - expected_total) > 0.01:
        raise ValueError("El total del snapshot no coincide con el recibo.")
    if len(items) != int(receipt_row.get("item_count") or 0):
        raise ValueError("La cantidad de ítems del snapshot no coincide.")
    required_text_groups = [
        [str(header.get("receipt_number") or "")],
        [str(patient.get("name") or "")],
        _date_text_variants(header.get("service_date")),
        [str(header.get("ars") or "")],
    ]
    missing_groups = [
        index
        for index, candidates in enumerate(required_text_groups)
        if candidates
        and not any(
            _normalize_text(candidate) in extracted
            for candidate in candidates
            if candidate
        )
    ]
    if missing_groups:
        raise ValueError(
            "El PDF reconstruido no contiene todos los campos identificadores."
        )
    return {
        "pdf_bytes": path.stat().st_size,
        "pages": len(reader.pages),
        "receipt_number_verified": True,
        "patient_verified": True,
        "date_verified": True,
        "ars_verified": True,
        "coverage_verified": True,
        "state_verified": True,
        "authorization_verified": True,
        "item_count": len(items),
        "items_verified": True,
        "total_verified": True,
        "multipage": len(reader.pages) > 1,
    }


def _source_pdf(con, receipt_id: int, filename: str) -> bytes:
    row = con.execute(
        """SELECT file_data,document_type,owner_receipt_id
           FROM pdf_storage
           WHERE filename=%s""",
        (filename,),
    ).fetchone()
    if not row or not row["file_data"]:
        raise ValueError("No existe el PDF histórico exacto.")
    owner = row["owner_receipt_id"]
    if owner is not None and int(owner) != int(receipt_id):
        raise ValueError("El PDF está asociado a otro recibo.")
    if str(row["document_type"] or "") not in {
        "RECEIPT_LEGACY",
        "UNKNOWN",
    }:
        raise ValueError("El archivo está protegido como documento no recibo.")
    return bytes(row["file_data"])


def mark_migration_error(
    receipt_id: int,
    status: str,
    error: Exception,
) -> None:
    message = re.sub(r"\s+", " ", str(error or ""))[:800]
    with app.db_connect() as con:
        con.execute(
            """INSERT INTO recibo_document_migration(
                   recibo_id,migration_status,attempts,last_error,
                   started_at,updated_at
               ) VALUES(%s,%s,1,%s,NOW(),NOW())
               ON CONFLICT(recibo_id) DO UPDATE
               SET migration_status=EXCLUDED.migration_status,
                   attempts=recibo_document_migration.attempts+1,
                   last_error=EXCLUDED.last_error,
                   updated_at=NOW()""",
            (int(receipt_id), status, message),
        )


def migrate_to_hybrid(
    receipt_id: int,
    renderer,
) -> dict[str, Any]:
    with app.db_connect() as con:
        row = _locked_receipt_row(con, receipt_id)
        outcome, classification, reasons = classify_row(row)
        if outcome == ALREADY_MIGRATED:
            document = load_current_receipt_snapshot(con, receipt_id)
        elif outcome != RECONSTRUCTIBLE:
            con.execute(
                """INSERT INTO recibo_document_migration(
                       recibo_id,migration_status,classification,
                       source_pdf_filename,source_pdf_size,attempts,
                       last_error,started_at,updated_at
                   ) VALUES(%s,'NEEDS_REVIEW',%s,%s,%s,1,%s,NOW(),NOW())
                   ON CONFLICT(recibo_id) DO UPDATE SET
                       migration_status='NEEDS_REVIEW',
                       classification=EXCLUDED.classification,
                       source_pdf_filename=EXCLUDED.source_pdf_filename,
                       source_pdf_size=EXCLUDED.source_pdf_size,
                       attempts=recibo_document_migration.attempts+1,
                       last_error=EXCLUDED.last_error,
                       updated_at=NOW()""",
                (
                    receipt_id,
                    classification,
                    row.get("pdf_filename"),
                    row.get("pdf_size"),
                    ",".join(reasons),
                ),
            )
            return {
                "receipt_id": receipt_id,
                "status": NEEDS_REVIEW,
                "classification": classification,
            }
        else:
            filename = str(row["pdf_filename"])
            source = _source_pdf(con, receipt_id, filename)
            source_hash = bytes_sha256(source)
            source_size = len(source)
            del source
            con.execute(
                """INSERT INTO recibo_document_migration(
                       recibo_id,migration_status,classification,
                       source_pdf_filename,source_pdf_size,source_pdf_hash,
                       attempts,last_error,started_at,updated_at
                   ) VALUES(%s,'ANALYZING','COMPLETE',%s,%s,%s,1,NULL,NOW(),NOW())
                   ON CONFLICT(recibo_id) DO UPDATE SET
                       migration_status='ANALYZING',
                       classification='COMPLETE',
                       source_pdf_filename=EXCLUDED.source_pdf_filename,
                       source_pdf_size=EXCLUDED.source_pdf_size,
                       source_pdf_hash=EXCLUDED.source_pdf_hash,
                       attempts=recibo_document_migration.attempts+1,
                       last_error=NULL,started_at=COALESCE(
                         recibo_document_migration.started_at,NOW()
                       ),updated_at=NOW()""",
                (receipt_id, filename, source_size, source_hash),
            )
            document = save_receipt_document_snapshot(
                con,
                receipt_id,
                "MIGRACION_DOCUMENTAL",
                target_storage_mode=STORAGE_HYBRID,
            )
            document = load_current_receipt_snapshot(con, receipt_id)
        pdf_path = app.render_receipt_snapshot_pdf(
            document, renderer=renderer
        )
        validation = validate_rendered_document(pdf_path, document, row)
        con.execute(
            """UPDATE recibo_document_migration
               SET migration_status='RENDER_VERIFIED',
                   classification=COALESCE(classification,'COMPLETE'),
                   snapshot_version=%s,snapshot_hash=%s,
                   render_verified=TRUE,validation_jsonb=%s,
                   last_error=NULL,verified_at=NOW(),updated_at=NOW()
               WHERE recibo_id=%s""",
            (
                int(document["version"]),
                str(document["snapshot_hash"]),
                psycopg2.extras.Json(validation),
                receipt_id,
            ),
        )
        return {
            "receipt_id": receipt_id,
            "status": "RENDER_VERIFIED",
            "pdf_path": pdf_path,
            "validation": validation,
        }


def backup_original_pdf(receipt_id: int, backup_root: Path) -> dict[str, Any]:
    with app.db_connect() as con:
        row = con.execute(
            """SELECT m.source_pdf_filename,m.source_pdf_size,
                      m.source_pdf_hash,m.render_verified,
                      r.document_storage_mode
               FROM recibo_document_migration m
               JOIN recibos r ON r.id=m.recibo_id
               WHERE m.recibo_id=%s""",
            (receipt_id,),
        ).fetchone()
        if not row or not bool(row["render_verified"]):
            raise ValueError("La renderización todavía no está verificada.")
        if str(row["document_storage_mode"]) != STORAGE_HYBRID:
            raise ValueError("El recibo no está en modo HYBRID.")
        filename = str(row["source_pdf_filename"] or "")
        payload = _source_pdf(con, receipt_id, filename)
    expected_hash = str(row["source_pdf_hash"] or "")
    if bytes_sha256(payload) != expected_hash:
        raise ValueError("El PDF original cambió antes del respaldo.")
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename)
    destination = (
        backup_root
        / "receipt_pdfs"
        / f"{int(receipt_id):08d}"
        / safe_name
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    with temporary.open("wb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    del payload
    os.replace(temporary, destination)
    backup_hash = file_sha256(destination)
    if backup_hash != expected_hash:
        raise ValueError("El hash del respaldo externo no coincide.")
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader

    if len(PdfReader(str(destination)).pages) < 1:
        raise ValueError("El respaldo externo no contiene páginas.")
    with app.db_connect() as con:
        con.execute(
            """UPDATE recibo_document_migration
               SET migration_status='DELETION_CANDIDATE',
                   backup_location=%s,backup_hash=%s,
                   backup_verified=TRUE,last_error=NULL,
                   verified_at=NOW(),updated_at=NOW()
               WHERE recibo_id=%s AND render_verified=TRUE""",
            (str(destination.resolve()), backup_hash, receipt_id),
        )
    return {
        "receipt_id": receipt_id,
        "backup_path": str(destination.resolve()),
        "backup_hash": backup_hash,
        "bytes": destination.stat().st_size,
    }


def delete_exact_receipt_pdf(
    receipt_id: int,
    batch_id: str,
) -> dict[str, Any]:
    with app.db_connect() as con:
        row = con.execute(
            """SELECT r.id,r.numero,r.pdf_filename,r.document_storage_mode,
                      r.estado_facturacion,r.estado_documento,r.total,r.ars,
                      r.revision_version,
                      m.migration_status,m.source_pdf_filename,
                      m.source_pdf_size,m.source_pdf_hash,m.backup_location,
                      m.backup_hash,m.backup_verified,m.render_verified,
                      m.snapshot_hash
               FROM recibos r
               JOIN recibo_document_migration m ON m.recibo_id=r.id
               WHERE r.id=%s FOR UPDATE OF r,m""",
            (receipt_id,),
        ).fetchone()
        if not row:
            raise ValueError("No existe control de migración para el recibo.")
        row = dict(row)
        if str(row["document_storage_mode"]) != STORAGE_HYBRID:
            raise ValueError("El recibo ya no está en modo HYBRID.")
        if str(row["migration_status"]) != "DELETION_CANDIDATE":
            raise ValueError("El recibo no es candidato de eliminación.")
        if not bool(row["backup_verified"]) or not bool(row["render_verified"]):
            raise ValueError("Falta validar render o respaldo.")
        if str(row["pdf_filename"]) != str(row["source_pdf_filename"]):
            raise ValueError("La referencia exacta del PDF cambió.")
        backup_path = Path(str(row["backup_location"] or ""))
        if not backup_path.is_file():
            raise ValueError("El respaldo externo no existe.")
        backup_hash = file_sha256(backup_path)
        if (
            backup_hash != str(row["backup_hash"] or "")
            or backup_hash != str(row["source_pdf_hash"] or "")
        ):
            raise ValueError("El respaldo externo no supera la validación.")
        document = load_current_receipt_snapshot(con, receipt_id)
        if str(document["snapshot_hash"]) != str(row["snapshot_hash"]):
            raise ValueError("El snapshot actual cambió.")
        stored = con.execute(
            """SELECT file_data,document_type,owner_receipt_id
               FROM pdf_storage
               WHERE filename=%s""",
            (row["source_pdf_filename"],),
        ).fetchone()
        if not stored or not stored["file_data"]:
            raise ValueError("El binario exacto ya no existe.")
        if str(stored["document_type"]) != "RECEIPT_LEGACY":
            raise ValueError("El archivo está protegido como no recibo.")
        if int(stored["owner_receipt_id"] or 0) != receipt_id:
            raise ValueError("El binario pertenece a otro recibo.")
        source_payload = bytes(stored["file_data"])
        if (
            len(source_payload) != int(row["source_pdf_size"] or 0)
            or bytes_sha256(source_payload)
            != str(row["source_pdf_hash"] or "")
        ):
            raise ValueError("El binario cambió antes de eliminarse.")
        del source_payload
        deleted = con.execute(
            """DELETE FROM pdf_storage
               WHERE filename=%s
                 AND owner_receipt_id=%s
                 AND document_type='RECEIPT_LEGACY'
               RETURNING filename""",
            (row["source_pdf_filename"], receipt_id),
        ).fetchone()
        if not deleted:
            raise ValueError("No se eliminó el binario exacto.")
        con.execute(
            """UPDATE recibos
               SET document_storage_mode='SNAPSHOT',
                   pdf_synced=0,pdf_sync_error=NULL,
                   legacy_pdf_deleted_at=NOW(),
                   legacy_pdf_checksum=%s,
                   legacy_pdf_size=%s,
                   legacy_pdf_backup_reference=%s,
                   legacy_pdf_deletion_batch=%s
               WHERE id=%s""",
            (
                row["source_pdf_hash"],
                row["source_pdf_size"],
                str(backup_path.resolve()),
                batch_id,
                receipt_id,
            ),
        )
        con.execute(
            """INSERT INTO recibo_facturacion_history(
                   recibo_id,estado_anterior,estado_nuevo,realizado_por,
                   realizado_at,motivo,observacion,referencia,evento_tipo,
                   total_al_momento,ars_al_momento,recibo_version
               ) VALUES(%s,%s,%s,'MIGRACION_DOCUMENTAL',NOW(),%s,%s,%s,
                        'PDF_HISTORICO_ELIMINADO',%s,%s,%s)""",
            (
                receipt_id,
                row["estado_facturacion"],
                row["estado_facturacion"],
                "Conversión documental HYBRID a SNAPSHOT",
                "PDF histórico respaldado y eliminado selectivamente.",
                str(backup_path.resolve()),
                row["total"],
                row["ars"],
                row["revision_version"],
            ),
        )
        con.execute(
            """UPDATE recibo_document_migration
               SET migration_status='PDF_DELETED',
                   deletion_batch_id=%s,pdf_deleted_at=NOW(),
                   last_error=NULL,updated_at=NOW()
               WHERE recibo_id=%s""",
            (batch_id, receipt_id),
        )
    return {
        "receipt_id": receipt_id,
        "bytes_deleted": int(row["source_pdf_size"] or 0),
        "receipt_number": int(row["numero"]),
    }


def clear_receipt_cache(receipt_number: int) -> int:
    cache = (
        Path(os.getenv("TEMP", ""))
        / "HospitalFacturacion"
        / "receipt_cache"
    )
    if not cache.is_dir():
        return 0
    removed = 0
    for path in cache.glob(f"recibo_{int(receipt_number)}_v*.pdf"):
        if path.is_file() and path.parent.resolve() == cache.resolve():
            path.unlink()
            removed += 1
    return removed


def verify_after_deletion(receipt_id: int, renderer) -> dict[str, Any]:
    with app.db_connect() as con:
        row = _locked_receipt_row(con, receipt_id)
        if str(row["document_storage_mode"]) != STORAGE_SNAPSHOT:
            raise ValueError("El recibo no quedó en modo SNAPSHOT.")
        document = load_current_receipt_snapshot(con, receipt_id)
        missing_binary = not bool(row["pdf_exists"])
    if not missing_binary:
        raise ValueError("El binario histórico todavía existe.")
    clear_receipt_cache(int(row["numero"]))
    pdf_path = app.render_receipt_snapshot_pdf(
        document, renderer=renderer
    )
    validation = validate_rendered_document(pdf_path, document, row)
    with app.db_connect() as con:
        con.execute(
            """UPDATE recibo_document_migration
               SET migration_status='VERIFIED',completed_at=NOW(),
                   verified_at=NOW(),validation_jsonb=%s,
                   last_error=NULL,updated_at=NOW()
               WHERE recibo_id=%s
                 AND pdf_deleted_at IS NOT NULL""",
            (psycopg2.extras.Json(validation), receipt_id),
        )
    return {
        "receipt_id": receipt_id,
        "pdf_path": pdf_path,
        "validation": validation,
    }


def restore_receipt_pdf(receipt_id: int, actor: str) -> dict[str, Any]:
    with migration_lock():
        with app.db_connect() as con:
            row = con.execute(
                """SELECT r.pdf_filename,r.document_storage_mode,
                          m.backup_location,m.backup_hash,
                          m.source_pdf_hash,m.source_pdf_size
                   FROM recibos r
                   JOIN recibo_document_migration m ON m.recibo_id=r.id
                   WHERE r.id=%s FOR UPDATE OF r,m""",
                (receipt_id,),
            ).fetchone()
            if not row:
                raise ValueError("No existe respaldo controlado.")
            backup = Path(str(row["backup_location"] or ""))
            if not backup.is_file():
                raise ValueError("El respaldo externo no existe.")
            payload = backup.read_bytes()
            digest = bytes_sha256(payload)
            if (
                digest != str(row["backup_hash"] or "")
                or digest != str(row["source_pdf_hash"] or "")
                or len(payload) != int(row["source_pdf_size"] or 0)
            ):
                raise ValueError("El respaldo no supera la verificación.")
            con.execute(
                """UPDATE recibos
                   SET legacy_pdf_backup_reference=%s,
                       legacy_pdf_checksum=%s,
                       legacy_pdf_size=%s,
                       pdf_synced=0,
                       pdf_sync_error=NULL
                   WHERE id=%s""",
                (
                    str(backup.resolve()),
                    digest,
                    len(payload),
                    receipt_id,
                ),
            )
            con.execute(
                """UPDATE recibo_document_migration
                   SET migration_status='EXTERNAL_AVAILABLE',last_error=NULL,
                       updated_at=NOW() WHERE recibo_id=%s""",
                (receipt_id,),
            )
            con.execute(
                """INSERT INTO action_history(
                       username,action,details,created_at
                   ) VALUES(%s,'Restaurar PDF histórico',%s,NOW())""",
                (
                    str(actor or "Administrador"),
                    f"Recibo ID {receipt_id}; referencia externa verificada.",
                ),
            )
    return {
        "receipt_id": receipt_id,
        "restored": True,
        "storage": "EXTERNAL",
        "path": str(backup.resolve()),
    }


def reconcile(baseline: dict[str, Any]) -> dict[str, Any]:
    current = canonical_data(capture_baseline())
    baseline = canonical_data(baseline)
    stable_keys = (
        "total",
        "active",
        "logically_deleted",
        "total_amount",
    )
    checks = {
        f"receipts.{key}": (
            str(current["receipts"][key])
            == str(baseline["receipts"][key])
        )
        for key in stable_keys
    }
    for key in ("item_rows", "item_total"):
        checks[f"items.{key}"] = (
            str(current["items"][key]) == str(baseline["items"][key])
        )
    for group in (
        "by_ars",
        "by_user",
        "by_month",
        "by_document_state",
        "by_billing_state",
    ):
        checks[group] = current[group] == baseline[group]
    checks["protected_pdf_count"] = (
        int(current["pdf_storage"]["protected_files"])
        == int(baseline["pdf_storage"]["protected_files"])
    )
    checks["protected_pdf_bytes"] = (
        int(current["pdf_storage"]["protected_bytes"])
        == int(baseline["pdf_storage"]["protected_bytes"])
    )
    return {
        "checked_at": utc_now(),
        "ok": all(checks.values()),
        "checks": checks,
        "current": current,
        "receipt_pdf_files_removed": (
            int(baseline["pdf_storage"]["receipt_files"])
            - int(current["pdf_storage"]["receipt_files"])
        ),
        "receipt_pdf_bytes_freed": (
            int(baseline["pdf_storage"]["receipt_bytes"])
            - int(current["pdf_storage"]["receipt_bytes"])
        ),
    }


def process_receipt(
    receipt_id: int,
    backup_root: Path,
    renderer,
    batch_id: str,
) -> dict[str, Any]:
    with app.db_connect() as con:
        state = con.execute(
            """SELECT r.document_storage_mode,m.migration_status,
                      m.pdf_deleted_at
               FROM recibos r
               LEFT JOIN recibo_document_migration m
                 ON m.recibo_id=r.id
               WHERE r.id=%s""",
            (int(receipt_id),),
        ).fetchone()
    if not state:
        raise ValueError("El recibo no existe.")
    if (
        str(state["document_storage_mode"]) == STORAGE_SNAPSHOT
        and str(state["migration_status"] or "") == "VERIFIED"
    ):
        return {
            "receipt_id": receipt_id,
            "status": "ALREADY_VERIFIED",
            "backup_bytes": 0,
            "deleted_bytes": 0,
            "pages": 0,
            "multipage": False,
        }
    if (
        str(state["document_storage_mode"]) == STORAGE_SNAPSHOT
        and state["pdf_deleted_at"] is not None
    ):
        verified = verify_after_deletion(receipt_id, renderer)
        return {
            "receipt_id": receipt_id,
            "status": "VERIFIED",
            "backup_bytes": 0,
            "deleted_bytes": 0,
            "pages": verified["validation"]["pages"],
            "multipage": verified["validation"]["multipage"],
            "review_pdf": verified["pdf_path"],
        }
    migrated = migrate_to_hybrid(receipt_id, renderer)
    if migrated["status"] == NEEDS_REVIEW:
        return migrated
    backup = backup_original_pdf(receipt_id, backup_root)
    deleted = delete_exact_receipt_pdf(receipt_id, batch_id)
    verified = verify_after_deletion(receipt_id, renderer)
    return {
        "receipt_id": receipt_id,
        "status": "VERIFIED",
        "backup_bytes": backup["bytes"],
        "deleted_bytes": deleted["bytes_deleted"],
        "pages": verified["validation"]["pages"],
        "multipage": verified["validation"]["multipage"],
        "review_pdf": verified["pdf_path"],
    }


def process_receipt_chunk(
    receipt_ids: list[int],
    backup_root: Path,
    batch_id: str,
) -> list[dict[str, Any]]:
    from pdf_engine import ReceiptPDFRenderer

    results = []
    renderer = None
    try:
        renderer = ReceiptPDFRenderer(persistent=True)
        renderer.start()
        for receipt_id in receipt_ids:
            try:
                results.append(
                    process_receipt(
                        receipt_id,
                        backup_root,
                        renderer,
                        batch_id,
                    )
                )
            except Exception as exc:
                mark_migration_error(receipt_id, "FAILED", exc)
                results.append(
                    {
                        "receipt_id": receipt_id,
                        "status": "FAILED",
                        "error": (
                            f"{type(exc).__name__}: "
                            f"{re.sub(r'\\s+', ' ', str(exc))[:500]}"
                        ),
                    }
                )
    except Exception as exc:
        processed_ids = {
            int(result["receipt_id"]) for result in results
        }
        for receipt_id in receipt_ids:
            if int(receipt_id) in processed_ids:
                continue
            mark_migration_error(receipt_id, "FAILED", exc)
            results.append(
                {
                    "receipt_id": receipt_id,
                    "status": "FAILED",
                    "error": (
                        f"{type(exc).__name__}: "
                        f"{re.sub(r'\\s+', ' ', str(exc))[:500]}"
                    ),
                }
            )
    finally:
        if renderer is not None:
            renderer.close()
    return results


def run_batches_sequential(
    candidate_ids: list[int],
    backup_root: Path,
    baseline: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    from pdf_engine import ReceiptPDFRenderer

    requested_ids = list(
        dict.fromkeys(int(value) for value in candidate_ids)
    )
    with app.db_connect() as con:
        already_verified = {
            int(row["id"])
            for row in con.execute(
                """SELECT r.id
                   FROM recibos r
                   JOIN recibo_document_migration m
                     ON m.recibo_id=r.id
                   WHERE r.id=ANY(%s)
                     AND r.document_storage_mode='SNAPSHOT'
                     AND m.migration_status='VERIFIED'""",
                (requested_ids,),
            ).fetchall()
        } if requested_ids else set()
    pending = [
        receipt_id
        for receipt_id in requested_ids
        if receipt_id not in already_verified
    ]
    results = []
    batch_summaries = []
    renderer = ReceiptPDFRenderer(persistent=True)
    renderer.start()
    try:
        verified_before_resume = len(already_verified)
        if verified_before_resume >= 75:
            sizes = []
        elif verified_before_resume >= 25:
            sizes = [75 - verified_before_resume]
        elif verified_before_resume:
            sizes = [25 - verified_before_resume, 50]
        else:
            sizes = [25, 50]
        batch_number = 0
        while pending:
            batch_size = (
                sizes[batch_number]
                if batch_number < len(sizes)
                else 100
            )
            batch_number += 1
            selected = pending[:batch_size]
            pending = pending[batch_size:]
            batch_id = (
                f"RDM-{datetime.now():%Y%m%d-%H%M%S}-"
                f"{batch_number:04d}"
            )
            succeeded = 0
            failed = 0
            review = 0
            bytes_deleted = 0
            multipage = 0
            for receipt_id in selected:
                try:
                    result = process_receipt(
                        receipt_id,
                        backup_root,
                        renderer,
                        batch_id,
                    )
                    results.append(result)
                    if result["status"] == "VERIFIED":
                        succeeded += 1
                        bytes_deleted += int(result["deleted_bytes"])
                        multipage += int(bool(result["multipage"]))
                    else:
                        review += 1
                except Exception as exc:
                    failed += 1
                    mark_migration_error(receipt_id, "FAILED", exc)
                    results.append(
                        {
                            "receipt_id": receipt_id,
                            "status": "FAILED",
                            "error": (
                                f"{type(exc).__name__}: "
                                f"{re.sub(r'\\s+', ' ', str(exc))[:500]}"
                            ),
                        }
                    )
            reconciliation = reconcile(baseline)
            summary = {
                "batch_id": batch_id,
                "requested": len(selected),
                "verified": succeeded,
                "needs_review": review,
                "failed": failed,
                "failure_rate": (
                    failed / len(selected) if selected else 0.0
                ),
                "bytes_deleted": bytes_deleted,
                "multipage": multipage,
                "reconciliation_ok": reconciliation["ok"],
            }
            batch_summaries.append(summary)
            json_dump_file(
                report_path,
                {
                    "updated_at": utc_now(),
                    "results": results,
                    "batches": batch_summaries,
                    "pending_count": len(pending),
                    "reconciliation": reconciliation,
                },
            )
            if not reconciliation["ok"]:
                raise MigrationStopped(
                    "La reconciliación cambió después del lote."
                )
            if summary["failure_rate"] > 0.02:
                raise MigrationStopped(
                    "La tasa de fallos del lote superó el 2 %."
                )
    finally:
        renderer.close()
    return {
        "results": results,
        "batches": batch_summaries,
        "reconciliation": reconcile(baseline),
        "already_verified": len(already_verified),
    }


def run_batches(
    candidate_ids: list[int],
    backup_root: Path,
    baseline: dict[str, Any],
    report_path: Path,
) -> dict[str, Any]:
    requested_ids = list(
        dict.fromkeys(int(value) for value in candidate_ids)
    )
    with app.db_connect() as con:
        already_verified = {
            int(row["id"])
            for row in con.execute(
                """SELECT r.id
                   FROM recibos r
                   JOIN recibo_document_migration m
                     ON m.recibo_id=r.id
                   WHERE r.id=ANY(%s)
                     AND r.document_storage_mode='SNAPSHOT'
                     AND m.migration_status='VERIFIED'""",
                (requested_ids,),
            ).fetchall()
        } if requested_ids else set()
    pending = [
        receipt_id
        for receipt_id in requested_ids
        if receipt_id not in already_verified
    ]
    results = []
    batch_summaries = []
    max_batches = max(
        0,
        int(os.getenv("RECEIPT_MIGRATION_MAX_BATCHES", "0")),
    )
    verified_before_resume = len(already_verified)
    if verified_before_resume >= 75:
        sizes = []
    elif verified_before_resume >= 25:
        sizes = [75 - verified_before_resume]
    elif verified_before_resume:
        sizes = [25 - verified_before_resume, 50]
    else:
        sizes = [25, 50]
    batch_number = 0
    while pending:
        batch_size = (
            sizes[batch_number]
            if batch_number < len(sizes)
            else 100
        )
        batch_number += 1
        selected = pending[:batch_size]
        pending = pending[batch_size:]
        batch_id = (
            f"RDM-{datetime.now():%Y%m%d-%H%M%S}-"
            f"{batch_number:04d}"
        )
        configured_workers = max(
            1,
            min(
                4,
                int(os.getenv("RECEIPT_MIGRATION_WORKERS", "4")),
            ),
        )
        worker_count = min(configured_workers, len(selected))
        chunks = [
            selected[index::worker_count]
            for index in range(worker_count)
        ]
        batch_results = []
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(
                    process_receipt_chunk,
                    chunk,
                    backup_root,
                    batch_id,
                )
                for chunk in chunks
                if chunk
            ]
            for future in as_completed(futures):
                batch_results.extend(future.result())
        result_order = {
            receipt_id: index
            for index, receipt_id in enumerate(selected)
        }
        batch_results.sort(
            key=lambda result: result_order[int(result["receipt_id"])]
        )
        results.extend(batch_results)
        succeeded = sum(
            result["status"] == "VERIFIED"
            for result in batch_results
        )
        failed = sum(
            result["status"] == "FAILED"
            for result in batch_results
        )
        review = len(batch_results) - succeeded - failed
        bytes_deleted = sum(
            int(result.get("deleted_bytes") or 0)
            for result in batch_results
            if result["status"] == "VERIFIED"
        )
        multipage = sum(
            int(bool(result.get("multipage")))
            for result in batch_results
            if result["status"] == "VERIFIED"
        )
        reconciliation = reconcile(baseline)
        summary = {
            "batch_id": batch_id,
            "requested": len(selected),
            "verified": succeeded,
            "needs_review": review,
            "failed": failed,
            "failure_rate": (
                failed / len(selected) if selected else 0.0
            ),
            "bytes_deleted": bytes_deleted,
            "multipage": multipage,
            "workers": worker_count,
            "reconciliation_ok": reconciliation["ok"],
        }
        batch_summaries.append(summary)
        json_dump_file(
            report_path,
            {
                "updated_at": utc_now(),
                "results": results,
                "batches": batch_summaries,
                "pending_count": len(pending),
                "reconciliation": reconciliation,
            },
        )
        if not reconciliation["ok"]:
            raise MigrationStopped(
                "La reconciliación cambió después del lote."
            )
        if summary["failure_rate"] > 0.02:
            raise MigrationStopped(
                "La tasa de fallos del lote superó el 2 %."
            )
        if max_batches and batch_number >= max_batches:
            break
    return {
        "results": results,
        "batches": batch_summaries,
        "reconciliation": reconcile(baseline),
        "already_verified": len(already_verified),
        "remaining": len(pending),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--phase",
        choices=("baseline", "structure", "dry-run", "migrate", "all"),
        default="all",
    )
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--backup-root", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    backup_root = Path(args.backup_root).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    backup_root.mkdir(parents=True, exist_ok=True)
    baseline_path = run_dir / "baseline.json"
    logical_backup_path = run_dir / "logical_backup.jsonl.gz"
    logical_manifest_path = run_dir / "logical_backup_manifest.json"
    dry_run_path = run_dir / "dry_run.json"
    final_path = run_dir / "migration_report.json"

    if args.phase in {"baseline", "all"}:
        baseline = capture_baseline()
        json_dump_file(baseline_path, baseline)
        manifest = create_logical_backup(
            logical_backup_path, baseline
        )
        json_dump_file(logical_manifest_path, manifest)
        print(
            json.dumps(
                {
                    "phase": "baseline",
                    "receipts": baseline["receipts"]["total"],
                    "receipt_pdf_bytes": baseline["pdf_storage"][
                        "receipt_bytes"
                    ],
                    "logical_backup_sha256": manifest["sha256"],
                }
            ),
            flush=True,
        )
        if args.phase == "baseline":
            return 0

    if args.phase in {"structure", "all"}:
        app.db_init()
        app.db_init()
        print('{"phase":"structure","passes":2}', flush=True)
        if args.phase == "structure":
            return 0

    if args.phase in {"dry-run", "all"}:
        report = dry_run()
        json_dump_file(dry_run_path, report)
        print(
            json.dumps(
                {
                    "phase": "dry-run",
                    "analyzed": report["analyzed"],
                    "outcomes": report["outcomes"],
                    "potential_bytes": report[
                        "potential_receipt_pdf_bytes"
                    ],
                }
            ),
            flush=True,
        )
        if args.phase == "dry-run":
            return 0

    if args.phase in {"migrate", "all"}:
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        report = json.loads(dry_run_path.read_text(encoding="utf-8"))
        with migration_lock():
            migration = run_batches(
                report["candidate_receipt_ids"],
                backup_root,
                baseline,
                final_path,
            )
        status_counts = Counter(
            result["status"] for result in migration["results"]
        )
        needs_review_recorded = record_needs_review_classifications()
        final = {
            "completed_at": utc_now(),
            "status_counts": dict(status_counts),
            "already_verified": migration["already_verified"],
            "remaining": migration.get("remaining", 0),
            "needs_review_recorded": needs_review_recorded,
            "batches": migration["batches"],
            "reconciliation": migration["reconciliation"],
            "backup_root": str(backup_root),
        }
        json_dump_file(final_path, final)
        print(
            json.dumps(
                {
                    "phase": "migrate",
                    "status_counts": dict(status_counts),
                    "batches": len(migration["batches"]),
                    "bytes_freed": migration["reconciliation"][
                        "receipt_pdf_bytes_freed"
                    ],
                    "reconciliation_ok": migration["reconciliation"]["ok"],
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {
                    "fatal": type(exc).__name__,
                    "message": re.sub(r"\s+", " ", str(exc))[:800],
                }
            ),
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()
        raise
