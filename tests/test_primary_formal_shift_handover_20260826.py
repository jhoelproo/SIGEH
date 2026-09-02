from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
from types import SimpleNamespace

import pytest

from admission_hybrid import (
    AdmissionWriteGuard,
    AdmissionWriteBlocked,
    DeviceAttachment,
    OperationalSession,
    OperationalSessionService,
    OperationalState,
    PrimaryTransitionResult,
    StationRole,
    SAME_USER_HANDOFF_MESSAGE,
    can_change_admission_turn,
    evaluate_admission_access,
    is_primary_shift_handover,
)
from admission_v15_adapter import _HybridAdmissionRuntime
from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App


OLD_USER = {"id": 10, "username": "aux_anterior", "role": "auxiliar"}
NEW_USER = {
    "id": 11,
    "username": "aux_nuevo",
    "full_name": "AUXILIAR NUEVO",
    "role": "auxiliar",
}


def _state(*, role=StationRole.PRIMARY, turn_id=500):
    return {
        "active_user_id": "10",
        "active_username": "aux_anterior",
        "turn_id": turn_id,
        "device_role": role,
        "status": "ACTIVE",
        "connection_state": "CONNECTED",
        "offline": False,
    }


def test_different_auxiliary_on_primary_can_only_start_explicit_handover():
    state = _state()

    assert is_primary_shift_handover(NEW_USER, state, StationRole.PRIMARY)
    assert can_change_admission_turn(NEW_USER, state, StationRole.PRIMARY)
    assert not is_primary_shift_handover(OLD_USER, state, StationRole.PRIMARY)


def test_access_snapshot_keeps_turn_identity_for_incoming_primary_operator():
    now = datetime.now(timezone.utc)
    state = OperationalState(
        operational_session_id="op-access",
        generation=7,
        active_user_id="10",
        active_username="aux_anterior",
        active_user_display_name="AUXILIAR ANTERIOR",
        turn_id=500,
        primary_device_id="PC-PRIMARY",
        primary_login_session_id="login-new",
        local_device_id="PC-PRIMARY",
        local_login_session_id="login-new",
        device_role=StationRole.PRIMARY,
        device_attached=True,
        user_matches_operational=False,
        write_allowed=False,
        turn_code="8AM_8PM",
        turn_started_at=now,
        turn_ends_at=now + timedelta(hours=12),
    )

    decision = evaluate_admission_access(NEW_USER, state)

    assert can_change_admission_turn(NEW_USER, state, StationRole.PRIMARY)
    assert decision.write_allowed is False
    assert decision.can_change_turn is True


def test_turn_button_uses_live_runtime_policy_not_stale_snapshot_flag():
    app = object.__new__(App)
    app._turn_change_in_progress = False
    app._turn_change_committing = False
    runtime = SimpleNamespace(
        offline=False,
        device_id="PC-PRIMARY",
        state=lambda: {
            "role": "PRIMARY",
            "can_change_turn": False,
            "primary_device_id": "PC-PRIMARY",
        },
        can_change_admission_turn=lambda: True,
        require_primary_turn_change=lambda: True,
    )
    app.db = SimpleNamespace(_runtime=runtime)

    allowed, reason_code, message = app.can_change_admission_turn()

    assert allowed is True
    assert reason_code == "ALLOWED"
    assert message == ""


def test_turn_button_rejects_live_policy_denial_on_primary():
    app = object.__new__(App)
    app._turn_change_in_progress = False
    app._turn_change_committing = False
    runtime = SimpleNamespace(
        offline=False,
        device_id="PC-PRIMARY",
        state=lambda: {
            "role": "PRIMARY",
            "can_change_turn": True,
            "primary_device_id": "PC-PRIMARY",
        },
        can_change_admission_turn=lambda: False,
    )
    app.db = SimpleNamespace(_runtime=runtime)

    allowed, reason_code, _message = app.can_change_admission_turn()

    assert allowed is False
    assert reason_code == "ROLE_NOT_ALLOWED"


