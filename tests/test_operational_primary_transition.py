from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from admission_hybrid import (
    AdmissionWriteBlocked,
    AdmissionWriteGuard,
    OperationalSessionService,
    SAME_USER_HANDOFF_MESSAGE,
    StationRole,
    same_user,
    user_can_operate_admission,
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


class _OperationalDB:
    def __init__(self):
        self.source_id = "11111111-1111-4111-8111-111111111111"
        self.session = None
        self.devices = {}
        self.active_sessions = {}
        self.active_session_users = {}
        self.logout_reasons = {}
        self.audit = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, _exc, _tb):
        return False

    def execute(self, query, params=()):
        sql = " ".join(str(query).split())
        upper = sql.upper()
        params = tuple(params or ())
        if "PG_ADVISORY_XACT_LOCK" in upper:
            return _Result((True,))
        if "TO_REGCLASS('PUBLIC.ACTIVE_SESSIONS')" in upper:
            return _Result(("active_sessions",))
        if "FROM ADMISSION_OPERATIONAL_IDENTITY" in upper:
            return _Result((self.source_id,))
        if "INSERT INTO ADMISSION_OPERATIONAL_IDENTITY" in upper:
            self.source_id = str(params[0])
            return _Result(rowcount=1)
        if upper.startswith("INSERT INTO ADMISSION_OPERATIONAL_SESSIONS"):
            (
                sid, username, user_id, display_name, device_id, login_id,
                turn_id, source_id,
            ) = params
            started = datetime.now(timezone.utc)
            self.session = {
                "operational_session_id": str(sid),
                "active_username": str(username),
                "active_user_id": str(user_id),
                "active_user_display_name": str(display_name),
                "primary_device_id": str(device_id),
                "primary_login_session_id": str(login_id),
                "turn_id": turn_id,
                "operational_source_id": str(source_id),
                "status": "ACTIVE",
                "generation": 1,
                "lease_generation": 0,
                "primary_last_seen": "now",
                "updated_at": "now",
                "turn_started_at": started.isoformat(),
                "turn_ends_at": (started + timedelta(hours=12)).isoformat(),
            }
            return _Result(rowcount=1)
        if "SELECT OPERATIONAL_SESSION_ID,DETAILS_JSON FROM ADMISSION_OPERATIONAL_AUDIT" in upper:
            transition_id = str(params[0])
            match = next(
                (item for item in self.audit if item.get("transition_id") == transition_id),
                None,
            )
            return _Result(
                {
                    "operational_session_id": match["session_id"],
                    "details_json": match["details"],
                }
                if match else None
            )
        if "SELECT S.*," in upper and "LEFT JOIN ADMISSION_OPERATIONAL_DEVICES" in upper:
            if not self.session or self.session["status"] != "ACTIVE":
                return _Result(None)
            data = deepcopy(self.session)
            device = deepcopy(self.devices.get(str(params[0]), {}))
            if device:
                data.update(device)
                data["device_login_session_id"] = device.get("login_session_id")
            return _Result(data)
        if upper.startswith("SELECT * FROM ADMISSION_OPERATIONAL_SESSIONS"):
            if not self.session:
                return _Result(None)
            if "WHERE STATUS='ACTIVE'" in upper and self.session["status"] != "ACTIVE":
                return _Result(None)
            if "WHERE OPERATIONAL_SESSION_ID=%S" in upper and str(params[0]) != self.session["operational_session_id"]:
                return _Result(None)
            return _Result(deepcopy(self.session))
        if "SELECT COUNT(*) FROM ADMISSION_OPERATIONAL_DEVICES" in upper:
            sid, excluded = map(str, params)
            count = sum(
                1 for device_id, row in self.devices.items()
                if row["operational_session_id"] == sid
                and device_id != excluded and row.get("detached_at") is None
            )
            return _Result((count,))
        if "SELECT LOGIN_SESSION_ID,DETACHED_AT FROM ADMISSION_OPERATIONAL_DEVICES" in upper:
            row = self.devices.get(str(params[1]))
            return _Result(
                deepcopy(row) if row and row.get("detached_at") is None else None
            )
        if "SELECT IS_ACTIVE,DEVICE_ID FROM ACTIVE_SESSIONS" in upper:
            session_id = str(params[0])
            active = self.active_sessions.get(session_id)
            owner = next(
                (
                    device_id for device_id, row in self.devices.items()
                    if row.get("login_session_id") == session_id
                ),
                "",
            )
            return _Result(
                {"is_active": active, "device_id": owner}
                if active is not None else None
            )
        if "FROM ACTIVE_SESSIONS S JOIN USERS U" in upper:
            session_id = str(params[0])
            active = self.active_sessions.get(session_id)
            actor = deepcopy(self.active_session_users.get(session_id, {}))
            if active is None or not actor:
                return _Result(None)
            actor["is_active"] = active
            return _Result(actor)
        if "SELECT PRIMARY_LAST_SEEN < NOW()" in upper:
            return _Result((False,))
        if "SELECT LOGIN_SESSION_ID,STATION_ROLE" in upper:
            row = self.devices.get(str(params[1]))
            return _Result(deepcopy(row) if row else None)
        if upper.startswith("INSERT INTO ADMISSION_OPERATIONAL_DEVICES"):
            if len(params) == 4:
                sid, device_id, login_id, device_name = params
                self.active_sessions.setdefault(str(login_id), True)
                self.active_session_users.setdefault(
                    str(login_id),
                    {
                        "username": str(self.session.get("active_username") or ""),
                        "device_id": str(device_id),
                        "user_id": str(self.session.get("active_user_id") or ""),
                        "role": "administrador",
                    },
                )
                row = self.devices.setdefault(str(device_id), {})
                row.update({
                    "operational_session_id": str(sid),
                    "device_id": str(device_id),
                    "login_session_id": str(login_id),
                    "device_name": str(device_name),
                    "station_role": "PRIMARY",
                    "detached_at": None,
                    "invalidated_at": None,
                    "invalidated_reason": None,
                    "invalidated_generation": None,
                    "new_active_username": None,
                })
                return _Result(rowcount=1)
            sid, device_id, login_id, device_name, role = params
            self.active_sessions.setdefault(str(login_id), True)
            self.active_session_users.setdefault(
                str(login_id),
                {
                    "username": str(self.session.get("active_username") or ""),
                    "device_id": str(device_id),
                    "user_id": str(self.session.get("active_user_id") or ""),
                    "role": (
                        "administrador"
                        if str(self.session.get("active_username") or "").casefold()
                        == "admin"
                        else "facturador de auditoria"
                    ),
                },
            )
            self.devices[str(device_id)] = {
                "operational_session_id": str(sid),
                "device_id": str(device_id),
                "login_session_id": str(login_id),
                "device_name": str(device_name),
                "station_role": str(role),
                "detached_at": None,
                "invalidated_at": None,
                "invalidated_reason": None,
                "invalidated_generation": None,
                "new_active_username": None,
            }
            return _Result(rowcount=1)
        if upper.startswith("INSERT INTO ADMISSION_OPERATIONAL_AUDIT"):
            sid, event, device, username, generation, details, transition_id = params
            self.audit.append({
                "session_id": str(sid), "event": str(event),
                "device_id": str(device), "username": str(username),
                "generation": generation, "details": details,
                "transition_id": str(transition_id) if transition_id else None,
            })
            return _Result(rowcount=1)
        if "ADMISSION_OPERATIONAL_TURN_INTERVALS" in upper:
            return _Result(rowcount=1)
        if "SELECT LOGIN_SESSION_ID FROM ADMISSION_OPERATIONAL_DEVICES" in upper:
            row = self.devices.get(str(params[1]))
            return _Result(
                {"login_session_id": row.get("login_session_id")} if row else None
            )
        if "SELECT STATION_ROLE,LOGIN_SESSION_ID" in upper:
            row = self.devices.get(str(params[1]))
            return _Result(deepcopy(row) if row and row.get("detached_at") is None else None)
        if "SELECT DEVICE_ID,LOGIN_SESSION_ID FROM ADMISSION_OPERATIONAL_DEVICES" in upper:
            sid, primary = map(str, params)
            rows = [
                deepcopy(row) for device_id, row in sorted(self.devices.items())
                if row["operational_session_id"] == sid
                and device_id != primary and row.get("detached_at") is None
            ]
            return _Result(rows=rows)
        if "SELECT DEVICE_ID,STATION_ROLE,LOGIN_SESSION_ID" in upper:
            sid = str(params[0])
            rows = [
                deepcopy(row) for row in self.devices.values()
                if row["operational_session_id"] == sid
                and row.get("detached_at") is None
            ]
            return _Result(rows=rows)
        if upper.startswith(
            "UPDATE ADMISSION_OPERATIONAL_DEVICES SET DETACHED_AT=NOW(),INVALIDATED_AT=NOW(), INVALIDATED_REASON=%S"
        ):
            _reason, _sid, device_id, _login_id = params
            row = self.devices.get(str(device_id))
            if row and row.get("detached_at") is None:
                row.update({"detached_at": "now", "invalidated_at": "now"})
                return _Result(rowcount=1)
            return _Result(rowcount=0)
        if upper.startswith(
            "UPDATE ADMISSION_OPERATIONAL_SESSIONS SET PRIMARY_DEVICE_ID='',PRIMARY_LOGIN_SESSION_ID=''"
        ):
            self.session.update({"primary_device_id": "", "primary_login_session_id": ""})
            return _Result(rowcount=1)
        if upper.startswith(
            "UPDATE ADMISSION_OPERATIONAL_SESSIONS SET PRIMARY_DEVICE_ID=%S,PRIMARY_LOGIN_SESSION_ID=%S"
        ):
            device_id, login_id, _sid = params
            self.session.update({
                "primary_device_id": str(device_id),
                "primary_login_session_id": str(login_id),
            })
            if "LEASE_GENERATION=LEASE_GENERATION+1" in upper:
                self.session["lease_generation"] += 1
            return _Result(rowcount=1)
        if upper.startswith(
            "UPDATE ADMISSION_OPERATIONAL_DEVICES SET STATION_ROLE='SECONDARY',LAST_SEEN=NOW()"
        ):
            if "PRIMARY_TRANSFERRED_ADMINISTRATIVELY" in upper:
                generation, username, _sid, device_id = params
                count = 0
                for current_device_id, row in self.devices.items():
                    if (
                        row["operational_session_id"] == str(_sid)
                        and current_device_id != str(device_id)
                        and row.get("detached_at") is None
                        and row.get("station_role") == "PRIMARY"
                    ):
                        row.update(
                            {
                                "station_role": "SECONDARY",
                                "detached_at": "now",
                                "invalidated_at": "now",
                                "invalidated_reason": (
                                    "PRIMARY_TRANSFERRED_ADMINISTRATIVELY"
                                ),
                                "invalidated_generation": int(generation),
                                "new_active_username": str(username),
                            }
                        )
                        count += 1
                return _Result(rowcount=count)
            _sid, device_id = params
            if "DEVICE_ID<>%S" in upper:
                count = 0
                for current_device_id, row in self.devices.items():
                    if (
                        row["operational_session_id"] == str(_sid)
                        and current_device_id != str(device_id)
                        and row.get("detached_at") is None
                        and row.get("station_role") == "PRIMARY"
                    ):
                        row["station_role"] = "SECONDARY"
                        count += 1
                return _Result(rowcount=count)
            row = self.devices.get(str(device_id))
            if row and row.get("detached_at") is None:
                row["station_role"] = "SECONDARY"
                return _Result(rowcount=1)
            return _Result(rowcount=0)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_SESSIONS SET PRIMARY_LOGIN_SESSION_ID"):
            self.session["primary_login_session_id"] = str(params[0])
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_SESSIONS SET PRIMARY_LAST_SEEN"):
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_DEVICES SET LOGIN_SESSION_ID"):
            login_id, _sid, device_id = params
            self.devices[str(device_id)].update({
                "login_session_id": str(login_id),
                "detached_at": None,
                "invalidated_at": None,
                "invalidated_reason": None,
                "invalidated_generation": None,
                "new_active_username": None,
            })
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_SESSIONS SET ACTIVE_USERNAME"):
            (
                username, user_id, display_name, login_id, turn_id,
                turn_code, generation, _duration_hours, _changed_by, _reason, _sid,
            ) = params
            self.session.update({
                "active_username": str(username),
                "active_user_id": str(user_id),
                "active_user_display_name": str(display_name),
                "primary_login_session_id": str(login_id),
                "turn_id": turn_id,
                "turn_code": str(turn_code),
                "generation": int(generation),
            })
            return _Result(rowcount=1)
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_DEVICES SET STATION_ROLE='PRIMARY'"):
            login_id, _sid, device_id = params
            row = self.devices[str(device_id)]
            row.update({
                "station_role": "PRIMARY", "login_session_id": str(login_id),
                "detached_at": None, "invalidated_at": None,
                "invalidated_reason": None, "invalidated_generation": None,
                "new_active_username": None,
            })
            return _Result(rowcount=1)
        if "INVALIDATED_REASON='PRIMARY_USER_CHANGED'" in upper:
            generation, username, sid, selected_devices = params
            selected_devices = {str(item) for item in selected_devices}
            count = 0
            for device_id, row in self.devices.items():
                if (
                    row["operational_session_id"] == str(sid)
                    and device_id in selected_devices
                    and row.get("detached_at") is None
                ):
                    row.update({
                        "detached_at": "now", "invalidated_at": "now",
                        "invalidated_reason": "PRIMARY_USER_CHANGED",
                        "invalidated_generation": int(generation),
                        "new_active_username": str(username),
                    })
                    count += 1
            return _Result(rowcount=count)
        if "INVALIDATED_REASON='SECONDARY_USER_CHANGED'" in upper:
            generation, username, _sid, device_id = params
            row = self.devices.get(str(device_id))
            if row:
                row.update({
                    "detached_at": "now", "invalidated_at": "now",
                    "invalidated_reason": "SECONDARY_USER_CHANGED",
                    "invalidated_generation": int(generation),
                    "new_active_username": str(username),
                })
            return _Result(rowcount=bool(row))
        if "UPDATE ADMISSION_OPERATIONAL_DEVICES SET DETACHED_AT=NOW()" in upper:
            _sid, device_id = params
            row = self.devices.get(str(device_id))
            if row and row.get("station_role") != "PRIMARY":
                row["detached_at"] = "now"
                return _Result(rowcount=1)
            return _Result(rowcount=0)
        if upper.startswith("UPDATE ACTIVE_SESSIONS SET IS_ACTIVE=0"):
            if "PRIMARY_TRANSFERRED_ADMINISTRATIVELY" in upper:
                session_id, device_id = map(str, params)
                actor = self.active_session_users.get(session_id, {})
                if (
                    self.active_sessions.get(session_id)
                    and str(actor.get("device_id") or "") == device_id
                ):
                    self.active_sessions[session_id] = False
                    self.logout_reasons[session_id] = (
                        "PRIMARY_TRANSFERRED_ADMINISTRATIVELY"
                    )
                    return _Result(rowcount=1)
                return _Result(rowcount=0)
            ids = {str(item) for item in params[0]}
            excluded = str(params[1]) if len(params) > 1 else ""
            for session_id in ids:
                if session_id != excluded and session_id in self.active_sessions:
                    self.active_sessions[session_id] = False
            return _Result(rowcount=len(ids))
        if upper.startswith("UPDATE ADMISSION_OPERATIONAL_DEVICES SET LAST_SEEN"):
            return _Result(rowcount=1)
        raise AssertionError(f"SQL no simulado: {sql} | {params}")


