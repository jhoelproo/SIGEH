"""Shared receipt identity and authorization, without rewriting issued lists."""

import json
from datetime import datetime, timedelta, timezone


EDITABLE_BATCHES = ("PENDIENTE", "BORRADOR")
HOSPITAL_TIMEZONE = timezone(timedelta(hours=-4))


def normalized_document_type(value):
    kind = str(value or "").strip().upper()
    if kind not in {"NSS", "CÉDULA", "CEDULA"}:
        raise ValueError("Selecciona NSS o CÉDULA.")
    return "NSS" if kind == "NSS" else "CÉDULA"


def required_list_text(value, maximum, missing, too_long):
    text = str(value or "").strip()
    if not text:
        raise ValueError(missing)
    if len(text) > maximum:
        raise ValueError(too_long)
    return text


def normalize_list_metadata(kind, number, authorization, specialty):
    return (
        normalized_document_type(kind),
        required_list_text(
            number,
            24,
            "Escribe el NSS o la cédula.",
            "El NSS o la cédula no puede exceder 24 caracteres.",
        ),
        required_list_text(
            authorization,
            40,
            "Escribe el número de autorización.",
            "La autorización no puede exceder 40 caracteres.",
        ),
        str(specialty or "").strip() or "EMERGENCIOLOGÍA",
    )


def validate_authorization(value):
    value = str(value or "").strip()
    if not value.isascii() or not value.isdigit() or not 4 <= len(value) <= 40:
        raise ValueError("La autorización debe contener entre 4 y 40 dígitos.")
    return value


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return stamp.replace(tzinfo=stamp.tzinfo or HOSPITAL_TIMEZONE)


def _current_value(entry, field, current, current_stamp):
    saved = str(entry.get(field) or "")
    value = str(entry.get(current) or "")
    latest = entry.get(current_stamp)
    if _timestamp(entry.get("last_edited_at")) > _timestamp(latest) and saved:
        return saved
    return entry.get(current) if value or latest else entry.get(field)


def effective_list_entry(row):
    """Reconcile old copies on read; only explicit receipt IDs are used."""
    entry = dict(row)
    if entry.get("batch_status") not in EDITABLE_BATCHES or not entry.get("recibo_id"):
        return entry
    for field, current in (
        ("nss_snapshot", "receipt_nss"),
        ("cedula_snapshot", "receipt_cedula"),
        ("specialty_snapshot", "receipt_specialty"),
    ):
        entry[field] = _current_value(entry, field, current, "receipt_edited_at")
    entry["authorization_snapshot"] = _current_value(
        entry,
        "authorization_snapshot",
        "receipt_authorization",
        "receipt_authorization_at",
    )
    kind = str(entry.get("document_type_snapshot") or "NSS")
    document = (
        entry["cedula_snapshot"]
        if kind in {"CÉDULA", "CEDULA"}
        else entry["nss_snapshot"]
    )
    entry["document_number_snapshot"] = document or entry.get(
        "document_number_snapshot"
    )
    return entry


def lock_receipt(connection, receipt_id):
    row = connection.execute(
        """SELECT id,numero,nombre,estado_facturacion,revision_version,total,ars,
                  tipo_cobertura,numero_autorizacion,admission_nss_snapshot,
                  admission_cedula_snapshot,specialty_snapshot,verification_bypassed,
                  admission_atencion_id,estado_documento,review_status,review_reason
           FROM recibos WHERE id=%s AND COALESCE(is_deleted,0)=0 FOR UPDATE""",
        (int(receipt_id),),
    ).fetchone()
    if not row:
        raise ValueError("El recibo no existe o fue retirado.")
    return dict(row)


def lock_pending_batches(connection, receipt_id):
    connection.execute(
        """SELECT b.id FROM billing_batches b
           JOIN billing_batch_receipts br ON br.batch_id=b.id
           WHERE br.recibo_id=%s AND br.included=1
             AND b.status IN ('PENDIENTE','BORRADOR')
           ORDER BY b.id FOR UPDATE OF b""",
        (int(receipt_id),),
    ).fetchall()


