"""Bounded demographic correction using the canonical patient identity."""

import json
from uuid import NAMESPACE_URL, uuid5


def demographic_patch(patient):
    fields = {
        "nombre": "name",
        "cedula": "cedula",
        "nss": "nss",
        "telefono": "phone",
        "direccion": "address",
        "nacionalidad": "nationality",
        "ars": "ars",
    }
    return {
        target: patient[source]
        for source, target in fields.items()
        if source in patient
    }


def lock_recent_attentions(connection, patient_id):
    """Match synchronization's attention-before-patient lock order."""
    rows = connection.execute(
        """SELECT global_attention_id FROM admission_attention_projection
           WHERE global_patient_id=%s::UUID AND global_attention_id IS NOT NULL
             AND created_at_effective_utc BETWEEN NOW()-INTERVAL '7 days' AND NOW()
           ORDER BY global_attention_id""",
        (patient_id,),
    ).fetchall()
    for row in rows:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"admission-sync:attention:{row['global_attention_id']}",),
        )


def correct_recent_attentions(connection, patient, *, edited_at):
    """Run inside the patient edit transaction; receipts are never modified."""
    patient_id = patient["global_patient_id"]
    candidates = connection.execute(
        """SELECT global_attention_id FROM admission_attention_projection
           WHERE global_patient_id=%s::UUID
             AND global_attention_id IS NOT NULL
             AND created_at_effective_utc BETWEEN %s::timestamptz-INTERVAL '7 days'
                                             AND %s::timestamptz
           ORDER BY global_attention_id""",
        (patient_id, edited_at, edited_at),
    ).fetchall()
    count = 0
    for candidate in candidates:
        identity = str(candidate["global_attention_id"])
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"admission-sync:attention:{identity}",),
        )
        row = connection.execute(
            """SELECT * FROM admission_attention_projection
               WHERE global_attention_id=%s::UUID AND global_patient_id=%s::UUID
                 AND created_at_effective_utc BETWEEN %s::timestamptz-INTERVAL '7 days'
                                                 AND %s::timestamptz
               FOR UPDATE""",
            (identity, patient_id, edited_at, edited_at),
        ).fetchone()
        if row:
            _correct_snapshot(connection, dict(row), patient, edited_at)
            count += 1
    return count


def _correct_snapshot(connection, row, patient, edited_at):
    from admission_hybrid import AdmissionCloudRepository
    from admission_contract import (
        assess_coverage,
        assess_billing_readiness,
        stable_snapshot_hash,
    )

    event = AdmissionCloudRepository._readthrough_event(row)
    payload = event["payload_json"]
    for key in (
        "operational_session_id",
        "operational_source_id",
        "turn_id",
        "generation",
        "origin_device_id",
        "origin_user_id",
        "created_at_device",
        "created_at_effective_utc",
        "device_local_sequence",
        "admission_username",
    ):
        if row.get(key) is not None:
            payload[key] = row[key]
            event["origin_username" if key == "admission_username" else key] = row[key]
    payload.update(demographic_patch(patient))
    coverage = assess_coverage(payload["ars"], payload["nss"])
    readiness = assess_billing_readiness(
        name=payload["name"],
        service_date=payload.get("service_date"),
        attention_type=payload.get("service_type"),
        coverage=coverage,
        cedula=payload["cedula"],
    )
    revision = int(row["server_revision"]) + 1
    payload["version"] = revision
    event_id = str(
        uuid5(
            NAMESPACE_URL,
            (
                f"sigeh-patient-correction:{patient['global_patient_id']}:"
                f"{patient['server_revision']}:{row['global_attention_id']}"
            ),
        )
    )
    encoded = json.dumps(payload, ensure_ascii=False, default=str)
    connection.execute(
        """UPDATE admission_attention_projection SET
               patient_name=%s,cedula_snapshot=%s,nss_snapshot=%s,canonical_ars=%s,
               latest_payload_json=%s::jsonb,server_revision=%s,version=%s,
               coverage_status=%s,readiness=%s,readiness_reasons=%s,
               snapshot_hash=%s,source_updated_at=%s
           WHERE global_attention_id=%s::UUID""",
        (
            payload["name"],
            payload["cedula"],
            payload["nss"],
            coverage.canonical_ars,
            encoded,
            revision,
            revision,
            coverage.status,
            readiness.status,
            json.dumps(list(readiness.reasons), ensure_ascii=False),
            stable_snapshot_hash(json.loads(encoded)),
            str(edited_at),
            row["global_attention_id"],
        ),
    )
    connection.execute(
        """INSERT INTO admission_sync_events(
               event_uuid,entity_type,entity_uuid,operation,payload_json,
               operational_session_id,generation,origin_device_id,
               base_version,resulting_version,created_at,operational_source_id,
               turn_id,origin_user_id,origin_username,created_at_device,
               created_at_effective_utc,device_local_sequence
           ) VALUES(%s,'attention',%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s)""",
        (
            event_id,
            row["global_attention_id"],
            event["operation"],
            encoded,
            event["operational_session_id"],
            event["generation"],
            event["origin_device_id"],
            row["server_revision"],
            revision,
            edited_at,
            event["operational_source_id"],
            event["turn_id"],
            event["origin_user_id"],
            event["origin_username"],
            event["created_at_device"],
            event["created_at_effective_utc"],
            event["device_local_sequence"],
        ),
    )
