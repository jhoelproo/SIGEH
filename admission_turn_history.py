"""Read paged turn metadata; never download or store report documents."""

from datetime import datetime, time, timedelta

from admission_statistical_reports import (
    HOSPITAL_TIMEZONE,
    OperationalPeriod,
    coerce_hospital_datetime,
)


PAGE_SIZE = 50


def historical_turn_period(turn):
    start = coerce_hospital_datetime(turn.get("started_at"))
    end = coerce_hospital_datetime(turn.get("ends_at"))
    if start is None or end is None or end <= start:
        raise ValueError("El turno no tiene un intervalo histórico válido.")
    return OperationalPeriod(
        start, end, f"Turno #{turn['turn_id']} · {turn['display_name']}"
    )


def search_turns(connection, start_date, end_date, username="", cursor=None):
    if end_date < start_date:
        raise ValueError("La fecha final debe ser igual o posterior a la inicial.")
    start = datetime.combine(start_date, time.min, HOSPITAL_TIMEZONE)
    end = datetime.combine(end_date + timedelta(days=1), time.min, HOSPITAL_TIMEZONE)
    parameters = [
        start,
        end,
        username.strip(),
        f"%{username.strip()}%",
        f"%{username.strip()}%",
    ]
    page_filter = ""
    if cursor:
        page_filter = (
            "AND (started_at,turn_id,username,operational_source_id)<(%s,%s,%s,%s)"
        )
        parameters.extend(cursor)
    parameters.append(PAGE_SIZE + 1)
    rows = connection.execute(
        f"""WITH candidates AS (
            SELECT s.operational_source_id::TEXT AS operational_source_id,
                   i.turn_id,i.started_at,
                   COALESCE(i.ended_at,i.nominal_ends_at,s.turn_ends_at) AS ends_at,
                   COALESCE(a.username,i.active_username,'') AS username,
                   COALESCE(NULLIF(u.full_name,''),NULLIF(a.username,''),
                            NULLIF(i.active_username,''),'Sin usuario') AS display_name,
                   COALESCE(a.patient_count,0) AS patient_count
            FROM admission_operational_turn_intervals i
            JOIN admission_operational_sessions s USING(operational_session_id)
            LEFT JOIN LATERAL (
                SELECT COALESCE(p.admission_username,'') AS username,COUNT(*) AS patient_count
                FROM admission_attention_projection p
                WHERE p.operational_source_id=s.operational_source_id AND p.turn_id=i.turn_id
                  AND NOT COALESCE(p.is_deleted,FALSE)
                  AND UPPER(COALESCE(p.source_status,'ACTIVA')) IN ('ACTIVA','PENDIENTE')
                GROUP BY COALESCE(p.admission_username,'')
            ) a ON TRUE
            LEFT JOIN users u ON u.username=COALESCE(a.username,i.active_username,'')
            WHERE i.started_at>=%s AND i.started_at<%s
        ) SELECT * FROM candidates
          WHERE (%s='' OR username ILIKE %s OR display_name ILIKE %s) {page_filter}
          ORDER BY started_at DESC,turn_id DESC,username DESC,operational_source_id DESC
          LIMIT %s""",
        tuple(parameters),
    ).fetchall()
    return [dict(row) for row in rows]


def turn_cursor(turn):
    return tuple(
        turn[key]
        for key in ("started_at", "turn_id", "username", "operational_source_id")
    )


def selected_turn_records(connection, turn):
    rows = connection.execute(
        """SELECT p.attention_id,p.global_attention_id,p.operational_source_id,p.turn_id,
                  p.patient_name,p.service_date,p.service_time,p.specialty,p.canonical_ars,
                  p.nss_snapshot AS nss,p.cedula_snapshot AS cedula,p.service_type,
                  p.coverage_status,p.admission_username,p.created_at_effective_utc,
                  p.source_status,p.is_deleted
           FROM admission_attention_projection p
           WHERE p.operational_source_id::TEXT=%s AND p.turn_id=%s
             AND COALESCE(p.admission_username,'')=%s
             AND NOT COALESCE(p.is_deleted,FALSE)
             AND UPPER(COALESCE(p.source_status,'ACTIVA')) IN ('ACTIVA','PENDIENTE')
           ORDER BY p.created_at_effective_utc,p.attention_id""",
        (turn["operational_source_id"], int(turn["turn_id"]), turn["username"]),
    ).fetchall()
    return [dict(row) for row in rows]