def restore_legacy_list_identity(connection, receipt_id, current):
    """Promote a newer legacy list correction before editing its receipt."""
    lock_pending_batches(connection, receipt_id)
    row = connection.execute(
        """SELECT br.recibo_id,b.status AS batch_status,br.last_edited_at,
                  br.nss_snapshot,br.cedula_snapshot,br.specialty_snapshot,
                  r.admission_nss_snapshot AS receipt_nss,
                  r.admission_cedula_snapshot AS receipt_cedula,
                  r.specialty_snapshot AS receipt_specialty,
                  v.created_at AS receipt_edited_at
           FROM billing_batch_receipts br
           JOIN billing_batches b ON b.id=br.batch_id
           JOIN recibos r ON r.id=br.recibo_id
           LEFT JOIN recibo_document_versions v ON v.recibo_id=r.id AND v.is_current=TRUE
           WHERE br.recibo_id=%s AND br.included=1
             AND b.status IN ('PENDIENTE','BORRADOR')
           ORDER BY br.last_edited_at DESC NULLS LAST,br.batch_id DESC LIMIT 1""",
        (int(receipt_id),),
    ).fetchone()
    if not row:
        return dict(current)
    effective = effective_list_entry(row)
    fields = {
        "admission_nss_snapshot": effective["nss_snapshot"],
        "admission_cedula_snapshot": effective["cedula_snapshot"],
        "specialty_snapshot": effective["specialty_snapshot"],
    }
    connection.execute(
        """UPDATE recibos SET admission_nss_snapshot=%s,admission_cedula_snapshot=%s,
               specialty_snapshot=%s WHERE id=%s""",
        (*fields.values(), int(receipt_id)),
    )
    return {**current, **fields}


def sync_pending_lists(connection, receipt_id, actor, stamp, document_type=None):
    lock_pending_batches(connection, receipt_id)
    rows = connection.execute(
        """UPDATE billing_batch_receipts br SET
               nss_snapshot=r.admission_nss_snapshot,
               cedula_snapshot=r.admission_cedula_snapshot,
               authorization_snapshot=r.numero_autorizacion,
               specialty_snapshot=r.specialty_snapshot,
               document_type_snapshot=COALESCE(%s,br.document_type_snapshot,'NSS'),
               document_number_snapshot=CASE
                   WHEN COALESCE(%s,br.document_type_snapshot,'NSS') IN ('CÉDULA','CEDULA')
                   THEN r.admission_cedula_snapshot ELSE r.admission_nss_snapshot END,
               last_edited_at=%s,last_edited_by=%s
           FROM recibos r,billing_batches b
           WHERE r.id=%s AND br.recibo_id=r.id AND br.batch_id=b.id
             AND br.included=1 AND b.status IN ('PENDIENTE','BORRADOR')
           RETURNING br.batch_id""",
        (document_type, document_type, stamp, actor, int(receipt_id)),
    ).fetchall()
    for row in rows:
        connection.execute(
            """INSERT INTO billing_batch_events(batch_id,recibo_id,event_type,
               performed_at,performed_by,details)
               VALUES(%s,%s,'DATOS_RECIBO_SINCRONIZADOS',%s,%s,%s)""",
            (
                row["batch_id"],
                receipt_id,
                stamp,
                actor,
                "NSS, identificación y autorización",
            ),
        )
    if rows:
        connection.execute(
            "UPDATE billing_batches SET updated_at=%s,updated_by=%s WHERE id=ANY(%s)",
            (stamp, actor, [row["batch_id"] for row in rows]),
        )


def write_receipt_metadata(connection, current, changes, actor, stamp, policy):
    """The caller owns the transaction and document version creation."""
    values = {
        key: changes.get(key, current.get(key))
        for key in (
            "numero_autorizacion",
            "admission_nss_snapshot",
            "admission_cedula_snapshot",
            "specialty_snapshot",
        )
    }
    state, review, reason = policy(current, values["numero_autorizacion"])
    connection.execute(
        """UPDATE recibos SET numero_autorizacion=%s,admission_nss_snapshot=%s,
               admission_cedula_snapshot=%s,specialty_snapshot=%s,
               autorizacion_at=CASE WHEN numero_autorizacion IS DISTINCT FROM %s
                   THEN %s ELSE autorizacion_at END,
               autorizacion_por=CASE WHEN numero_autorizacion IS DISTINCT FROM %s
                   THEN %s ELSE autorizacion_por END,
               estado_documento=%s,review_status=%s,review_reason=%s,
               revision_version=revision_version+1
           WHERE id=%s""",
        (
            *values.values(),
            values["numero_autorizacion"],
            stamp,
            values["numero_autorizacion"],
            actor,
            state,
            review,
            reason,
            current["id"],
        ),
    )
    connection.execute(
        """INSERT INTO recibo_facturacion_history(recibo_id,estado_anterior,
               estado_nuevo,realizado_por,realizado_at,evento_tipo,observacion,
               total_al_momento,ars_al_momento,recibo_version)
           VALUES(%s,%s,%s,%s,%s,'CORRECCION_DATOS_RECIBO',%s,%s,%s,%s)""",
        (
            current["id"],
            current["estado_facturacion"],
            current["estado_facturacion"],
            actor,
            stamp,
            json.dumps(
                {
                    "previous": {key: current.get(key) for key in values},
                    "current": values,
                },
                ensure_ascii=False,
            ),
            current["total"],
            current["ars"],
            int(current["revision_version"] or 0) + 1,
        ),
    )