def _service():
    database = _OperationalDB()
    return database, OperationalSessionService(lambda: database)


def _configured_primary(
    database,
    service,
    *,
    username,
    user_id,
    device_id,
    login_session_id,
    turn_id,
    login_role="administrador",
    display_name="",
):
    """Model the explicit representative/turn setup that follows Admin bootstrap."""
    service.attach_device(
        login_username=username,
        login_user_id=user_id,
        login_role="administrador",
        login_display_name=display_name,
        device_id=device_id,
        login_session_id=login_session_id,
        turn_id=turn_id,
    )
    database.session.update(
        active_username=str(username),
        active_user_id=str(user_id),
        active_user_display_name=str(display_name or username),
        turn_id=int(turn_id),
    )
    database.active_sessions[str(login_session_id)] = True
    database.active_session_users[str(login_session_id)] = {
        "username": str(username),
        "device_id": str(device_id),
        "user_id": str(user_id),
        "role": str(login_role),
    }
    return service.attach_device(
        login_username=username,
        login_user_id=user_id,
        login_role=login_role,
        login_display_name=display_name,
        device_id=device_id,
        login_session_id=login_session_id,
        turn_id=turn_id,
    )


def test_canonical_identity_never_uses_full_name_for_permission():
    assert same_user(
        {"id": 7, "username": "admin", "full_name": "FERNANDO JHOEL"},
        {"active_user_id": "7", "active_username": "admin"},
    )


