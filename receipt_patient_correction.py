"""Correct a receipt's linked patient and attention within its transaction."""

MAX_PATIENT_NAME_LENGTH = 160


def optional_patient_name(value):
    return None if value is None else normalized_patient_name(value)


def require_expected_name(receipt, expected):
    if expected is not None and expected != receipt["nombre"]:
        raise ValueError("El nombre cambió en otra pantalla. Actualiza el listado.")


def normalized_patient_name(value):
    text = " ".join(str(value or "").split())
    if not text:
        raise ValueError("Escribe el nombre del paciente.")
    if len(text) > MAX_PATIENT_NAME_LENGTH:
        raise ValueError("El nombre no puede exceder 160 caracteres.")
    return text


def update_name_key(connection, receipt, name, actor, guard):
    update_header_key(connection, receipt, {"nombre": name}, actor, guard)


def write_name_key(connection, receipt, name, key):
    if name == str(receipt.get("nombre") or ""):
        return
    connection.execute(
        "UPDATE recibos SET dedup_key=%s WHERE id=%s", (key or None, receipt["id"])
    )


def lock_name_attention(connection, receipt_id, name):
    if name is None:
        return None
    receipt = _read_linked_receipt(connection, receipt_id)
    if not _name_needs_link(receipt, name):
        return None
    return _lock_central_attention(connection, receipt)


def lock_metadata_attention(connection, receipt_id):
    receipt = _read_linked_receipt(connection, receipt_id)
    if not receipt or not (
        receipt["admission_global_attention_id"] or receipt["admission_atencion_id"]
    ):
        return None
    return _lock_central_attention(connection, receipt, allow_legacy=True)


def _read_linked_receipt(connection, receipt_id):
    return connection.execute(
        """SELECT nombre,admission_global_attention_id,admission_atencion_id,
                  admission_source_instance_id FROM recibos
           WHERE id=%s AND COALESCE(is_deleted,0)=0""",
        (int(receipt_id),),
    ).fetchone()


def _lock_central_attention(connection, receipt, *, allow_legacy=False):
    global_id = str(receipt["admission_global_attention_id"] or "")
    local_id = receipt["admission_atencion_id"]
    row = connection.execute(
        """SELECT global_attention_id,attention_id,source_instance_id
           FROM admission_attention_projection
           WHERE ((%s<>'' AND global_attention_id::TEXT=%s)
             OR (%s='' AND attention_id=%s AND source_instance_id=%s))""",
        (
            global_id,
            global_id,
            global_id,
            local_id,
            receipt["admission_source_instance_id"] or "LEGACY",
        ),
    ).fetchone()
    if not row or not row["global_attention_id"]:
        if allow_legacy:
            return None
        raise ValueError("Corrige primero la identidad del paciente en Admisión.")
    for key in (
        f"admission-sync:attention:{row['global_attention_id']}",
        f"admission-billing:{row['source_instance_id']}:{row['attention_id']}",
    ):
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
    return connection.execute(
        "SELECT * FROM admission_attention_projection WHERE global_attention_id=%s FOR UPDATE",
        (row["global_attention_id"],),
    ).fetchone()


def update_header_key(connection, receipt, changes, actor, guard):
    """The duplicate guard must use the final name and final service date."""
    name = changes.get("nombre", receipt["nombre"])
    service_date = changes.get("fecha", receipt["fecha"])
    if (name, service_date) == (receipt["nombre"], receipt["fecha"]):
        return
    final = {**receipt, **changes}
    key = guard(
        connection,
        nombre=name,
        fecha=service_date,
        username=actor,
        attention_id=final.get("admission_atencion_id"),
        nss=final.get("admission_nss_snapshot"),
        cedula=final.get("admission_cedula_snapshot"),
        source_instance_id=final.get("admission_source_instance_id"),
        exclude_id=receipt["id"],
    )
    connection.execute(
        "UPDATE recibos SET dedup_key=%s WHERE id=%s", (key or None, receipt["id"])
    )


def linked_metadata_patch(changes):
    mapping = {
        "fecha": "service_date",
        "numero_autorizacion": "authorization",
        "specialty_snapshot": "specialty",
        "admission_nss_snapshot": "nss",
        "admission_cedula_snapshot": "cedula",
        "insurance_document_type": "insurance_document_type",
        "insurance_document_number": "insurance_document_number",
    }
    patch = {
        target: changes[source]
        for source, target in mapping.items()
        if source in changes
    }
    if "specialty" in patch:
        patch["detail_sheet"] = patch["specialty"]
    return patch


