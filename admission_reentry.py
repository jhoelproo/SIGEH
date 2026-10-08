"""Materialize authorized reentries using the original attention's global UUID."""

from uuid import UUID


def reentry_payload(connection, attention):
    is_reentry = int(attention.get("es_reingreso") or 0)
    origin = None
    if is_reentry:
        original = connection.execute(
            "SELECT global_attention_id FROM atenciones WHERE id=?",
            (attention.get("atencion_origen_id"),),
        ).fetchone()
        origin = str(original[0]) if original else None
    return {
        "is_reentry": is_reentry,
        "reentry_origin_global_attention_id": origin,
        "reentry_reason": attention.get("motivo_reingreso"),
        "reentry_authorized_by": attention.get("autorizado_por"),
    }


def _reentry_snapshot(payload):
    if "is_reentry" not in payload:
        return None
    value = int(payload["is_reentry"] or 0)
    if value not in (0, 1):
        raise ValueError("El estado de reingreso sincronizado no es válido.")
    if not value:
        return {}
    origin = str(UUID(str(payload.get("reentry_origin_global_attention_id") or "")))
    reason = str(payload.get("reentry_reason") or "").strip()
    actor = str(payload.get("reentry_authorized_by") or "").strip()
    if len(reason) < 8 or not actor:
        raise ValueError("El reingreso sincronizado requiere motivo y autorización.")
    return {"origin_uuid": origin, "reason": reason, "actor": actor}


def local_reentry_fields(connection, payload, patient_id, clinical_day_id):
    snapshot = _reentry_snapshot(payload)
    if snapshot is None:
        return {}
    if not snapshot:
        return {"es_reingreso": 0}
    origin = connection.execute(
        """SELECT a.id,a.paciente_id,a.dia_operativo_id
           FROM atenciones a
           WHERE REPLACE(LOWER(a.global_attention_id),'-','')=REPLACE(LOWER(?),'-','')
              OR a.id=(SELECT local_attention_id FROM sync_attention_aliases
                       WHERE REPLACE(LOWER(remote_global_attention_id),'-','')=REPLACE(LOWER(?),'-',''))
           LIMIT 1""",
        (snapshot["origin_uuid"], snapshot["origin_uuid"]),
    ).fetchone()
    if not origin:
        raise ValueError(
            "La atención original del reingreso todavía no se ha sincronizado."
        )
    if int(origin[1]) != int(patient_id) or int(origin[2]) != int(clinical_day_id):
        raise ValueError(
            "El reingreso no corresponde al paciente y día de la atención original."
        )
    return {
        "es_reingreso": 1,
        "atencion_origen_id": int(origin[0]),
        "motivo_reingreso": snapshot["reason"],
        "autorizado_por": snapshot["actor"],
    }
