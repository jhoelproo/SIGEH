import json
from unittest.mock import Mock
import uuid

import pytest

from admission_hybrid import is_temporary_connection_error
from local_receipts import LocalReceiptStore, LocalReceiptSynchronizer, receipt_command
from network_retry import NetworkRetryGate


def receipt_job():
    return {
        "editing_id": None,
        "patient": "PACIENTE FICTICIO",
        "date_str": "2026-10-06",
        "dx_raw": "Diagnóstico ficticio",
        "ars_name": "ARS DEMO",
        "coverage": "ASEGURADO",
        "sala": 500,
        "total_general": 510,
        "grouped": [("Materiales", [("GUANTES", 5, 2, 10, "Materiales")])],
        "current_user": {
            "username": "demo",
            "full_name": "DEMO",
            "role": "administrador",
            "password": "never-persist",
        },
    }


def test_command_excludes_credentials_and_preserves_amounts():
    serialized, digest = receipt_command(receipt_job())
    assert "password" not in serialized and "never-persist" not in serialized
    assert json.loads(serialized)["total_general"] == 510
    assert len(digest) == 64


@pytest.mark.parametrize(
    "key,value",
    [
        ("editing_id", 1),
        ("patient", ""),
        ("date_str", "wrong"),
        ("sala", -1),
        ("total_general", 511),
        ("total_general", float("nan")),
    ],
)
def test_invalid_commands_are_not_persisted(tmp_path, key, value):
    store = LocalReceiptStore(tmp_path / "receipts.db")
    job = receipt_job()
    job[key] = value
    with pytest.raises(ValueError):
        store.enqueue(job)
    assert store.rows("demo") == []


@pytest.mark.parametrize(
    "item",
    [
        ("", 5, 2, 10, "Materiales"),
        ("GUANTES", 5, 0, 0, "Materiales"),
        ("GUANTES", 5, 1.5, 7.5, "Materiales"),
        ("GUANTES", 5, 2, 11, "Materiales"),
    ],
)
def test_invalid_items(item):
    job = receipt_job()
    job["grouped"] = [("Materiales", [item])]
    with pytest.raises(ValueError):
        receipt_command(job)


def test_empty_or_zero_command_and_missing_actor():
    job = receipt_job()
    job.update(sala=0, total_general=0, grouped=[])
    with pytest.raises(ValueError):
        receipt_command(job)
    job = receipt_job()
    job["current_user"]["username"] = ""
    with pytest.raises(ValueError):
        receipt_command(job)


def test_reopen_keeps_command_and_same_request_is_idempotent(tmp_path):
    path = tmp_path / "receipts.db"
    store = LocalReceiptStore(path)
    identity = store.enqueue(receipt_job())
    assert uuid.UUID(identity)
    reopened = LocalReceiptStore(path)
    assert reopened.enqueue(receipt_job(), identity) == identity
    assert len(reopened.rows("demo", pending_only=True)) == 1
    assert reopened.rows("another-user") == []
    different = receipt_job()
    different["patient"] = "OTRO PACIENTE"
    with pytest.raises(ValueError):
        reopened.enqueue(different, identity)
    assert (
        json.loads(reopened.rows("demo")[0]["payload_json"])["patient"]
        == "PACIENTE FICTICIO"
    )


@pytest.mark.parametrize("receipt_id,number", [(0, 1), (1, 0), (-1, 1)])
def test_invalid_confirmation_keeps_pending(tmp_path, receipt_id, number):
    store = LocalReceiptStore(tmp_path / "receipts.db")
    identity = store.enqueue(receipt_job())
    with pytest.raises(ValueError):
        store.confirm(identity, receipt_id, number)
    assert store.rows("demo")[0]["state"] == "PENDING"


