"""Recover effects of committed relays; never initiate or expire a turn."""

from uuid import NAMESPACE_URL, uuid5
from datetime import datetime

from admission_bridge import AdmissionShiftClosure
from billing_inheritance_scope import HOSPITAL_TIMEZONE, PENDING_RESTART_AT


def install_closure_dispatch_schema(connection):
    """Upgrade old installations and start automatic delivery prospectively."""
    connection.execute(
        "ALTER TABLE billing_shift_closure_details "
        "ADD COLUMN IF NOT EXISTS global_attention_id UUID"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS billing_closure_dispatch_policy(
               singleton INTEGER PRIMARY KEY CHECK(singleton=1),
               enabled_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
           )"""
    )
    connection.execute(
        "INSERT INTO billing_closure_dispatch_policy(singleton) VALUES(1) "
        "ON CONFLICT(singleton) DO NOTHING"
    )


def can_recover_closures(state):
    """Allow either synchronized station to restore durable closure data."""
    role = str(state.get("role") or "").upper()
    return (
        not state.get("offline")
        and int(state.get("pending_sync_count") or 0) == 0
        and role in {"PRIMARY", "SECONDARY"}
        and bool(state.get("local_device_id"))
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
           JOIN billing_closure_dispatch_policy policy
             ON policy.singleton=1 AND i.ended_at>=policy.enabled_at
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
             AND (c.snapshot_created_at IS NULL
                  OR c.pdf_status IN ('PENDIENTE','ERROR','OMITIDO_VACIO')
                  OR (c.pdf_status='GENERADO' AND c.print_requested_at IS NULL))
           ORDER BY i.ended_at,i.turn_id LIMIT 50""",
        (PENDING_RESTART_AT,),
    ).fetchall()


def closed_turn_attentions(connection, event, *, previous=False):
    turn_filter = "p.turn_id=%s"
    parameters = (event.source_instance_id, event.turn_id)
    if previous:
        turn_filter = """p.turn_id IN (
            SELECT i.turn_id FROM admission_operational_turn_intervals i
            JOIN admission_operational_sessions s USING(operational_session_id)
            WHERE s.operational_source_id::TEXT=%s
              AND i.ended_at<=COALESCE((
                  SELECT MIN(current_turn.started_at)
                  FROM admission_operational_turn_intervals current_turn
                  JOIN admission_operational_sessions current_session
                    USING(operational_session_id)
                  WHERE current_session.operational_source_id::TEXT=%s
                    AND current_turn.turn_id=%s), %s)
              AND i.ended_at>=%s AND i.turn_id<>%s)"""
        parameters = (
            event.source_instance_id,
            event.source_instance_id,
            event.source_instance_id,
            event.turn_id,
            datetime.fromisoformat(event.started_at).replace(tzinfo=HOSPITAL_TIMEZONE),
            PENDING_RESTART_AT,
            event.turn_id,
        )
    return [
        dict(row)
        for row in connection.execute(
            f"""SELECT p.*,p.patient_name AS name,p.nss_snapshot AS nss_clean,
                  p.cedula_snapshot AS cedula_clean,p.service_type AS attention_type
           FROM admission_attention_projection p
           WHERE p.operational_source_id::TEXT=%s AND {turn_filter}
             AND NOT COALESCE(p.is_deleted,FALSE)
             AND UPPER(COALESCE(p.source_status,'ACTIVA')) IN ('ACTIVA','PENDIENTE')
             AND NOT EXISTS(SELECT 1 FROM admission_quick_list_dismissals d
                 WHERE d.source_instance_id=p.source_instance_id
                   AND d.attention_id=p.attention_id AND d.is_active)""",
            parameters,
        ).fetchall()
    ]
