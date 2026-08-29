"""Regression coverage for the independent Admission operational identities."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from admission_hybrid import (
    ADMISSION_ROLE_ADMINISTRATOR,
    ADMISSION_ROLE_AUDIT,
    ADMISSION_ROLE_AUXILIARY,
    OperationalSessionService,
    StationRole,
    AdmissionWriteBlocked,
    can_change_admission_turn,
    evaluate_admission_access,
)
from tests.test_operational_primary_transition import _OperationalDB


class _OperationalModelDB(_OperationalDB):
    """Small transactional fake for the central-only operational commands."""

    def __init__(self):
        super().__init__()
        self.users = {
            "7": {
                "id": 7,
                "username": "admin",
                "full_name": "Administrador del sistema",
                "role": ADMISSION_ROLE_ADMINISTRATOR,
                "is_active": True,
            },
            "22": {
                "id": 22,
                "username": "aux-b",
                "full_name": "AUX B",
                "role": ADMISSION_ROLE_AUXILIARY,
                "is_active": True,
            },
            "23": {
                "id": 23,
                "username": "aux-c",
                "full_name": "AUX C",
                "role": ADMISSION_ROLE_AUXILIARY,
                "is_active": True,
            },
            "24": {
                "id": 24,
                "username": "audit-user",
                "full_name": "AUDITOR ACTIVO",
                "role": ADMISSION_ROLE_AUDIT,
                "is_active": True,
            },
        }

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        upper = sql.upper()
        params = tuple(params or ())
        if "FROM USERS" in upper and "FOR UPDATE" in upper:
            requested = str(params[0]).casefold()
            if "CAST(ID AS TEXT)" in upper:
                row = self.users.get(requested)
            else:
                row = next(
                    (
                        item
                        for item in self.users.values()
                        if str(item["username"]).casefold() == requested
                    ),
                    None,
                )
            return _Result(deepcopy(row) if row else None)
        if "SELECT STATION_ROLE FROM ADMISSION_OPERATIONAL_DEVICES" in upper:
            row = self.devices.get(str(params[1]))
            return _Result(
                (row.get("station_role"),)
                if row and row.get("detached_at") is None
                else None
            )
        if (
            upper.startswith("UPDATE ADMISSION_OPERATIONAL_SESSIONS SET ACTIVE_USERNAME")
            and "OPERATIONAL_REVISION=OPERATIONAL_REVISION+1" in upper
        ):
            username, user_id, display_name, _actor, _reason, _session_id = params
            self.session.update(
                {
                    "active_username": str(username),
                    "active_user_id": str(user_id),
                    "active_user_display_name": str(display_name),
                    "operational_revision": int(
                        self.session.get("operational_revision") or 1
                    )
                    + 1,
                }
            )
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_SESSIONS SET TURN_ID=%S"):
            (
                turn_id,
                turn_code,
                generation,
                _duration_hours,
                _actor,
                _reason,
                _session_id,
            ) = params
            self.session.update(
                {
                    "turn_id": turn_id,
                    "turn_code": str(turn_code or self.session.get("turn_code") or ""),
                    "generation": int(generation),
                    "operational_revision": int(
                        self.session.get("operational_revision") or 1
                    )
                    + 1,
                }
            )
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_DEVICES SET DETACHED_AT=NULL"):
            return _Result(rowcount=0)
        return super().execute(query, params)


class _Result:
    def __init__(self, row=None, rows=None, rowcount=0):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


def _configured_service():
    database = _OperationalModelDB()
    service = OperationalSessionService(lambda: database)
    initial = service.attach_device(
        login_username="admin",
        login_user_id=7,
        login_role=ADMISSION_ROLE_ADMINISTRATOR,
        device_id="DEVICE-A",
        login_session_id="LOGIN-A1",
    )
    database.session.update(
        {
            "active_user_id": "22",
            "active_username": "aux-b",
            "active_user_display_name": "AUX B",
            "turn_id": 100,
            "turn_code": "8AM_8PM",
            "turn_started_at": datetime.now(timezone.utc).isoformat(),
            "turn_ends_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
            "operational_revision": 3,
        }
    )
    database.active_sessions["LOGIN-A1"] = True
    database.active_session_users["LOGIN-A1"] = {
        "username": "admin",
        "device_id": "DEVICE-A",
        "user_id": "7",
        "role": ADMISSION_ROLE_ADMINISTRATOR,
    }
    return database, service, initial.operational_session.operational_session_id


def test_login_logout_and_primary_rebind_preserve_representative_and_turn():
    database, service, _session_id = _configured_service()
    before = deepcopy(database.session)

    assert service.release_login_session(
        device_id="DEVICE-A", login_session_id="LOGIN-A1", reason="LOGOUT"
    )
    assert database.session["active_user_id"] == before["active_user_id"]
    assert database.session["turn_id"] == before["turn_id"]

    attachment = service.rebind_login_session_to_operational_state(
        current_user={"id": 7, "username": "admin", "role": "administrador"},
        device_id="DEVICE-A",
        login_session_id="LOGIN-A2",
    )

    assert attachment.role is StationRole.PRIMARY
    assert attachment.writable is True
    assert attachment.operational_session.active_user_id == "22"
    assert attachment.operational_session.turn_id == 100
    assert attachment.operational_session.primary_login_session_id == "LOGIN-A2"
    assert attachment.operational_session.generation == before["generation"]


def test_representative_change_changes_only_representative_revision():
    database, service, session_id = _configured_service()
    before = deepcopy(database.session)

    changed = service.admin_set_admission_representative(
        actor_user_id=7,
        actor_username="admin",
        actor_role="administrador",
        actor_login_session_id="LOGIN-A1",
        actor_device_id="DEVICE-A",
        target_user={"id": 23, "username": "aux-c"},
        reason="Prueba aislada",
    )

    assert changed.operational_session_id == session_id
    assert changed.active_user_id == "23"
    assert changed.turn_id == before["turn_id"]
    assert changed.turn_code == before["turn_code"]
    assert changed.primary_device_id == before["primary_device_id"]
    assert changed.primary_login_session_id == before["primary_login_session_id"]
    assert changed.generation == before["generation"]
    assert changed.operational_revision == before["operational_revision"] + 1


def test_repeated_heartbeats_never_change_turn_generation_or_representative():
    database, service, session_id = _configured_service()
    before = deepcopy(database.session)

    for _index in range(25):
        service.heartbeat(
            operational_session_id=session_id,
            device_id="DEVICE-A",
        )

    assert database.session["turn_id"] == before["turn_id"]
    assert database.session["generation"] == before["generation"]
    assert database.session["active_user_id"] == before["active_user_id"]


def test_turn_only_change_is_blocked_without_explicit_admin_override():
    database, service, session_id = _configured_service()
    before = deepcopy(database.session)

    with pytest.raises(AdmissionWriteBlocked, match="relevo explícito"):
        service.transition_primary_turn(
            operational_session_id=session_id,
            primary_device_id="DEVICE-A",
            new_turn_id=101,
            expected_generation=before["generation"],
            actor_user={"id": 7, "username": "admin", "role": "administrador"},
        )

    with pytest.raises(AdmissionWriteBlocked, match="relevo explícito"):
        service.admin_change_admission_turn(
            actor_user={"id": 7, "username": "admin", "role": "administrador"},
            operational_session_id=session_id,
            primary_device_id="DEVICE-A",
            new_turn_id=101,
            new_turn_code="8PM_8AM",
            expected_generation=before["generation"],
            transition_id="e2369a80-0495-44af-97c3-42f0cf675ce3",
        )

    assert database.session == before


def test_admin_override_outside_nominal_schedule_preserves_representative_and_lease():
    database, service, session_id = _configured_service()
    before = deepcopy(database.session)

    changed = service.admin_set_admission_turn(
        actor_user={"id": 7, "username": "admin", "role": "administrador"},
        operational_session_id=session_id,
        primary_device_id="DEVICE-A",
        new_turn_id=102,
        new_turn_code="8AM_8AM",
        expected_generation=before["generation"],
        transition_id="f2369a80-0495-44af-97c3-42f0cf675ce3",
        administrative_override=True,
        reason="Corrección administrativa de turno",
    ).operational_session

    assert changed.turn_id == 102
    assert changed.active_user_id == before["active_user_id"]
    assert changed.primary_device_id == before["primary_device_id"]
    assert changed.primary_login_session_id == before["primary_login_session_id"]
    assert changed.generation == before["generation"] + 1
    assert {
        item["event"] for item in database.audit
    } >= {"TURN_ADMIN_OVERRIDE_REQUESTED", "TURN_ADMIN_OVERRIDE_COMMITTED"}


def test_admin_can_select_an_active_audit_user_as_representative():
    database, service, _session_id = _configured_service()
    before = deepcopy(database.session)

    changed = service.admin_set_admission_representative(
        actor_user_id=7,
        actor_username="admin",
        actor_role="administrador",
        actor_login_session_id="LOGIN-A1",
        actor_device_id="DEVICE-A",
        target_user={"id": 24, "username": "audit-user"},
    )

    assert changed.active_user_id == "24"
    assert changed.active_username == "audit-user"
    assert changed.turn_id == before["turn_id"]
    assert changed.primary_device_id == before["primary_device_id"]


def test_aux_request_admin_b_authorizes_target_without_changing_turn_or_primary():
    database, service, _session_id = _configured_service()
    database.users["8"] = {
        "id": 8,
        "username": "admin-b",
        "full_name": "ADMIN B",
        "role": ADMISSION_ROLE_ADMINISTRATOR,
        "is_active": True,
    }
    before = deepcopy(database.session)

    changed = service.admin_set_admission_representative(
        authorizing_admin_user_id=8,
        authorizing_admin_username="admin-b",
        authorizing_admin_role=ADMISSION_ROLE_ADMINISTRATOR,
        requesting_user_id=22,
        requesting_username="aux-b",
        requesting_login_session_id="LOGIN-AUX-REQUEST",
        requesting_device_id="DEVICE-AUX",
        target_user={"id": 23, "username": "aux-c"},
    )

    assert changed.active_user_id == "23"
    assert changed.turn_id == before["turn_id"]
    assert changed.turn_code == before["turn_code"]
    assert changed.turn_started_at == before["turn_started_at"]
    assert changed.turn_ends_at == before["turn_ends_at"]
    assert changed.primary_device_id == before["primary_device_id"]
    assert changed.primary_login_session_id == before["primary_login_session_id"]
    assert changed.lease_generation == before["lease_generation"]
    assert changed.generation == before["generation"]
    audit = next(
        item for item in database.audit
        if item["event"] == "TURN_REPRESENTATIVE_ADMIN_CORRECTED"
    )
    details = json.loads(audit["details"])
    assert details["requesting_user_id"] == "22"
    assert details["authorizing_admin_user_id"] == "8"
    assert details["target_representative_user_id"] == "23"


@pytest.mark.parametrize(
    ("admin_user", "expected_message"),
    [
        (
            {
                "id": 8,
                "username": "admin-disabled",
                "full_name": "ADMIN DISABLED",
                "role": ADMISSION_ROLE_ADMINISTRATOR,
                "is_active": False,
            },
            "ya no está habilitado",
        ),
        (
            {
                "id": 8,
                "username": "aux-authorizer",
                "full_name": "AUX AUTHORIZER",
                "role": ADMISSION_ROLE_AUXILIARY,
                "is_active": True,
            },
            "Solo un Administrador",
        ),
    ],
)
def test_representative_service_rechecks_authorizing_admin(admin_user, expected_message):
    database, service, _session_id = _configured_service()
    database.users["8"] = admin_user
    before = deepcopy(database.session)

    with pytest.raises(AdmissionWriteBlocked, match=expected_message):
        service.admin_set_admission_representative(
            authorizing_admin_user_id=8,
            authorizing_admin_username=admin_user["username"],
            authorizing_admin_role=admin_user["role"],
            requesting_user_id=22,
            requesting_username="aux-b",
            requesting_login_session_id="LOGIN-AUX-REQUEST",
            requesting_device_id="DEVICE-AUX",
            target_user={"id": 23, "username": "aux-c"},
        )

    assert database.session == before


def test_auxiliary_cannot_apply_an_administrative_turn_override():
    database, service, session_id = _configured_service()

    with pytest.raises(AdmissionWriteBlocked, match="Administrador"):
        service.admin_set_admission_turn(
            actor_user={"id": 22, "username": "aux-b", "role": "auxiliar"},
            operational_session_id=session_id,
            primary_device_id="DEVICE-A",
            new_turn_id=102,
            expected_generation=database.session["generation"],
            administrative_override=True,
        )


def test_access_matrix_keeps_admin_write_aux_match_and_audit_readonly():
    admin = evaluate_admission_access(
        {"role": "administrador"},
        {"base_write_allowed": True, "device_role": "SECONDARY", "status": "ACTIVE"},
    )
    aux_match = evaluate_admission_access(
        {"role": "auxiliar"},
        {"base_write_allowed": True, "device_role": "SECONDARY", "status": "ACTIVE"},
    )
    aux_mismatch = evaluate_admission_access(
        {"role": "auxiliar"},
        {"base_write_allowed": False, "device_role": "SECONDARY", "status": "ACTIVE"},
    )
    audit = evaluate_admission_access(
        {"role": "facturador de auditoría"},
        {"base_write_allowed": True, "device_role": "PRIMARY", "status": "ACTIVE"},
    )

    assert admin.write_allowed is True
    assert aux_match.write_allowed is True
    assert aux_mismatch.write_allowed is False
    assert audit.write_allowed is False


def test_primary_turn_permission_allows_admin_or_matching_representative_only():
    primary_state = {
        "active_user_id": "22",
        "active_username": "aux-b",
        "device_role": "PRIMARY",
        "status": "ACTIVE",
        "connection_state": "CONNECTED",
    }

    assert can_change_admission_turn(
        {"id": 7, "username": "admin", "role": "administrador"},
        primary_state,
    )
    assert can_change_admission_turn(
        {"id": 22, "username": "aux-b", "role": "auxiliar"},
        primary_state,
    )
    assert not can_change_admission_turn(
        {"id": 23, "username": "aux-c", "role": "auxiliar"},
        primary_state,
    )
    assert not can_change_admission_turn(
        {"id": 24, "username": "audit", "role": "facturador de auditoría"},
        primary_state,
    )
    assert not can_change_admission_turn(
        {"id": 22, "username": "aux-b", "role": "auxiliar"},
        {**primary_state, "device_role": "SECONDARY"},
    )


def test_matching_auxiliary_attaches_as_writable_secondary_without_changing_state():
    database, service, _session_id = _configured_service()
    before = deepcopy(database.session)

    attachment = service.attach_device(
        login_username="aux-b",
        login_user_id=22,
        login_role=ADMISSION_ROLE_AUXILIARY,
        device_id="DEVICE-B",
        login_session_id="LOGIN-B1",
    )

    assert attachment.role is StationRole.SECONDARY
    assert attachment.writable is True
    assert attachment.operational_session.active_user_id == before["active_user_id"]
    assert attachment.operational_session.turn_id == before["turn_id"]


def test_different_auxiliary_attaches_readonly_without_impersonation():
    database, service, _session_id = _configured_service()
    before = deepcopy(database.session)

    attachment = service.attach_device(
        login_username="aux-c",
        login_user_id=23,
        login_role=ADMISSION_ROLE_AUXILIARY,
        device_id="DEVICE-C",
        login_session_id="LOGIN-C1",
    )

    assert attachment.role is StationRole.SECONDARY
    assert attachment.writable is False
    assert attachment.operational_session.active_user_id == before["active_user_id"]
    assert attachment.operational_session.active_username == "aux-b"
