"""Exercise receipt creation and replay in a disposable local database."""

import json
from unittest.mock import patch

import pytest

import CALCULOS_QT as app
from local_receipts import LocalReceiptStore, LocalReceiptSynchronizer
from tests import test_integral_emergency_to_monthly_list as integral
from tests.test_local_receipts import receipt_job


@pytest.fixture
def central_database():
    fixture = integral.IntegralEmergencyToMonthlyListTests()
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.tearDown()


def test_offline_receipt_reopens_and_lost_ack_replays_once(central_database, tmp_path):
    job = receipt_job()
    job["current_user"].update(username="admin", role=app.ROLE_ADMIN)
    job["verification_bypass"] = {
        "reason": "Prueba administrativa local",
        "role": app.ROLE_ADMIN,
    }
    store = LocalReceiptStore(tmp_path / "receipts.sqlite3")
    identity = store.enqueue(job)
    actor = {"username": "admin", "role": app.ROLE_ADMIN, "is_active": 1}
    calls = []

    def publish(payload, request_id, digest):
        with patch.object(app, "get_user", return_value=actor):
            result = app.publish_local_receipt_command(payload, request_id, digest)
        calls.append(result)
        if len(calls) == 1:
            raise ConnectionError("server closed the connection after commit")
        return result

    service = LocalReceiptSynchronizer(
        store, publish, app.is_temporary_connection_error
    )
    assert service.run("admin")["pending"] == 1
    reopened = LocalReceiptStore(store.path)
    service = LocalReceiptSynchronizer(
        reopened, publish, app.is_temporary_connection_error
    )
    assert service.run("admin")["confirmed"] == 1
    assert calls[0] == calls[1]
    with app.db_connect() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM recibos WHERE local_request_id=%s",
            (identity,),
        ).fetchone()
        assert row["count"] == 1
        items = connection.execute("SELECT cantidad,total FROM recibo_items").fetchall()
        assert len(items) == 1
        assert (items[0]["cantidad"], float(items[0]["total"])) == (2, 10)
    row = reopened.row_for_user(identity, "admin")
    assert row["state"] == "SYNCED"
    assert json.loads(row["payload_json"])["patient"] == job["patient"]