def test_auxiliary_and_administrator_are_admission_operational_roles():
    assert user_can_operate_admission({"role": "auxiliar"}) is True
    assert user_can_operate_admission({"role": "Auxiliar de facturación"}) is True
    assert user_can_operate_admission({"role": "administrador"}) is True


def test_auxiliary_login_never_creates_or_replaces_operational_session():
    database, service = _service()
    with pytest.raises(AdmissionWriteBlocked):
        service.attach_device(
            login_username="aux",
            login_user_id=9,
            login_role="auxiliar",
            device_id="PC-1",
            login_session_id="AUX-1",
            turn_id=99,
        )
    assert database.session is None

    current = _configured_primary(
        database,
        service,
        username="fernando",
        user_id=8,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="P-1",
        turn_id=316,
    )
    generation = current.operational_session.generation
    auxiliary = service.attach_device(
        login_username="aux",
        login_user_id=9,
        login_role="auxiliar",
        device_id="PC-1",
        login_session_id="AUX-2",
        turn_id=999,
    )
    assert auxiliary.role == StationRole.PRIMARY
    assert auxiliary.writable is False
    assert database.session["active_username"] == "fernando"
    assert database.session["turn_id"] == 316
    assert database.session["generation"] == generation


def test_primary_transition_accepts_an_explicitly_assigned_auxiliary():
    database, service = _service()
    current = _configured_primary(
        database,
        service,
        username="fernando",
        user_id=8,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="P-1",
        turn_id=316,
    )
    database.active_sessions["AUX-1"] = True
    changed = service.transition_primary_user(
        operational_session_id=current.operational_session.operational_session_id,
        primary_device_id="PC-1",
        new_login_session_id="AUX-1",
        new_user={"id": 9, "username": "aux", "role": "auxiliar"},
        new_turn_id=317,
        expected_generation=1,
    )
    assert changed.operational_session.active_username == "aux"
    assert changed.operational_session.turn_id == 317


