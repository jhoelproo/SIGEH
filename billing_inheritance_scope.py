"""Canonical SQL scope for pending admissions inherited after the reset."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


HOSPITAL_TIMEZONE = timezone(timedelta(hours=-4))
PENDING_RESTART_AT = datetime(2026, 9, 7, tzinfo=HOSPITAL_TIMEZONE)
PENDING_RESTART_SQL = f"TIMESTAMPTZ '{PENDING_RESTART_AT.isoformat()}'"


def inherited_attention_sql(
    projection_alias: str = "p",
    current_shift_alias: str = "cs",
    inheritance_alias: str = "inheritance",
) -> str:
    """Return the durable inherited-turn predicate shared by Billing reads.

    Explicit inheritances created before the authorized restart stay historical.
    A confirmed primary handoff is also sufficient evidence when closure artifact
    recovery has not yet materialized the inheritance row.
    """
    p = projection_alias
    shift = current_shift_alias
    inheritance = inheritance_alias
    return f"""(
        {p}.turn_id<>{shift}.turn_id
        AND (
            (
                {inheritance}.attention_id IS NOT NULL
                AND COALESCE(
                    {p}.created_at_effective_utc,
                    TO_TIMESTAMP(0)
                )>={PENDING_RESTART_SQL}
            )
            OR EXISTS (
                SELECT 1
                  FROM admission_operational_turn_intervals inherited_interval
                  JOIN admission_operational_sessions inherited_session
                    ON inherited_session.operational_session_id=
                       inherited_interval.operational_session_id
                  JOIN sigeh_product_state inherited_product
                    ON inherited_product.singleton=1
                   AND inherited_product.production_epoch_id=
                       inherited_interval.production_epoch_id
                 WHERE inherited_session.operational_source_id=
                       {p}.operational_source_id
                   AND inherited_interval.turn_id={p}.turn_id
                   AND inherited_interval.ended_at>={PENDING_RESTART_SQL}
                   AND EXISTS (
                       SELECT 1
                         FROM admission_operational_audit inherited_audit
                        WHERE inherited_audit.operational_session_id=
                              inherited_interval.operational_session_id
                          AND inherited_audit.event_type='TURN_HANDOFF_TRANSITION'
                          AND inherited_audit.details_json->>'status'='COMMITTED'
                          AND inherited_audit.details_json->'request'->>
                              'transition_type'='PRIMARY_USER_HANDOFF'
                          AND inherited_audit.details_json->'result'->>
                              'old_turn_id'={p}.turn_id::TEXT
                   )
            )
        )
    )"""
