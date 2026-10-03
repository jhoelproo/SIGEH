"""Administrative linking without changing a receipt's financial history."""

import re
from admission_contract import COVERAGE_UNINSURED_DECLARED


def _normalized(value):
    return " ".join(str(value or "").upper().split())


def validate_unlinked_receipt(receipt):
    if not receipt:
        raise ValueError("El recibo ya no existe.")
    if receipt.get("is_deleted") or any(
        _normalized(receipt.get(field))
        in {"ANULADO", "CANCELADO", "INVALIDO", "CANCELLED"}
        for field in ("estado_documento", "estado_facturacion")
    ):
        raise ValueError("El recibo está anulado o eliminado.")
    if any(
        receipt.get(key)
        for key in (
            "admission_atencion_id",
            "admission_global_attention_id",
            "admission_paciente_id",
        )
    ):
        raise ValueError("El recibo ya tiene un vínculo. Actualiza el historial.")


def _require_matching_field(original, target, message):
    normalized = _normalized(original)
    if not normalized or normalized != _normalized(target):
        raise ValueError(message)


def validate_matching_patient(receipt, attention):
    _require_matching_field(
        receipt.get("nombre"),
        attention.get("patient_name"),
        "El nombre del paciente no coincide con la atención.",
    )
    uninsured = _normalized(receipt.get("tipo_cobertura")) == "NO_ASEGURADO"
    if uninsured != (
        _normalized(attention.get("coverage_status")) == COVERAGE_UNINSURED_DECLARED
    ):
        raise ValueError("La cobertura del recibo no coincide con la atención.")
    if not uninsured:
        _require_matching_field(
            receipt.get("ars"),
            attention.get("canonical_ars"),
            "El seguro del recibo no coincide con la atención.",
        )
    for receipt_key, attention_key in (
        ("admission_nss_snapshot", "nss_snapshot"),
        ("admission_cedula_snapshot", "cedula_snapshot"),
    ):
        original = re.sub(r"\D", "", str(receipt.get(receipt_key) or ""))
        target = re.sub(r"\D", "", str(attention.get(attention_key) or ""))
        if original and target and original != target:
            raise ValueError("Los identificadores pertenecen a pacientes diferentes.")


def _lock_attention(con, selection, backend, actor, session_id):
    source = str(selection["source_instance_id"])
    identity = int(selection["attention_id"])
    con.execute(
        "SELECT pg_advisory_xact_lock(hashtext(%s))",
        (f"admission-billing:{source}:{identity}",),
    )
    row = con.execute(
        """SELECT attention_id FROM admission_attention_projection
           WHERE source_instance_id=%s AND attention_id=%s FOR UPDATE""",
        (source, identity),
    ).fetchone()
    if not row:
        raise ValueError("La atención ya no existe.")
    result = backend.evaluate_attention_billing_eligibility(
        identity,
        actor,
        source_instance_id=source,
        session_id=session_id,
        connection=con,
    )
    if not result.get("eligible"):
        raise ValueError(result.get("reason") or "La atención ya no está pendiente.")
    if result.get("turn_scope") != "INHERITED":
        raise ValueError("Selecciona una atención heredada pendiente.")
    return dict(result["_projection"])


def _write_link(con, receipt_id, attention, actor, backend):
    source, identity = attention["source_instance_id"], attention["attention_id"]
    con.execute(
        """UPDATE recibos SET admission_atencion_id=%s,admission_paciente_id=%s,
           admission_global_attention_id=%s,admission_source_instance_id=%s,
           admission_nss_snapshot=%s,admission_cedula_snapshot=%s,admission_ars_snapshot=%s,
           admission_linked_at=%s,admission_linked_by=%s,admission_snapshot_hash=%s,
           admission_source_updated_at=%s,admission_coverage_status=%s,admission_readiness=%s,
           turno_origen_id=%s,turno_procesamiento_id=%s,herencia_estado='HEREDADA_PROCESADA',
           herencia_procesada_at=%s,herencia_procesada_por=%s,
           revision_version=revision_version+1 WHERE id=%s""",
        (
            identity,
            attention["patient_id"],
            attention.get("global_attention_id"),
            source,
            attention.get("nss_snapshot") or "",
            attention.get("cedula_snapshot") or "",
            attention["canonical_ars"],
            backend.now_str(),
            actor["username"],
            attention.get("snapshot_hash") or "",
            attention.get("source_updated_at") or "",
            attention.get("coverage_status") or "",
            attention.get("readiness") or "",
            attention["turn_id"],
            attention["active_turn_id"],
            backend.now_str(),
            actor["username"],
            receipt_id,
        ),
    )
    con.execute(
        """UPDATE admission_billing_claims SET receipt_id=%s,processed_at=NOW(),expires_at=NOW()
           WHERE source_instance_id=%s AND attention_id=%s""",
        (receipt_id, source, identity),
    )
    backend._upsert_admission_inheritance(
        con,
        source_instance_id=source,
        attention_id=identity,
        turno_origen_id=attention["turn_id"],
        estado="COMPLETADA",
        turno_procesamiento_id=attention["active_turn_id"],
        processed_by=actor["username"],
        receipt_id=receipt_id,
    )
    backend._insert_action_history(
        con,
        actor["username"],
        "ADMISSION_ATTENTION_LINKED_LATER",
        f"Recibo id={receipt_id}; atención={identity}; origen={source}; resultado=VINCULADO",
        module="Facturación",
        entity_type="recibo",
        entity_id=str(receipt_id),
        role=backend.ROLE_ADMIN,
    )


def link_receipt_to_inherited_attention(
    receipt_id, selection, username, *, backend, session_id=""
):
    actor = backend.get_user(username) or {}
    if backend.normalize_role(actor.get("role")) != backend.ROLE_ADMIN:
        raise PermissionError("Solo el administrador puede vincular recibos.")
    selection = (
        selection.snapshot() if hasattr(selection, "snapshot") else dict(selection)
    )
    with backend.db_connect() as con:
        attention = _lock_attention(con, selection, backend, actor, session_id)
        row = con.execute(
            """SELECT nombre,ars,tipo_cobertura,is_deleted,estado_documento,
               estado_facturacion,admission_atencion_id,admission_global_attention_id,
               admission_paciente_id,admission_nss_snapshot,admission_cedula_snapshot
               FROM recibos WHERE id=%s FOR UPDATE""",
            (int(receipt_id),),
        ).fetchone()
        receipt = dict(row) if row else None
        validate_unlinked_receipt(receipt)
        validate_matching_patient(receipt, attention)
        _write_link(con, int(receipt_id), attention, actor, backend)
    backend.invalidate_admission_validation_cache()
    return int(receipt_id)
