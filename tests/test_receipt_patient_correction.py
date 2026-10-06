"""Identity, concurrency and transaction boundaries without external services."""

from unittest.mock import Mock

import pytest

import receipt_patient_correction as correction


def test_optional_name_and_expected_name_guards():
    assert correction.optional_patient_name(None) is None
    assert correction.optional_patient_name(" NEW  NAME ") == "NEW NAME"
    correction.require_expected_name({"nombre": "NEW"}, None)
    correction.require_expected_name({"nombre": "NEW"}, "NEW")
    with pytest.raises(ValueError, match="otra pantalla"):
        correction.require_expected_name({"nombre": "NEW"}, "OLD")


class Connection:
    def __init__(self, *rows):
        self.rows = iter(rows)
        self.calls = []

    def execute(self, sql, parameters):
        self.calls.append((sql, parameters))
        return self

    def fetchone(self):
        return next(self.rows)


@pytest.fixture
def receipt():
    return {
        "id": 1,
        "nombre": "OLD",
        "fecha": "2026-10-05",
        "admission_global_attention_id": "ATTENTION",
        "admission_atencion_id": 7,
        "admission_source_instance_id": "SOURCE",
    }


@pytest.fixture
def attention():
    return {
        "global_attention_id": "ATTENTION",
        "global_patient_id": "PATIENT",
        "patient_name": "OLD",
        "attention_id": 7,
        "source_instance_id": "SOURCE",
        "is_deleted": False,
    }


@pytest.mark.parametrize("key", ["KEY", ""])
def test_duplicate_guard_uses_explicit_identity_and_stores_result(receipt, key):
    connection = Connection()
    guard = Mock(return_value=key)
    correction.update_name_key(connection, receipt, "NEW", "admin", guard)
    arguments = guard.call_args.kwargs
    assert arguments["nombre"] == "NEW"
    assert arguments["fecha"] == receipt["fecha"]
    assert arguments["attention_id"] == 7
    assert arguments["source_instance_id"] == "SOURCE"
    assert arguments["exclude_id"] == 1
    assert connection.calls[0][1] == (key or None, 1)


def test_unchanged_name_does_not_change_duplicate_key(receipt):
    connection = Connection()
    guard = Mock()
    correction.update_name_key(connection, receipt, "OLD", "admin", guard)
    guard.assert_not_called()
    assert not connection.calls
    correction.write_name_key(connection, receipt, "OLD", "KEY")
    assert not connection.calls


def test_duplicate_guard_failure_stops_before_write(receipt):
    connection = Connection()
    guard = Mock(side_effect=ValueError("duplicate"))
    with pytest.raises(ValueError, match="duplicate"):
        correction.update_name_key(connection, receipt, "NEW", "admin", guard)
    assert not connection.calls


def test_absent_name_does_not_query_admission():
    connection = Connection()
    assert correction.lock_name_attention(connection, 1, None) is None
    assert not connection.calls


@pytest.mark.parametrize("case", ["absent", "unchanged", "unlinked"])
def test_no_attention_lock_when_correction_does_not_need_one(receipt, case):
    row = dict(receipt)
    if case == "absent":
        row = None
    elif case == "unlinked":
        row.update(admission_global_attention_id=None, admission_atencion_id=None)
    connection = Connection(row)
    name = "OLD" if case == "unchanged" else "NEW"
    assert correction.lock_name_attention(connection, 1, name) is None
    assert len(connection.calls) == 1


@pytest.mark.parametrize("projection", [None, {"global_attention_id": None}])
def test_missing_central_identity_requires_admission_correction(receipt, projection):
    connection = Connection(receipt, projection)
    with pytest.raises(ValueError, match="identidad"):
        correction.lock_name_attention(connection, 1, "NEW")
    assert len(connection.calls) == 2