def test_auxiliary_write_guard_is_read_only_without_changing_the_session():
    database, service = _service()
    current = _configured_primary(
        database,
        service,
        username="fernando",
        user_id=8,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="P-1",
        turn_id=316,
    )
    decision = AdmissionWriteGuard().can_write_admission(
        login_user="aux",
        login_user_id=9,
        login_role="auxiliar",
        device_id="PC-1",
        session=current.operational_session,
        generation=current.operational_session.generation,
        role=StationRole.NONE,
    )
    assert decision.allowed is False
    assert decision.code == "SECONDARY_USER_MISMATCH"
    assert "FERNANDO" in decision.message.upper()


def test_primary_rebinds_login_by_device_and_guard_uses_user_id():
    database, service = _service()
    first = _configured_primary(
        database,
        service,
        username="admin", user_id=7, display_name="FERNANDO JHOEL",
        login_role="administrador",
        device_id="PC-1", login_session_id="LOGIN-1", turn_id=10,
    )
    restarted = service.rebind_login_session_to_operational_state(
        current_user={
            "id": 7,
            "username": "ADMIN",
            "full_name": "Administrador del sistema",
            "role": "administrador",
        },
        device_id="PC-1",
        login_session_id="LOGIN-2",
        device_name="Principal",
    )
    assert first.role == restarted.role == StationRole.PRIMARY
    assert restarted.operational_session.primary_device_id == "PC-1"
    assert restarted.operational_session.primary_login_session_id == "LOGIN-2"
    assert restarted.operational_session.turn_id == 10
    assert restarted.operational_session.generation == 1
    decision = AdmissionWriteGuard().can_write_admission(
        login_user="ADMIN",login_user_id=7,login_role="administrador",device_id="PC-1",
        session=restarted.operational_session,generation=1,role=StationRole.PRIMARY,
    )
    assert decision.allowed
    assert database.devices["PC-1"]["station_role"] == "PRIMARY"
    assert any(item["event"] == "LOGIN_SESSION_REBOUND" for item in database.audit)