def test_lost_confirmation_is_replayed_without_duplicate_and_backoff(tmp_path):
    path = tmp_path / "receipts.db"
    store = LocalReceiptStore(path)
    identity = store.enqueue(receipt_job())
    central = {}
    calls = []

    def publish(_job, request_id, digest):
        calls.append(request_id)
        central.setdefault(request_id, (25, 992200, digest))
        if len(calls) == 1:
            raise ConnectionError("server closed the connection")
        return central[request_id][:2]

    now = [0.0]
    gate = NetworkRetryGate(lambda: now[0])
    service = LocalReceiptSynchronizer(
        store, publish, is_temporary_connection_error, retry_gate=gate
    )
    assert service.run("demo")["pending"] == 1
    assert service.run("demo")["deferred"] == 1
    assert len(calls) == 1
    now[0] = 10
    reopened = LocalReceiptStore(path)
    service = LocalReceiptSynchronizer(
        reopened, publish, is_temporary_connection_error, retry_gate=gate
    )
    assert service.run("demo")["confirmed"] == 1
    assert service.run("demo")["confirmed"] == 0
    assert len(central) == 1 and calls == [identity, identity]
    row = reopened.rows("demo")[0]
    assert (row["central_id"], row["central_number"], row["state"]) == (
        25,
        992200,
        "SYNCED",
    )


def test_business_failure_requires_review_and_never_deletes_command(tmp_path):
    store = LocalReceiptStore(tmp_path / "receipts.db")
    identity = store.enqueue(receipt_job())
    publish = Mock(side_effect=PermissionError("private error message"))
    service = LocalReceiptSynchronizer(store, publish, is_temporary_connection_error)
    assert service.run("demo")["review"] == 1
    assert service.run("demo")["review"] == 0
    row = store.rows("demo")[0]
    assert row["request_id"] == identity and row["error_type"] == "PermissionError"
    assert row["state"] == "REVIEW" and "private error message" not in str(row)


def test_local_cache_and_receipt_access_survive_reopen(tmp_path):
    path = tmp_path / "receipts.db"
    store = LocalReceiptStore(path)
    identity = store.enqueue(receipt_job())
    store.cache("tariff", {"price": 500})
    store.cache("tariff", {"price": 600})
    reopened = LocalReceiptStore(path)
    assert reopened.cached("tariff") == {"price": 600}
    assert reopened.cached("missing", {}) == {}
    assert reopened.row_for_user(identity, "demo")["request_id"] == identity
    with pytest.raises(PermissionError):
        reopened.row_for_user(identity, "other")
    with pytest.raises(ValueError):
        reopened.row_for_user("wrong", "demo")


def test_changed_payload_requires_review_without_central_write(tmp_path):
    import sqlite3

    store = LocalReceiptStore(tmp_path / "receipts.db")
    identity = store.enqueue(receipt_job())
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE local_receipts SET payload_json=? WHERE request_id=?",
            ("{}", identity),
        )
    publish = Mock()
    service = LocalReceiptSynchronizer(store, publish, is_temporary_connection_error)
    assert service.run("demo")["review"] == 1
    publish.assert_not_called()
    assert store.rows("demo")[0]["state"] == "REVIEW"


def test_batch_limit_preserves_oldest_pending_and_drains_without_loss(tmp_path):
    store = LocalReceiptStore(tmp_path / "receipts.db")
    identities = [store.enqueue(receipt_job()) for _ in range(101)]
    assert len(store.rows("demo", pending_only=True)) == 100
    assert store.rows("demo", pending_only=True)[0]["request_id"] == identities[0]
    assert store.rows("demo")[0]["request_id"] == identities[-1]
    number = iter(range(1, 102))
    published = []

    def publish(_job, identity, _digest):
        published.append(identity)
        index = next(number)
        return index, index

    service = LocalReceiptSynchronizer(store, publish, is_temporary_connection_error)
    assert service.run("demo")["confirmed"] == 100
    assert service.run("demo")["confirmed"] == 1
    assert service.run("demo")["confirmed"] == 0
    assert published == identities
    assert store.rows("demo", pending_only=True) == []