def test_turn_guard_allows_handover_even_though_patient_writes_remain_read_only():
    now = datetime.now(timezone.utc)
    session = OperationalSession(
        operational_session_id="op-guard",
        active_username="aux_anterior",
        active_user_id="10",
        primary_device_id="PC-PRIMARY",
        primary_login_session_id="login-new",
        turn_id=500,
        operational_source_id="source-1",
        status="ACTIVE",
        generation=7,
        operational_revision=7,
        primary_last_seen=now.isoformat(),
        updated_at=now.isoformat(),
    )
    guard = AdmissionWriteGuard()
    arguments = {
        "login_user": "aux_nuevo",
        "login_user_id": 11,
        "login_role": "auxiliar",
        "device_id": "PC-PRIMARY",
        "session": session,
        "generation": 7,
        "role": StationRole.PRIMARY,
    }

    assert guard.can_write_admission(**arguments).allowed is False
    decision = guard.require_primary_turn_change(**arguments)

    assert decision.allowed is True
    assert decision.code == "PRIMARY_SHIFT_HANDOVER_ALLOWED"


def test_secondary_audit_and_unconfigured_session_cannot_start_handover():
    assert not can_change_admission_turn(
        NEW_USER, _state(role=StationRole.SECONDARY), StationRole.SECONDARY
    )
    assert not can_change_admission_turn(
        {"id": 12, "username": "auditor", "role": "facturador de auditoria"},
        _state(),
        StationRole.PRIMARY,
    )
    assert not is_primary_shift_handover(
        NEW_USER,
        {**_state(turn_id=None), "active_user_id": "", "active_username": ""},
        StationRole.PRIMARY,
    )