def test_secondary_logout_and_wrong_user_never_change_primary():
    database, service = _service()
    primary = _configured_primary(
        database, service, username="admin",user_id=7,device_id="PC-1",
        login_session_id="P-1",turn_id=10,login_role="administrador",
    )
    service.attach_device(
        login_username="admin",login_user_id=7,device_id="PC-2",
        login_session_id="S-1",turn_id=10,login_role="administrador",
    )
    service.detach_device(
        operational_session_id=primary.operational_session.operational_session_id,
        device_id="PC-2",
    )
    assert database.session["primary_device_id"] == "PC-1"
    assert database.session["turn_id"] == 10
    database.active_sessions["S-OTHER"] = True
    rejected = service.attach_device(
        login_username="otro",login_user_id=8,device_id="PC-2",
        login_session_id="S-OTHER",turn_id=999,
        login_role="facturador de auditoria",
    )
    assert rejected.role == StationRole.NONE
    assert database.active_sessions["S-OTHER"] is True
    assert database.session["active_username"] == "admin"
    assert database.session["primary_device_id"] == "PC-1"


def test_closed_primary_login_allows_same_user_to_acquire_primary_on_another_device():
    database, service = _service()
    primary = _configured_primary(
        database, service, username="fernando", user_id=8, device_id="PC-A",
        login_session_id="LOGIN-A", turn_id=316, login_role="administrador",
    )
    database.active_sessions["LOGIN-A"] = False

    assert service.release_login_session(
        device_id="PC-A", login_session_id="LOGIN-A", reason="LOGOUT"
    )
    assert database.session["primary_device_id"] == ""
    assert database.devices["PC-A"]["detached_at"] == "now"

    database.active_sessions["LOGIN-B"] = True
    acquired = service.attach_device(
        login_username="fernando", login_user_id=8, device_id="PC-B",
        login_session_id="LOGIN-B", turn_id=999, login_role="administrador",
    )

    assert acquired.role == StationRole.PRIMARY
    assert acquired.writable is True
    assert acquired.operational_session.primary_device_id == "PC-B"
    assert acquired.operational_session.primary_login_session_id == "LOGIN-B"
    assert acquired.operational_session.turn_id == primary.operational_session.turn_id
    assert acquired.operational_session.generation == primary.operational_session.generation
    assert any(item["event"] == "PRIMARY_ACQUIRED_AFTER_RELEASE" for item in database.audit)


def test_primary_logout_and_relogin_on_same_device_reuses_turn_and_generation():
    database, service = _service()
    original = _configured_primary(
        database, service, username="admin", user_id=7, device_id="PC-A",
        login_session_id="LOGIN-A1", turn_id=500, login_role="administrador",
    )
    assert service.release_login_session(
        device_id="PC-A", login_session_id="LOGIN-A1", reason="LOGOUT"
    )
    acquired = service.attach_device(
        login_username="admin", login_user_id=7, device_id="PC-A",
        login_session_id="LOGIN-A2", turn_id=999, login_role="administrador",
    )
    assert acquired.role == StationRole.PRIMARY
    assert acquired.operational_session.turn_id == original.operational_session.turn_id
    assert acquired.operational_session.generation == original.operational_session.generation
    assert database.devices["PC-A"]["login_session_id"] == "LOGIN-A2"