def correct_linked_metadata(connection, receipt, attention, changes, actor, stamp):
    if not (
        receipt.get("admission_global_attention_id")
        or receipt.get("admission_atencion_id")
    ):
        return None
    _require_current_link(receipt, attention, require_patient=False)
    row = connection.execute(
        "SELECT * FROM admission_attention_projection WHERE global_attention_id=%s FOR UPDATE",
        (attention["global_attention_id"],),
    ).fetchone()
    _require_current_link(receipt, row, require_patient=False)
    from admission_demographics import _correct_snapshot

    patch = linked_metadata_patch(changes)
    patch["receipt_correction_actor"] = str(actor.get("username") or "Sistema")
    _correct_snapshot(
        connection,
        dict(row),
        {
            "global_patient_id": row["global_patient_id"],
            "server_revision": row["server_revision"],
        },
        stamp,
        attention_patch=patch,
    )
    return _refresh_receipt_link_snapshot(
        connection, receipt["id"], attention["global_attention_id"]
    )


def correct_edited_receipt_metadata(
    connection, receipt, attention, service_date, authorization, actor, stamp
):
    changes = {
        field: value
        for field, value in (
            ("fecha", service_date),
            ("numero_autorizacion", authorization),
        )
        if str(value or "") != str(receipt.get(field) or "")
    }
    if not changes:
        return None
    if (
        not attention
        and not receipt.get("admission_global_attention_id")
        and "fecha" not in changes
    ):
        return None
    return correct_linked_metadata(
        connection, receipt, attention, changes, actor, stamp
    )


def _name_needs_link(receipt, name):
    return bool(
        receipt
        and name != str(receipt["nombre"] or "")
        and (
            receipt["admission_global_attention_id"] or receipt["admission_atencion_id"]
        )
    )


def correct_linked_name(connection, receipt, attention, name, actor):
    if name == str(receipt.get("nombre") or ""):
        return None
    if not (
        receipt.get("admission_global_attention_id")
        or receipt.get("admission_atencion_id")
    ):
        return None
    _require_current_link(receipt, attention)
    patient = _write_patient_name(connection, receipt, attention, name, actor)
    from admission_demographics import _correct_snapshot

    _correct_snapshot(
        connection,
        dict(attention),
        {
            "global_patient_id": patient["global_patient_id"],
            "server_revision": patient["server_revision"],
            "nombre": name,
        },
        patient["updated_at"],
    )
    return _refresh_receipt_link_snapshot(
        connection, receipt["id"], attention["global_attention_id"]
    )


def _refresh_receipt_link_snapshot(connection, receipt_id, attention_id):
    corrected = connection.execute(
        "SELECT snapshot_hash,source_updated_at FROM admission_attention_projection WHERE global_attention_id=%s",
        (attention_id,),
    ).fetchone()
    connection.execute(
        """UPDATE recibos SET admission_snapshot_hash=%s,admission_source_updated_at=%s
           WHERE id=%s""",
        (corrected["snapshot_hash"], corrected["source_updated_at"], receipt_id),
    )
    return corrected


def _require_current_link(receipt, attention, *, require_patient=True):
    if (
        not attention
        or (require_patient and not attention.get("global_patient_id"))
        or attention.get("is_deleted")
    ):
        raise ValueError("El paciente vinculado ya no está disponible en Admisión.")
    global_id = str(receipt.get("admission_global_attention_id") or "")
    matches = (
        global_id == str(attention["global_attention_id"])
        if global_id
        else (
            receipt["admission_atencion_id"],
            receipt.get("admission_source_instance_id") or "LEGACY",
        )
        == (attention["attention_id"], attention["source_instance_id"])
    )
    if not matches:
        raise ValueError(
            "El vínculo del recibo cambió. Recarga el listado antes de corregir."
        )


def _write_patient_name(connection, receipt, attention, name, actor):
    from patient_directory import CentralPatientDirectoryRepository

    patient = connection.execute(
        "SELECT * FROM admission_patient_directory WHERE global_patient_id=%s FOR UPDATE",
        (attention["global_patient_id"],),
    ).fetchone()
    if not patient or patient["is_deleted"]:
        raise ValueError("El paciente ya no está disponible en Admisión.")
    if patient["patient_name"] not in (
        attention["patient_name"],
        receipt["nombre"],
        name,
    ):
        raise ValueError(
            "El nombre cambió en Admisión. Recarga el paciente antes de corregir."
        )
    updated = connection.execute(
        """UPDATE admission_patient_directory SET patient_name=%s,
               server_revision=server_revision+1,updated_at=NOW()
           WHERE global_patient_id=%s RETURNING *""",
        (name, attention["global_patient_id"]),
    ).fetchone()
    CentralPatientDirectoryRepository._insert_event(
        connection,
        dict(updated),
        "PATIENT_UPDATED",
        audit={
            "operation": "RECEIPT_NAME_CORRECTION",
            "actor_user": actor.get("username") or "Sistema",
            "actor_role": actor.get("role") or "",
            "fields_changed": ["patient_name"],
        },
    )
    return updated