class _Result:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _HandoverDatabase:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.session = {
            "operational_session_id": "op-1",
            "active_username": "aux_anterior",
            "active_user_id": "10",
            "active_user_display_name": "AUXILIAR ANTERIOR",
            "primary_device_id": "PC-PRIMARY",
            "primary_login_session_id": "login-new",
            "turn_id": 500,
            "turn_code": "8AM_8PM",
            "operational_source_id": "source-1",
            "status": "ACTIVE",
            "generation": 7,
            "operational_revision": 7,
            "lease_generation": 2,
            "primary_last_seen": now.isoformat(),
            "updated_at": now.isoformat(),
            "turn_started_at": now.isoformat(),
            "turn_ends_at": (now + timedelta(hours=12)).isoformat(),
        }
        self.devices = {
            "PC-PRIMARY": {
                "device_id": "PC-PRIMARY",
                "login_session_id": "login-new",
                "station_role": "PRIMARY",
                "detached_at": None,
            },
            "PC-OLD-SECONDARY": {
                "device_id": "PC-OLD-SECONDARY",
                "login_session_id": "login-old-secondary",
                "station_role": "SECONDARY",
                "detached_at": None,
            },
            "PC-OTHER-SECONDARY": {
                "device_id": "PC-OTHER-SECONDARY",
                "login_session_id": "login-other-secondary",
                "station_role": "SECONDARY",
                "detached_at": None,
            },
        }
        self.active_sessions = {
            "login-new": {"username": "aux_nuevo", "is_active": True},
            "login-old-secondary": {"username": "aux_anterior", "is_active": True},
            "login-other-secondary": {"username": "otro_aux", "is_active": True},
        }
        self.audit = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        upper = sql.upper()
        params = tuple(params or ())
        if "PG_ADVISORY_XACT_LOCK" in upper:
            return _Result((True,))
        if "TO_REGCLASS('PUBLIC.ACTIVE_SESSIONS')" in upper:
            return _Result(("active_sessions",))
        if "SELECT OPERATIONAL_SESSION_ID,DETAILS_JSON" in upper:
            return _Result(None)
        if upper.startswith("SELECT * FROM ADMISSION_OPERATIONAL_SESSIONS"):
            return _Result(deepcopy(self.session))
        if "SELECT STATION_ROLE,LOGIN_SESSION_ID" in upper:
            return _Result(deepcopy(self.devices[str(params[1])]))
        if upper.startswith("SELECT GREATEST("):
            return _Result({"next_turn_id": 501})
        if "LEFT JOIN ACTIVE_SESSIONS" in upper:
            rows = []
            for device_id, device in self.devices.items():
                if device_id == str(params[1]) or device.get("detached_at") is not None:
                    continue
                item = deepcopy(device)
                item["login_username"] = self.active_sessions[
                    device["login_session_id"]
                ]["username"]
                rows.append(item)
            return _Result(rows=rows)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_SESSIONS SET ACTIVE_USERNAME"):
            (
                username,
                user_id,
                display_name,
                login_id,
                turn_id,
                turn_code,
                generation,
                _duration_hours,
                _changed_by,
                _reason,
                _session_id,
            ) = params
            self.session.update(
                {
                    "active_username": str(username),
                    "active_user_id": str(user_id),
                    "active_user_display_name": str(display_name),
                    "primary_login_session_id": str(login_id),
                    "turn_id": int(turn_id),
                    "turn_code": str(turn_code),
                    "generation": int(generation),
                    "operational_revision": self.session["operational_revision"] + 1,
                }
            )
            return _Result(rowcount=1)
        if "ADMISSION_OPERATIONAL_TURN_INTERVALS" in upper:
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_DEVICES SET STATION_ROLE='PRIMARY'"):
            self.devices[str(params[2])]["login_session_id"] = str(params[0])
            return _Result(rowcount=1)
        if "INVALIDATED_REASON='PRIMARY_USER_CHANGED'" in upper:
            generation, username, _session_id, device_ids = params
            for device_id in device_ids:
                self.devices[str(device_id)].update(
                    {
                        "detached_at": "now",
                        "invalidated_reason": "PRIMARY_USER_CHANGED",
                        "invalidated_generation": int(generation),
                        "new_active_username": str(username),
                    }
                )
            return _Result(rowcount=len(device_ids))
        if upper.startswith("UPDATE ACTIVE_SESSIONS SET IS_ACTIVE=0"):
            session_ids, excluded = params
            for session_id in session_ids:
                if session_id != excluded:
                    self.active_sessions[str(session_id)]["is_active"] = False
            return _Result(rowcount=len(session_ids))
        if upper.startswith("INSERT INTO ADMISSION_OPERATIONAL_AUDIT"):
            self.audit.append({"event": params[1], "details": params[5]})
            return _Result(rowcount=1)
        raise AssertionError(f"SQL no simulado: {sql} | {params}")


def test_formal_handover_changes_turn_and_rep_but_only_logs_out_old_rep_secondary():
    database = _HandoverDatabase()
    service = OperationalSessionService(lambda: database)

    result = service.transition_primary_user(
        operational_session_id="op-1",
        primary_device_id="PC-PRIMARY",
        new_login_session_id="login-new",
        new_user=NEW_USER,
        new_turn_id=999,
        new_turn_code="8PM_8AM",
        expected_generation=7,
        transition_id="2646a4a5-91ec-44db-9a8c-a6af2d06f18f",
        invalidate_secondaries=True,
        invalidate_only_previous_user_secondaries=True,
        allocate_central_turn_id=True,
    )

    assert result.old_turn_id == 500
    assert result.new_turn_id == 501
    assert result.old_username == "aux_anterior"
    assert result.new_username == "aux_nuevo"
    assert result.operational_session.primary_device_id == "PC-PRIMARY"
    assert result.operational_session.generation == 8
    assert result.invalidated_login_session_ids == ("login-old-secondary",)
    assert database.devices["PC-OLD-SECONDARY"]["detached_at"] == "now"
    assert database.active_sessions["login-old-secondary"]["is_active"] is False
    assert database.devices["PC-OTHER-SECONDARY"]["detached_at"] is None
    assert database.active_sessions["login-other-secondary"]["is_active"] is True
    assert any(item["event"] == "OPERATIONAL_GENERATION_CHANGED" for item in database.audit)
    assert any(item["event"] == "TURN_HANDOFF_REQUESTED" for item in database.audit)
    assert any(item["event"] == "TURN_HANDOFF_COMMITTED" for item in database.audit)
    identity_event = next(
        item for item in database.audit
        if item["event"] == "OPERATIONAL_IDENTITY_CHANGED"
    )
    assert '"trigger": "USER_REQUESTED_HANDOFF"' in identity_event["details"]