def test_available_primary_lease_has_one_winner_and_live_owner_cannot_be_stolen():
    database, service = _service()
    first = _configured_primary(
        database, service, username="admin", user_id=7, device_id="PC-A",
        login_session_id="LOGIN-A", turn_id=500, login_role="administrador",
    )
    service.release_login_session(
        device_id="PC-A", login_session_id="LOGIN-A", reason="LOGOUT"
    )
    winner = service.attach_device(
        login_username="admin", login_user_id=7, device_id="PC-B",
        login_session_id="LOGIN-B", turn_id=999, login_role="administrador",
    )
    loser = service.attach_device(
        login_username="admin", login_user_id=7, device_id="PC-C",
        login_session_id="LOGIN-C", turn_id=999, login_role="administrador",
    )
    assert winner.role == StationRole.PRIMARY
    assert loser.role == StationRole.SECONDARY
    assert database.session["primary_device_id"] == "PC-B"
    assert sum(
        row.get("station_role") == "PRIMARY" and row.get("detached_at") is None
        for row in database.devices.values()
    ) == 1
    assert winner.operational_session.turn_id == first.operational_session.turn_id
    assert winner.operational_session.generation == first.operational_session.generation


def test_primary_transition_invalidates_exact_secondaries_and_is_idempotent():
    database, service = _service()
    primary = _configured_primary(
        database, service, username="admin",user_id=7,device_id="PC-1",
        login_session_id="P-OLD",turn_id=10,login_role="administrador",
    )
    service.attach_device(
        login_username="admin",login_user_id=7,device_id="PC-2",
        login_session_id="S-OLD",turn_id=10,login_role="administrador",
    )
    database.active_sessions.update({"P-OLD": True, "P-NEW": True, "S-OLD": True})
    changed = service.transition_primary_user(
        operational_session_id=primary.operational_session.operational_session_id,
        primary_device_id="PC-1",new_login_session_id="P-NEW",
        new_user={
            "id": 8,"username": "usuario_b","full_name": "USUARIO B",
            "role": "administrador",
        },
        new_turn_id=20,expected_generation=1,transition_id="22222222-2222-4222-8222-222222222222",
    )
    assert changed.operational_session.generation == 2
    assert changed.committed is True
    assert changed.old_generation == 1
    assert changed.new_generation == 2
    assert changed.old_turn_id == 10
    assert changed.new_turn_id == 20
    assert changed.operational_session.primary_device_id == "PC-1"
    assert changed.operational_session.primary_login_session_id == "P-NEW"
    assert database.active_sessions == {"P-OLD": False, "P-NEW": True, "S-OLD": False}
    assert database.devices["PC-2"]["invalidated_reason"] == "PRIMARY_USER_CHANGED"
    assert any(
        item["event"] == "OPERATIONAL_GENERATION_CHANGED"
        for item in database.audit
    )
    duplicate = service.transition_primary_user(
        operational_session_id=primary.operational_session.operational_session_id,
        primary_device_id="PC-1",new_login_session_id="P-NEW",
        new_user={"id": 8,"username": "usuario_b","role": "administrador"},new_turn_id=20,
        expected_generation=2,transition_id="22222222-2222-4222-8222-222222222222",
    )
    assert duplicate.operational_session.generation == 2
    assert duplicate.committed is True
    assert duplicate.new_generation == 2
    assert duplicate.new_turn_id == 20


def test_same_user_handoff_is_rejected_and_keeps_turn_generation_and_secondary():
    database, service = _service()
    primary = _configured_primary(
        database, service, username="admin",user_id=7,device_id="PC-1",
        login_session_id="P-1",turn_id=10,login_role="administrador",
    )
    service.attach_device(
        login_username="admin",login_user_id=7,device_id="PC-2",
        login_session_id="S-1",turn_id=10,login_role="administrador",
    )
    database.active_sessions.update({"P-1": True, "S-1": True})
    before = deepcopy(database.session)
    with pytest.raises(AdmissionWriteBlocked) as error:
        service.transition_primary_user(
            operational_session_id=primary.operational_session.operational_session_id,
            primary_device_id="PC-1",new_login_session_id="P-1",
            new_user={
                "id": 7,"username": "admin","full_name": "FERNANDO",
                "role": "administrador",
            },
            new_turn_id=10,expected_generation=1,
            transition_id="44444444-4444-4444-8444-444444444444",
            invalidate_secondaries=False,
        )
    assert str(error.value) == SAME_USER_HANDOFF_MESSAGE
    assert database.session == before
    assert database.devices["PC-2"]["detached_at"] is None
    assert database.active_sessions["S-1"] is True
    stale = service.resolve_operational_state(
        current_user={"id": 7, "username": "admin", "role": "administrador"},
        current_session_id="S-1",
        current_device_id="PC-2",
        local_generation=1,
    )
    assert stale.reason_code == "ALLOWED"
    assert stale.write_allowed is True
    refreshed = service.resolve_operational_state(
        current_user={"id": 7, "username": "admin", "role": "administrador"},
        current_session_id="S-1",
        current_device_id="PC-2",
        local_generation=None,
    )
    assert refreshed.generation == 1
    assert refreshed.turn_id == 10
    assert refreshed.write_allowed is True


