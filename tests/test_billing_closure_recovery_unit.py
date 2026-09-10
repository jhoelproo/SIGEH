from datetime import datetime, timezone

from billing_closure_recovery import (
    PENDING_RESTART_AT,
    can_recover_closures,
    closed_turn_attentions,
    closure_from_interval,
    pending_central_closures,
)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.params = ()

    def execute(self, sql, params=()):
        self.sql = str(sql)
        self.params = tuple(params)
        return _Rows(self.rows)


def test_recovery_requires_a_synchronized_admission_station():
    valid = {
        "offline": False,
        "pending_sync_count": 0,
        "role": "SECONDARY",
        "local_device_id": "PC-2",
    }

    assert can_recover_closures(valid)
    for change in (
        {"offline": True},
        {"pending_sync_count": 1},
        {"role": "OBSERVER"},
        {"local_device_id": ""},
    ):
        assert not can_recover_closures({**valid, **change})


def test_interval_is_converted_to_a_stable_hospital_closure():
    row = {
        "operational_source_id": "source",
        "turn_id": 77,
        "started_at": datetime(2026, 9, 8, 12, tzinfo=timezone.utc),
        "ended_at": datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
        "nominal_ends_at": datetime(2026, 9, 9, 12, tzinfo=timezone.utc),
        "active_username": "original",
        "closed_by": "closer",
        "operational_session_id": "session",
    }

    first = closure_from_interval(row)
    second = closure_from_interval(row)

    assert first.event_uuid == second.event_uuid
    assert first.turn_id == 77
    assert first.started_at == "2026-09-08 08:00:00"
    assert first.closed_at == "2026-09-09 08:00:00"
    assert first.actor == "closer"


def test_recovery_queries_are_bounded_and_keep_patient_data_in_memory():
    interval_connection = _Connection([{"turn_id": 77}])
    assert pending_central_closures(interval_connection) == [{"turn_id": 77}]
    assert interval_connection.params == (PENDING_RESTART_AT,)
    assert "PRIMARY_USER_HANDOFF" in interval_connection.sql
    assert "LIMIT 50" in interval_connection.sql

    attention_connection = _Connection([{"attention_id": 10}])
    event = type("Closure", (), {"source_instance_id": "source", "turn_id": 77})()
    assert closed_turn_attentions(attention_connection, event) == [{"attention_id": 10}]
    assert attention_connection.params == ("source", 77)
    assert "admission_quick_list_dismissals" in attention_connection.sql
