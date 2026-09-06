"""Synthetic history/selector tracer; never includes patient fields in diagnostics."""

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import (
    GLOBAL,
    USER,
    candidates,
    database as database,
    server as server,
)


@pytest.mark.parametrize(
    "cause,reason", [("readiness", "NOT_READY"), ("receipt", "RECEIPT_PENDING")]
)
def test_history_visibility_does_not_imply_billing_eligibility(database, cause, reason):
    with database() as connection:
        before = connection.execute(
            "SELECT global_attention_id,turn_id,operational_source_id FROM admission_attention_projection"
        ).fetchone()
        if cause == "readiness":
            connection.execute(
                "UPDATE admission_attention_projection SET readiness='PENDIENTE_CORRECCION'"
            )
        else:
            connection.execute(
                "INSERT INTO recibos(id,admission_global_attention_id) VALUES(6004,%s)",
                (GLOBAL,),
            )
    history = app.list_admission_history(current_user=USER, turn_filter="ACTUAL")
    assert len(history["rows"]) == 1
    assert str(history["rows"][0]["global_attention_id"]) == GLOBAL
    assert candidates(GLOBAL) == []
    result = app.evaluate_attention_billing_eligibility(
        329, USER, global_attention_id=GLOBAL
    )
    assert result["eligible"] is False
    assert result["reason_code"] == reason
    with database() as connection:
        after = connection.execute(
            "SELECT global_attention_id,turn_id,operational_source_id FROM admission_attention_projection"
        ).fetchone()
    assert after == before