def test_nominal_turn_window_is_twelve_hours_in_central_schema():
    from admission_hybrid import ADMISSION_TURN_HOURS, POSTGRES_HYBRID_SCHEMA

    assert ADMISSION_TURN_HOURS == 12
    assert "turn_ends_at TIMESTAMPTZ" in POSTGRES_HYBRID_SCHEMA
    database, service = _service()
    session = _configured_primary(
        database,
        service,
        username="admin",
        user_id=7,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="P-1",
        turn_id=10,
    ).operational_session
    started = datetime.fromisoformat(session.turn_started_at)
    ends = datetime.fromisoformat(session.turn_ends_at)
    assert (ends - started).total_seconds() == 12 * 60 * 60


def test_secondary_old_user_is_rejected_and_new_user_reattaches_to_central_turn():
    database, service = _service()
    primary = _configured_primary(
        database, service, username="admin", user_id=7, device_id="PC-1",
        login_session_id="P-OLD", turn_id=10, login_role="administrador",
    )
    service.attach_device(
        login_username="admin", login_user_id=7, device_id="PC-2",
        login_session_id="S-OLD", turn_id=10, login_role="administrador",
    )
    database.active_sessions.update({"P-OLD": True, "P-NEW": True, "S-OLD": True})
    service.transition_primary_user(
        operational_session_id=primary.operational_session.operational_session_id,
        primary_device_id="PC-1", new_login_session_id="P-NEW",
        new_user={
            "id": 8, "username": "fernando", "full_name": "FERNANDO",
            "role": "auxiliar",
        },
        new_turn_id=20, expected_generation=1,
        transition_id="55555555-5555-4555-8555-555555555555",
    )

    old_login = service.attach_device(
        login_username="admin", login_user_id=7, device_id="PC-2",
        login_session_id="S-ADMIN-NEW", turn_id=999, login_role="auxiliar",
    )
    assert old_login.role == StationRole.SECONDARY
    assert old_login.writable is False
    assert old_login.operational_session.active_username == "fernando"
    assert old_login.operational_session.turn_id == 20

    new_login = service.attach_device(
        login_username="fernando", login_user_id=8, device_id="PC-2",
        login_session_id="S-FERNANDO", turn_id=999, login_role="auxiliar",
    )
    assert new_login.role == StationRole.SECONDARY
    assert new_login.writable is True
    assert new_login.operational_session.generation == 2
    assert new_login.operational_session.turn_id == 20


def test_five_consecutive_primary_transitions_preserve_invariants():
    database, service = _service()
    attached = _configured_primary(
        database, service, username="user_a",user_id=1,device_id="PC-1",
        login_session_id="P-0",turn_id=100,login_role="administrador",
    )
    generation = attached.operational_session.generation
    users = [("user_b", 2), ("user_a", 1), ("user_b", 2), ("user_a", 1), ("user_b", 2)]
    for index, (username, user_id) in enumerate(users, start=1):
        service.attach_device(
            login_username=username,login_user_id=user_id,device_id="PC-1",
            login_session_id=f"P-{index}",turn_id=100 + index,
            login_role="administrador",
        )
        result = service.transition_primary_user(
            operational_session_id=attached.operational_session.operational_session_id,
            primary_device_id="PC-1",new_login_session_id=f"P-{index}",
            new_user={
                "id": user_id,"username": username,"full_name": username.upper(),
                "role": "administrador",
            },
            new_turn_id=100 + index,expected_generation=generation,
            transition_id=f"33333333-3333-4333-8333-{index:012d}",
        )
        generation += 1
        assert result.operational_session.generation == generation
        assert result.operational_session.primary_device_id == "PC-1"
        assert result.operational_session.primary_login_session_id == f"P-{index}"
        assert result.operational_session.active_username == username
        assert result.operational_session.turn_id == 100 + index
        assert service.validate_operational_invariants()["valid"]


