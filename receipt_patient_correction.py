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
    if name == str(receipt.get("nombre") or ""):
        return
    key = guard(
        connection,
        nombre=name,
        fecha=receipt["fecha"],
        username=actor,
        attention_id=receipt.get("admission_atencion_id"),
        nss=receipt.get("admission_nss_snapshot"),
        cedula=receipt.get("admission_cedula_snapshot"),
        source_instance_id=receipt.get("admission_source_instance_id"),
        exclude_id=receipt["id"],
    )
    write_name_key(connection, receipt, name, key)


def write_name_key(connection, receipt, name, key):
    if name == str(receipt.get("nombre") or ""):
        return
    connection.execute(
        "UPDATE recibos SET dedup_key=%s WHERE id=%s", (key or None, receipt["id"])
    )


def lock_name_attention(connection, receipt_id, name):
    if name is None:
        return None
    receipt = connection.execute(
        """SELECT nombre,admission_global_attention_id,admission_atencion_id,
                  admission_source_instance_id FROM recibos
           WHERE id=%s AND COALESCE(is_deleted,0)=0""",
        (int(receipt_id),),
    ).fetchone()
    if not _name_needs_link(receipt, name):
        return None
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
    corrected = connection.execute(
        "SELECT snapshot_hash,source_updated_at FROM admission_attention_projection WHERE global_attention_id=%s",
        (attention["global_attention_id"],),
    ).fetchone()
    connection.execute(
        """UPDATE recibos SET admission_snapshot_hash=%s,admission_source_updated_at=%s
           WHERE id=%s""",
        (corrected["snapshot_hash"], corrected["source_updated_at"], receipt["id"]),
    )
    return corrected


def _require_current_link(receipt, attention):
    if (
        not attention
        or not attention.get("global_patient_id")
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
