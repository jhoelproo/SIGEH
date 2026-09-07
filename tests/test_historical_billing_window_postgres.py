"""Real PostgreSQL checks for the two-day historical billing window."""

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import (
    GLOBAL,
    database as database,
    server as server,
)


def _age_attention(database, age):
    with app.db_connect() as connection:
        connection.execute(
            """UPDATE admission_attention_projection
               SET turn_id=3948,
                   created_at_effective_utc=CURRENT_TIMESTAMP-(%s::INTERVAL)
               WHERE global_attention_id=%s""",
            (age, GLOBAL),
        )


@pytest.mark.parametrize(
    ("role", "age", "expected", "reason_code"),
    (
        (app.ROLE_AUX, "47 hours 59 minutes", True, "ELIGIBLE_PENDING"),
        (
            app.ROLE_MEDICAL_AUDIT,
            "47 hours 59 minutes",
            True,
            "ELIGIBLE_PENDING",
        ),
        (app.ROLE_AUX, "2 days", False, "HISTORICAL_TIME_DENIED"),
        (app.ROLE_MEDICAL_AUDIT, "3 days", False, "HISTORICAL_TIME_DENIED"),
        (app.ROLE_ADMIN, "30 days", True, "ELIGIBLE_PENDING"),
        (app.ROLE_AUDIT, "30 days", True, "ELIGIBLE_PENDING"),
    ),
)
def test_historical_billing_window_uses_database_time(
    database, role, age, expected, reason_code
):
    _age_attention(database, age)

    result = app.evaluate_attention_billing_eligibility(
        0,
        {"username": "operator", "role": role},
        global_attention_id=GLOBAL,
        session_id="session-a",
    )

    assert result["eligible"] is expected
    assert result["reason_code"] == reason_code


def test_auxiliary_history_lists_recent_but_not_expired_emergencies(database):
    _age_attention(database, "47 hours 59 minutes")
    recent = app.list_admission_history(current_user={"role": app.ROLE_AUX})

    _age_attention(database, "2 days 1 minute")
    expired = app.list_admission_history(current_user={"role": app.ROLE_AUX})
    privileged = app.list_admission_history(current_user={"role": app.ROLE_ADMIN})

    assert [row["global_attention_id"] for row in recent["rows"]] == [GLOBAL]
    assert expired["rows"] == []
    assert [row["global_attention_id"] for row in privileged["rows"]] == [GLOBAL]