def test_same_user_handoff_is_absolute_noop_before_allocating_turn_id():
    database = _HandoverDatabase()
    service = OperationalSessionService(lambda: database)
    allocator_calls = []
    service._allocate_next_central_turn_id = lambda _connection: allocator_calls.append(True) or 501
    before_session = deepcopy(database.session)
    before_devices = deepcopy(database.devices)
    before_audit = deepcopy(database.audit)

    with pytest.raises(AdmissionWriteBlocked) as error:
        service.transition_primary_user(
            operational_session_id="op-1",
            primary_device_id="PC-PRIMARY",
            new_login_session_id="login-new",
            new_user=OLD_USER,
            new_turn_id=None,
            expected_generation=7,
            transition_id="6646a4a5-91ec-44db-9a8c-a6af2d06f18f",
            allocate_central_turn_id=True,
        )

    assert str(error.value) == SAME_USER_HANDOFF_MESSAGE
    assert allocator_calls == []
    assert database.session == before_session
    assert database.devices == before_devices
    assert database.audit == before_audit


class _RuntimeTransitionService:
    def __init__(self, changed_session):
        self.changed_session = changed_session
        self.handover_calls = []
        self.turn_only_calls = []

    def transition_primary_user(self, **kwargs):
        self.handover_calls.append(kwargs)
        return PrimaryTransitionResult(
            operational_session=self.changed_session,
            transition_id=kwargs["transition_id"],
            invalidated_login_session_ids=("login-old-secondary",),
            committed=True,
            old_turn_id=500,
            new_turn_id=501,
            old_generation=7,
            new_generation=8,
            old_user_id="10",
            new_user_id="11",
            old_username="aux_anterior",
            new_username="aux_nuevo",
        )

    def admin_set_admission_turn(self, **kwargs):
        self.turn_only_calls.append(kwargs)
        return PrimaryTransitionResult(
            operational_session=self.changed_session,
            transition_id=kwargs["transition_id"],
            committed=True,
            old_turn_id=500,
            new_turn_id=501,
            old_generation=7,
            new_generation=8,
            old_user_id="10",
            new_user_id="10",
            old_username="aux_anterior",
            new_username="aux_anterior",
        )


