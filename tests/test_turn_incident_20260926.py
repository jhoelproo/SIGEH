"""Regressions for the split shift and lock inversion seen on September 26."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

from admission_hybrid import OperationalSessionService
from admission_hybrid import AdmissionWriteBlocked
from tests.test_operational_primary_transition import (
    _strict_handoff_fixture,
    _admin_turn_override,
)


def test_heartbeat_locks_session_before_device_like_handoff():
    statements = []

    class Connection:
        def execute(self, query, params):
            statements.append(" ".join(query.split()))

    @contextmanager
    def connect():
        yield Connection()

    OperationalSessionService(connect).heartbeat(
        operational_session_id="shift",
        device_id="station",
    )
    assert "admission_operational_sessions" in statements[0]
    assert "admission_operational_devices" in statements[1]


def test_schedule_correction_must_not_allocate_another_shift(monkeypatch):
    database, service, primary = _strict_handoff_fixture()
    original = database.session.copy()

    def forbidden(_connection):
        raise AssertionError("A schedule correction must never allocate a shift")

    monkeypatch.setattr(service, "_allocate_next_central_turn_id", forbidden)
    result = _admin_turn_override(
        service,
        primary,
        new_turn_id=None,
        allocate_central_turn_id=True,
        new_turn_code="8AM_8PM",
    )
    assert result.new_turn_id == original["turn_id"]
    assert result.new_generation == original["generation"]
    assert result.operational_session.turn_started_at == original["turn_started_at"]


@pytest.mark.parametrize("code", ["", "invalid", "8AM_8AM", "8AM_8PM", "8PM_8AM"])
def test_schedule_codes_and_idempotent_replay(code):
    database, service, primary = _strict_handoff_fixture()
    if code in {"", "invalid"}:
        with pytest.raises(AdmissionWriteBlocked, match="horario"):
            _admin_turn_override(
                service,
                primary,
                new_turn_id=None,
                allocate_central_turn_id=True,
                new_turn_code=code,
            )
        return
    first = _admin_turn_override(
        service,
        primary,
        new_turn_id=None,
        allocate_central_turn_id=True,
        new_turn_code=code,
    )
    replay = _admin_turn_override(
        service,
        primary,
        new_turn_id=None,
        allocate_central_turn_id=True,
        new_turn_code=code,
    )
    assert replay.idempotent_replay
    assert first.new_turn_id == replay.new_turn_id == 10
    assert database.session["operational_revision"] == 2


@pytest.mark.parametrize("missing", ["turn_id", "turn_started_at"])
def test_correction_requires_original_turn_and_start(missing):
    database, service, primary = _strict_handoff_fixture()
    database.session[missing] = None
    with pytest.raises(AdmissionWriteBlocked, match="identidad e inicio"):
        _admin_turn_override(
            service,
            primary,
            new_turn_id=None,
            expected_previous_turn_id=None if missing == "turn_id" else 10,
            allocate_central_turn_id=True,
            new_turn_code="8AM_8AM",
        )


@pytest.mark.parametrize("count", [0, 2])
def test_schedule_correction_requires_one_open_interval(monkeypatch, count):
    database, service, primary = _strict_handoff_fixture()
    execute = database.execute

    def missing_interval(query, params=()):
        if "UPDATE admission_operational_turn_intervals SET nominal_ends_at" in query:
            return Mock(rowcount=count)
        return execute(query, params)

    monkeypatch.setattr(database, "execute", missing_interval)
    with pytest.raises(AdmissionWriteBlocked, match="intervalo abierto"):
        _admin_turn_override(
            service,
            primary,
            new_turn_id=None,
            allocate_central_turn_id=True,
            new_turn_code="8AM_8AM",
        )


def test_schedule_reservation_collision_uses_committed_recovery(monkeypatch):
    _database, service, primary = _strict_handoff_fixture()
    duplicate = {"transition_id": "existing"}
    expected = Mock(new_generation=1)
    monkeypatch.setattr(
        service, "_reserve_handoff_transition", Mock(return_value=duplicate)
    )
    recover = Mock(return_value=expected)
    monkeypatch.setattr(service, "_recover_committed_handoff", recover)
    result = _admin_turn_override(
        service,
        primary,
        new_turn_id=None,
        allocate_central_turn_id=True,
        new_turn_code="8AM_8AM",
    )
    assert result is expected
    assert recover.call_args.kwargs["transition_row"] == duplicate


def test_schedule_correction_cannot_confirm_a_missing_session(monkeypatch):
    _database, service, primary = _strict_handoff_fixture()
    monkeypatch.setattr(
        service,
        "_row_to_session",
        Mock(
            side_effect=[
                primary.operational_session,
                None,
            ]
        ),
    )
    with pytest.raises(AdmissionWriteBlocked, match="confirmar la corrección"):
        _admin_turn_override(
            service,
            primary,
            new_turn_id=None,
            allocate_central_turn_id=True,
            new_turn_code="8AM_8AM",
        )
