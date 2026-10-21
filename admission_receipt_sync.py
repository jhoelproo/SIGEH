"""Administrative receipt snapshots follow their explicit admission identities."""

from receipt_list_consistency import sync_pending_lists


def linked_patient_receipts(connection, patient_id):
    rows = connection.execute(
        """SELECT r.* FROM recibos r
           WHERE COALESCE(r.is_deleted,0)=0 AND EXISTS(
               SELECT 1 FROM admission_attention_projection p
               WHERE p.global_patient_id=%s::uuid AND (
                   r.admission_global_attention_id=p.global_attention_id OR (
                       r.admission_global_attention_id IS NULL
                       AND r.admission_atencion_id=p.attention_id
                       AND COALESCE(r.admission_source_instance_id,'LEGACY')=p.source_instance_id)))
           ORDER BY r.id FOR UPDATE OF r""",
        (patient_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def administrative_changes(receipt, patient, changed):
    mapping = {
        "patient_name": ("nombre", "nombre"),
        "nss": ("admission_nss_snapshot", "nss"),
        "cedula": ("admission_cedula_snapshot", "cedula"),
    }
    return {
        target: patient[source]
        for field, (target, source) in mapping.items()
        if field in changed
        and str(receipt.get(target) or "") != str(patient[source] or "")
    }


def synchronize_patient_receipts(connection, patient, changed, actor, stamp):
    receipts = linked_patient_receipts(connection, patient["global_patient_id"])
    for receipt in receipts:
        changes = administrative_changes(
            receipt, patient, set(changed) | {"nss", "cedula"}
        )
        if changes:
            _write_administrative_snapshot(
                connection, receipt, changes, actor, str(stamp)
            )
    return [
        {
            "id": row["id"],
            "numero": row["numero"],
            "ars": row["ars"],
            "estado_facturacion": row["estado_facturacion"],
        }
        for row in receipts
    ]


def _write_administrative_snapshot(connection, receipt, changes, actor, stamp):
    from receipt_documents import refresh_administrative_snapshot

    values = {
        key: changes.get(key, receipt.get(key))
        for key in ("nombre", "admission_nss_snapshot", "admission_cedula_snapshot")
    }
    connection.execute(
        """UPDATE recibos SET nombre=%s,admission_nss_snapshot=%s,
               admission_cedula_snapshot=%s,revision_version=revision_version+1 WHERE id=%s""",
        (*values.values(), receipt["id"]),
    )
    sync_pending_lists(connection, receipt["id"], actor, stamp)
    refresh_administrative_snapshot(connection, receipt["id"], actor)
    import json

    connection.execute(
        """INSERT INTO recibo_facturacion_history(recibo_id,estado_anterior,estado_nuevo,
            realizado_por,realizado_at,evento_tipo,observacion,total_al_momento,ars_al_momento,recibo_version)
           VALUES(%s,%s,%s,%s,%s,'CORRECCION_DESDE_ADMISION',%s,%s,%s,%s)""",
        (
            receipt["id"],
            receipt["estado_facturacion"],
            receipt["estado_facturacion"],
            actor,
            stamp,
            json.dumps(
                {
                    "previous": {key: receipt.get(key) for key in changes},
                    "current": changes,
                },
                ensure_ascii=False,
            ),
            receipt["total"],
            receipt["ars"],
            int(receipt.get("revision_version") or 0) + 1,
        ),
    )
