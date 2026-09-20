"""Keep immediate carryover separate from older pending admissions."""

from collections import Counter


def install_closure_categories(connection):
    """Extend snapshots without reclassifying previously captured closures."""
    connection.execute(
        "ALTER TABLE billing_shift_closures "
        "ADD COLUMN IF NOT EXISTS classification_version INTEGER NOT NULL DEFAULT 1"
    )
    connection.execute(
        "ALTER TABLE billing_shift_closure_details "
        "DROP CONSTRAINT IF EXISTS billing_shift_closure_details_classification_check"
    )
    connection.execute(
        """ALTER TABLE billing_shift_closure_details
           ADD CONSTRAINT billing_shift_closure_details_classification_check
           CHECK(classification IN (
               'AUTORIZADA','PENDIENTE DE AUTORIZACIÓN',
               'HEREDADA AUTORIZADA','HEREDADA PENDIENTE',
               'HISTÓRICA AUTORIZADA','HISTÓRICA PENDIENTE'
           ))"""
    )


def classify_closure_attention(*, authorized, inherited, origin_turn, previous_turn):
    if not inherited:
        return "AUTORIZADA" if authorized else "PENDIENTE DE AUTORIZACIÓN"
    prefix = "HEREDADA" if previous_turn == origin_turn else "HISTÓRICA"
    return f"{prefix} {'AUTORIZADA' if authorized else 'PENDIENTE'}"


def closure_category_counts(classifications):
    counts = Counter(classifications)
    result = {
        "eligible_current": counts["AUTORIZADA"] + counts["PENDIENTE DE AUTORIZACIÓN"],
        "authorized_current": counts["AUTORIZADA"],
        "new_pending": counts["PENDIENTE DE AUTORIZACIÓN"],
    }
    for key, prefix in (("inherited", "HEREDADA"), ("historical", "HISTÓRICA")):
        result[f"{key}_authorized"] = counts[f"{prefix} AUTORIZADA"]
        result[f"{key}_pending"] = counts[f"{prefix} PENDIENTE"]
        result[f"{key}_received"] = (
            result[f"{key}_authorized"] + result[f"{key}_pending"]
        )
    result["pending_next"] = (
        result["new_pending"]
        + result["inherited_pending"]
        + result["historical_pending"]
    )
    result["authorized_applicable"] = (
        result["authorized_current"]
        + result["inherited_authorized"]
        + result["historical_authorized"]
    )
    result["worked_applicable"] = sum(counts.values())
    result["authorization_rate"] = (
        100 * result["authorized_applicable"] / result["worked_applicable"]
        if result["worked_applicable"]
        else 0.0
    )
    return result


def previous_operational_turn(connection, event):
    row = connection.execute(
        """SELECT prior.turn_id
           FROM admission_operational_turn_intervals prior
           JOIN admission_operational_sessions s USING(operational_session_id)
           WHERE s.operational_source_id::TEXT=%s AND prior.turn_id<>%s
             AND prior.ended_at<=(
                 SELECT MIN(current_turn.started_at)
                 FROM admission_operational_turn_intervals current_turn
                 JOIN admission_operational_sessions current_session USING(operational_session_id)
                 WHERE current_session.operational_source_id::TEXT=%s
                   AND current_turn.turn_id=%s)
           ORDER BY prior.ended_at DESC,prior.started_at DESC LIMIT 1""",
        (
            event.source_instance_id,
            int(event.turn_id),
            event.source_instance_id,
            int(event.turn_id),
        ),
    ).fetchone()
    return int(row["turn_id"]) if row else None