@pytest.mark.parametrize(
    "global_id,source", [("ATTENTION", "SOURCE"), (None, "SOURCE"), (None, None)]
)
def test_attention_locks_follow_sync_then_billing_then_row_order(
    receipt, attention, global_id, source
):
    receipt.update(
        admission_global_attention_id=global_id, admission_source_instance_id=source
    )
    attention["source_instance_id"] = source or "LEGACY"
    connection = Connection(receipt, attention, attention)
    assert correction.lock_name_attention(connection, 1, "NEW") == attention
    assert connection.calls[1][1] == (
        global_id or "",
        global_id or "",
        global_id or "",
        7,
        source or "LEGACY",
    )
    assert connection.calls[2][1] == ("admission-sync:attention:ATTENTION",)
    assert connection.calls[3][1] == (f"admission-billing:{source or 'LEGACY'}:7",)
    assert "FOR UPDATE" in connection.calls[4][0]


@pytest.mark.parametrize("case", ["unchanged", "unlinked"])
def test_no_linked_name_update_for_unchanged_or_unlinked_receipt(
    receipt, attention, case
):
    if case == "unlinked":
        receipt.update(admission_global_attention_id=None, admission_atencion_id=None)
    connection = Connection()
    assert (
        correction.correct_linked_name(
            connection, receipt, attention, "OLD" if case == "unchanged" else "NEW", {}
        )
        is None
    )
    assert not connection.calls


@pytest.mark.parametrize(
    "case",
    [
        "absent",
        "patient_absent",
        "deleted",
        "global_changed",
        "local_changed",
        "source_changed",
    ],
)
def test_changed_or_deleted_link_is_rejected_before_writes(receipt, attention, case):
    if case == "absent":
        attention = None
    elif case == "patient_absent":
        attention["global_patient_id"] = None
    elif case == "deleted":
        attention["is_deleted"] = True
    elif case == "global_changed":
        attention["global_attention_id"] = "OTHER"
    else:
        receipt["admission_global_attention_id"] = None
        attention[
            "attention_id" if case == "local_changed" else "source_instance_id"
        ] = "OTHER"
    connection = Connection()
    with pytest.raises(ValueError):
        correction.correct_linked_name(connection, receipt, attention, "NEW", {})
    assert not connection.calls


@pytest.mark.parametrize("case", ["absent", "deleted", "stale"])
def test_deleted_or_newer_patient_is_not_overwritten(receipt, attention, case):
    patient = (
        None
        if case == "absent"
        else {
            "patient_name": "SOMEONE ELSE" if case == "stale" else "OLD",
            "is_deleted": case == "deleted",
        }
    )
    connection = Connection(patient)
    with pytest.raises(ValueError):
        correction.correct_linked_name(connection, receipt, attention, "NEW", {})
    assert len(connection.calls) == 1
    assert "FOR UPDATE" in connection.calls[0][0]


@pytest.mark.parametrize("global_id", ["ATTENTION", None])
@pytest.mark.parametrize("actor", [{"username": "admin", "role": "Administrador"}, {}])
def test_name_patch_preserves_other_demographics_and_records_actor(
    monkeypatch, receipt, attention, global_id, actor
):
    receipt["admission_global_attention_id"] = global_id
    patient = {"patient_name": "OLD", "is_deleted": False}
    updated = {
        "global_patient_id": "PATIENT",
        "server_revision": 2,
        "updated_at": "TIME",
    }
    snapshot = {"snapshot_hash": "HASH", "source_updated_at": "TIME"}
    connection = Connection(patient, updated, snapshot)
    patch = Mock()
    event = Mock()
    monkeypatch.setattr("admission_demographics._correct_snapshot", patch)
    monkeypatch.setattr(
        "patient_directory.CentralPatientDirectoryRepository._insert_event", event
    )
    assert (
        correction.correct_linked_name(connection, receipt, attention, "NEW", actor)
        == snapshot
    )
    assert patch.call_args.args[2] == {
        "global_patient_id": "PATIENT",
        "server_revision": 2,
        "nombre": "NEW",
    }
    assert event.call_args.kwargs["audit"]["actor_user"] == actor.get(
        "username", "Sistema"
    )
    assert connection.calls[-1][1] == ("HASH", "TIME", 1)