def test_admin_force_transfer_changes_only_primary_lease():
    database, service = _service()
    primary = _configured_primary(
        database, service, username="admin", user_id=7, login_role="administrador",
        device_id="PC-1", login_session_id="P-1", turn_id=350,
    )
    service.attach_device(
        login_username="admin", login_user_id=7, login_role="administrador",
        device_id="PC-2", login_session_id="S-1", turn_id=999,
    )
    before = primary.operational_session
    changed = service.force_transfer_admission_primary(
        operational_session_id=before.operational_session_id,
        device_id="PC-2",
        login_session_id="S-1",
        admin_user_id=7,
        admin_username="admin",
        admin_role="administrador",
        reason="Recuperación administrativa",
    )
    assert changed.primary_device_id == "PC-2"
    assert changed.primary_login_session_id == "S-1"
    assert changed.turn_id == before.turn_id
    assert changed.active_user_id == before.active_user_id
    assert changed.generation == before.generation
    assert changed.lease_generation == before.lease_generation + 1
    assert database.devices["PC-1"]["station_role"] == "SECONDARY"
    assert database.devices["PC-1"]["detached_at"] == "now"
    assert database.devices["PC-1"]["invalidated_reason"] == (
        "PRIMARY_TRANSFERRED_ADMINISTRATIVELY"
    )
    assert database.devices["PC-2"]["station_role"] == "PRIMARY"
    assert database.active_sessions["P-1"] is False
    assert database.logout_reasons["P-1"] == (
        "PRIMARY_TRANSFERRED_ADMINISTRATIVELY"
    )
    events = {item["event"] for item in database.audit}
    assert "ADMISSION_PRIMARY_TRANSFER_REQUESTED" in events
    assert "ADMISSION_PRIMARY_REVOKED" in events
    assert "ADMISSION_PRIMARY_TRANSFER_COMPLETED" in events

    # A fresh login on the revoked computer is attached as SECONDARY; it does
    # not regain PRIMARY while PC-2 still owns the central lease.
    reattached = service.attach_device(
        login_username="admin",
        login_user_id=7,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="P-2",
        turn_id=999,
    )
    assert reattached.role is StationRole.SECONDARY
    assert reattached.operational_session.primary_device_id == "PC-2"
    assert reattached.operational_session.turn_id == before.turn_id
    assert reattached.operational_session.generation == before.generation

    transferred_back = service.force_transfer_admission_primary(
        operational_session_id=before.operational_session_id,
        device_id="PC-1",
        login_session_id="P-2",
        admin_user_id=7,
        admin_username="admin",
        admin_role="administrador",
        reason="Transferencia administrativa de regreso",
    )
    assert transferred_back.primary_device_id == "PC-1"
    assert transferred_back.turn_id == before.turn_id
    assert transferred_back.active_user_id == before.active_user_id
    assert transferred_back.generation == before.generation
    assert database.active_sessions["S-1"] is False


def test_transfer_is_rejected_when_target_is_already_primary():
    database, service = _service()
    session = _configured_primary(
        database,
        service,
        username="admin",
        user_id=7,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="P-1",
        turn_id=350,
    ).operational_session
    with pytest.raises(AdmissionWriteBlocked, match="ya posee"):
        service.force_transfer_admission_primary(
            operational_session_id=session.operational_session_id,
            device_id="PC-1",
            login_session_id="P-1",
            admin_user_id=7,
            admin_username="admin",
            admin_role="administrador",
            reason="No debe duplicarse",
        )


def test_non_admin_cannot_force_primary_transfer():
    database, service = _service()
    session = _configured_primary(
        database, service, username="admin", user_id=7,
        login_role="administrador", device_id="PC-1",
        login_session_id="P-1", turn_id=350,
    ).operational_session
    with pytest.raises(AdmissionWriteBlocked):
        service.force_transfer_admission_primary(
            operational_session_id=session.operational_session_id,
            device_id="PC-1", login_session_id="P-1",
            admin_user_id=9, admin_username="audit",
            admin_role="facturador de auditoria", reason="No autorizado",
        )


def test_vacant_primary_is_acquired_by_admin_without_changing_representative():
    database, service = _service()
    original = _configured_primary(
        database,
        service,
        username="aux-test",
        user_id=8,
        login_role="administrador",
        device_id="PC-A",
        login_session_id="LOGIN-A1",
        turn_id=500,
    )
    assert service.release_login_session(
        device_id="PC-A", login_session_id="LOGIN-A1", reason="LOGOUT"
    )

    acquired = service.rebind_login_session_to_operational_state(
        current_user={"id": 7, "username": "admin", "role": "administrador"},
        device_id="PC-A",
        login_session_id="LOGIN-A2",
    )

    assert acquired.role is StationRole.PRIMARY
    assert acquired.writable is True
    assert acquired.operational_session.primary_device_id == "PC-A"
    assert acquired.operational_session.primary_login_session_id == "LOGIN-A2"
    assert acquired.operational_session.active_username == "aux-test"
    assert acquired.operational_session.turn_id == original.operational_session.turn_id
    assert acquired.operational_session.generation == original.operational_session.generation
    assert database.session["lease_generation"] == 1


def test_valid_primary_is_not_acquired_by_a_second_admin_device():
    database, service = _service()
    _configured_primary(
        database,
        service,
        username="aux-test",
        user_id=8,
        login_role="administrador",
        device_id="PC-B",
        login_session_id="LOGIN-B1",
        turn_id=500,
    )

    secondary = service.rebind_login_session_to_operational_state(
        current_user={"id": 7, "username": "admin", "role": "administrador"},
        device_id="PC-A",
        login_session_id="LOGIN-A1",
    )

    assert secondary.role is StationRole.SECONDARY
    assert secondary.writable is True
    assert database.session["primary_device_id"] == "PC-B"
    assert sum(
        row.get("station_role") == "PRIMARY" and row.get("detached_at") is None
        for row in database.devices.values()
    ) == 1