def _runtime_for_handover():
    now = datetime.now(timezone.utc)
    current = OperationalSession(
        operational_session_id="op-1",
        active_username="aux_anterior",
        active_user_id="10",
        primary_device_id="PC-PRIMARY",
        primary_login_session_id="login-new",
        turn_id=500,
        operational_source_id="source-1",
        status="ACTIVE",
        generation=7,
        operational_revision=7,
        lease_generation=2,
        primary_last_seen=now.isoformat(),
        updated_at=now.isoformat(),
        active_user_display_name="AUXILIAR ANTERIOR",
        turn_code="8AM_8PM",
        turn_started_at=now.isoformat(),
        turn_ends_at=(now + timedelta(hours=12)).isoformat(),
    )
    changed = replace(
        current,
        active_username="aux_nuevo",
        active_user_id="11",
        active_user_display_name="AUXILIAR NUEVO",
        turn_id=501,
        turn_code="8PM_8AM",
        generation=8,
        operational_revision=8,
    )
    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime.StationRole = StationRole
    runtime.host = SimpleNamespace(
        user=NEW_USER,
        device_id="PC-PRIMARY",
        session_id="login-new",
        current_shift={},
    )
    runtime.attachment = DeviceAttachment(
        current, StationRole.PRIMARY, False, "Relevo pendiente"
    )
    runtime._operational_state = OperationalState(
        operational_session_id="op-1",
        generation=7,
        active_user_id="10",
        active_username="aux_anterior",
        active_user_display_name="AUXILIAR ANTERIOR",
        turn_id=500,
        primary_device_id="PC-PRIMARY",
        primary_login_session_id="login-new",
        local_device_id="PC-PRIMARY",
        local_login_session_id="login-new",
        device_role=StationRole.PRIMARY,
        device_attached=True,
        user_matches_operational=False,
        write_allowed=False,
        turn_code="8AM_8PM",
        turn_started_at=now,
        turn_ends_at=now + timedelta(hours=12),
    )
    runtime.offline = False
    runtime.offline_lease_valid = False
    runtime._pending_transition_id = ""
    runtime._last_transition_result = None
    runtime.store = None
    runtime.logger = logging.getLogger("test.formal-handover-runtime")
    runtime.session_service = _RuntimeTransitionService(changed)
    runtime.require_primary_turn_change = lambda: True
    runtime.applied_states = []
    runtime.apply_operational_snapshot = (
        lambda state, **_kwargs: runtime.applied_states.append(state) or True
    )
    runtime.refresh_operational_state = lambda **_kwargs: {}
    return runtime


def test_adapter_routes_normal_different_user_turn_to_atomic_handover():
    runtime = _runtime_for_handover()

    runtime.change_primary_turn(999, shift_metadata={"turno_codigo": "8PM_8AM"})

    assert len(runtime.session_service.handover_calls) == 1
    assert runtime.session_service.turn_only_calls == []
    call = runtime.session_service.handover_calls[0]
    assert call["allocate_central_turn_id"] is True
    assert call["invalidate_only_previous_user_secondaries"] is True
    assert call["new_user"]["username"] == "aux_nuevo"
    committed_state = runtime.applied_states[-1]
    assert committed_state.turn_id == 501
    assert committed_state.user_matches_operational is True
    assert committed_state.write_allowed is True
    assert committed_state.can_change_turn is True
    assert committed_state.can_generate_attention is True


def test_administrative_schedule_override_stays_turn_only_and_keeps_rep_flow_separate():
    runtime = _runtime_for_handover()

    runtime.change_primary_turn(
        999,
        shift_metadata={
            "turno_codigo": "8PM_8AM",
            "administrative_override": True,
            "override_reason": "Corrección de horario",
        },
    )

    assert runtime.session_service.handover_calls == []
    assert len(runtime.session_service.turn_only_calls) == 1
    assert runtime.session_service.turn_only_calls[0]["administrative_override"] is True


def test_adapter_rejects_normal_same_user_turn_before_any_service_call():
    runtime = _runtime_for_handover()
    runtime.host.user = OLD_USER
    runtime._operational_state = replace(
        runtime._operational_state,
        user_matches_operational=True,
        write_allowed=True,
    )

    with pytest.raises(AdmissionWriteBlocked) as error:
        runtime.perform_explicit_turn_handoff(
            shift_metadata={"turno_codigo": "8PM_8AM"}
        )

    assert str(error.value) == SAME_USER_HANDOFF_MESSAGE
    assert runtime.session_service.handover_calls == []
    assert runtime.session_service.turn_only_calls == []


def test_invalidated_old_representative_secondary_requests_immediate_logout():
    runtime = _runtime_for_handover()
    runtime._operational_state = replace(
        runtime._operational_state,
        device_role=StationRole.DETACHED,
        device_attached=False,
        invalidated_reason="PRIMARY_USER_CHANGED",
        write_allowed=False,
    )
    runtime.status_message = "Relevo confirmado"
    runtime._pending_sync_count = 0

    state = runtime.state()

    assert state["force_logout_required"] is True
