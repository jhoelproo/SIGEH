"""Recover effects of committed relays; never initiate or expire a turn."""

from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid5

from admission_bridge import AdmissionShiftClosure

HOSPITAL_TIMEZONE = timezone(timedelta(hours=-4))
# The user-authorized restart is a fixed boundary, not a rolling daily filter.
PENDING_RESTART_AT = datetime(2026, 9, 7, tzinfo=HOSPITAL_TIMEZONE)


def can_recover_closures(state):
    return (
        not state.get("offline")
        and int(state.get("pending_sync_count") or 0) == 0
        and str(state.get("role") or "").upper() == "PRIMARY"
        and bool(state.get("local_device_id"))
        and state.get("local_device_id") == state.get("primary_device_id")
    )


def _local_stamp(value):
    return value.astimezone(HOSPITAL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def closure_from_interval(row):
    source = str(row["operational_source_id"])
    turn = int(row["turn_id"])
    start = _local_stamp(row["started_at"])
    end = _local_stamp(row["ended_at"])
    return AdmissionShiftClosure(
        0,
        str(uuid5(NAMESPACE_URL, f"sigeh-central-closure:{source}:{turn}")),
        source,
        turn,
        0,
        start[:10],
        start,
        _local_stamp(row.get("nominal_ends_at") or row["ended_at"]),
        end,
        str(row.get("active_username") or ""),
        "",
        str(row.get("closed_by") or "Sistema"),
        "",
        str(row["operational_session_id"]),
        end,
    )


def pending_central_closures(connection):
    return connection.execute(
        """SELECT i.*,s.operational_source_id,
                  COALESCE(a.username,i.active_username) AS closed_by
           FROM admission_operational_turn_intervals i
           JOIN admission_operational_sessions s USING(operational_session_id)
           JOIN sigeh_product_state product
             ON product.singleton=1 AND product.production_epoch_id=i.production_epoch_id
           JOIN LATERAL (
               SELECT username FROM admission_operational_audit audit
               WHERE audit.operational_session_id=i.operational_session_id
                 AND audit.details_json->'result'->>'old_turn_id'=i.turn_id::TEXT
                 AND audit.details_json->>'status'='COMMITTED'
                 AND audit.event_type='TURN_HANDOFF_TRANSITION'
                 AND audit.details_json->'request'->>'transition_type'='PRIMARY_USER_HANDOFF'
               ORDER BY audit.id DESC LIMIT 1
           ) a ON TRUE
           LEFT JOIN billing_shift_closures c
             ON c.source_instance_id=s.operational_source_id::TEXT AND c.turn_id=i.turn_id
           WHERE i.ended_at IS NOT NULL AND i.ended_at>=%s
             AND i.turn_id IS NOT NULL
             AND (c.snapshot_created_at IS NULL OR c.pdf_status IN ('PENDIENTE','ERROR'))
           ORDER BY i.ended_at,i.turn_id LIMIT 50""",
        (PENDING_RESTART_AT,),
    ).fetchall()


def closed_turn_attentions(connection, event):
    return [
        dict(row)
        for row in connection.execute(
            """SELECT p.*,p.patient_name AS name,p.nss_snapshot AS nss_clean,
                  p.cedula_snapshot AS cedula_clean,p.service_type AS attention_type
           FROM admission_attention_projection p
           WHERE p.operational_source_id::TEXT=%s AND p.turn_id=%s
             AND NOT COALESCE(p.is_deleted,FALSE)
             AND UPPER(COALESCE(p.source_status,'ACTIVA')) IN ('ACTIVA','PENDIENTE')
             AND NOT EXISTS(SELECT 1 FROM admission_quick_list_dismissals d
                 WHERE d.source_instance_id=p.source_instance_id
                   AND d.attention_id=p.attention_id AND d.is_active)""",
            (event.source_instance_id, event.turn_id),
        ).fetchall()
    ]
