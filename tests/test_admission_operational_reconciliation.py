from __future__ import annotations

import importlib
import logging
import sqlite3
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from admission_hybrid import (
    AdmissionWriteBlocked,
    DatabaseTemporarilyOffline,
    DeviceAttachment,
    OfflineAdmissionStore,
    OperationalSession,
    OperationalState,
    StationRole,
    same_user,
)
from admission_v15_adapter import (
    DEFAULT_V15_ROOT,
    _HybridAdmissionRuntime,
    _HybridDatabaseProxy,
    _load_v15_modules,
)


def _session(*, username: str = "fernando", user_id: str = "8", generation: int = 42):
    return OperationalSession(
        operational_session_id="operational-1",
        active_username=username,
        active_user_id=user_id,
        active_user_display_name="FERNANDO JHOEL GUERRERO",
        primary_device_id="PC-1",
        primary_login_session_id="PRIMARY-LOGIN",
        turn_id=316,
        operational_source_id="source-central",
        status="ACTIVE",
        generation=generation,
        updated_at="2026-08-13T12:00:00+00:00",
    )


def _local_database(path: Path) -> None:
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE pacientes(id INTEGER PRIMARY KEY, nombre TEXT);
            CREATE TABLE dias_operativos(
                id INTEGER PRIMARY KEY, fecha_base TEXT, fecha_inicio TEXT,
                fecha_fin TEXT, estado TEXT
            );
            CREATE TABLE turnos(
                id INTEGER PRIMARY KEY, dia_operativo_id INTEGER,
                fecha_inicio TEXT, fecha_inicio_real TEXT,
                representante TEXT, estado TEXT, updated_at TEXT
            );
            CREATE TABLE atenciones(
                id INTEGER PRIMARY KEY AUTOINCREMENT, paciente_id INTEGER,
                dia_operativo_id INTEGER, turno_id INTEGER, nombre TEXT,
                sexo TEXT, edad_num INTEGER, unidad TEXT, cedula TEXT,
                telefono TEXT, direccion TEXT, nacionalidad TEXT, ars TEXT,
                hoja TEXT, fecha TEXT, hora TEXT, tipo_atencion TEXT,
                estado TEXT, nss TEXT
            );
            INSERT INTO turnos(
                id,dia_operativo_id,fecha_inicio,fecha_inicio_real,
                representante,estado
            ) VALUES(315,1,'2026-08-12','2026-08-12','Administrador sistema','ABIERTO');
            """
        )


class _SyncService:
    def synchronize_once(self):
        return {"pushed": 0, "pulled": 0}


class _SessionService:
    def __init__(self, central: OperationalSession, *, attached: bool = False):
        self.central = central
        self.attached = attached
        self.attach_calls = 0
        self.heartbeat_calls = 0

    def ensure_schema(self):
        return None

    def get_operational_session(self):
        return self.central

    def get_central_admission_operational_state(
        self,
        *,
        current_user,
        current_session_id,
        current_device_id,
        local_generation=None,
    ):
        matches = same_user(self.central, current_user)
        attached = self.attached
        return OperationalState(
            operational_session_id=self.central.operational_session_id,
            generation=self.central.generation,
            active_user_id=self.central.active_user_id,
            active_username=self.central.active_username,
            active_user_display_name=self.central.active_user_display_name,
            turn_id=self.central.turn_id,
            primary_device_id=self.central.primary_device_id,
            primary_login_session_id=self.central.primary_login_session_id,
            local_device_id=current_device_id,
            local_login_session_id=current_session_id,
            device_role=StationRole.SECONDARY if attached else StationRole.DETACHED,
            device_attached=attached,
            user_matches_operational=matches,
            write_allowed=attached and matches,
            reason_code=(
                "ALLOWED"
                if attached and matches
                else "READONLY_DIFFERENT_USER"
                if attached
                else "PRIMARY_USER_CHANGED"
            ),
            message=(
                "Conectado."
                if attached
                else "La sesión principal de Admisión cambió."
            ),
            invalidated_reason="" if attached else "PRIMARY_USER_CHANGED",
            operational_source_id=self.central.operational_source_id,
            status=self.central.status,
            updated_at=self.central.updated_at,
        )

    def attach_device(self, **_kwargs):
        self.attach_calls += 1
        self.attached = True
        return DeviceAttachment(
            self.central, StationRole.SECONDARY, True, "Conectado."
        )

    def rebind_login_session_to_operational_state(self, **kwargs):
        return self.attach_device(**kwargs)

    def heartbeat(self, **_kwargs):
        self.heartbeat_calls += 1


class _PrimaryReloginService(_SessionService):
    def __init__(self, central: OperationalSession):
        super().__init__(central, attached=True)
        self.rebind_calls = 0

    def get_central_admission_operational_state(
        self,
        *,
        current_user,
        current_session_id,
        current_device_id,
        local_generation=None,
    ):
        matches = same_user(self.central, current_user)
        login_matches = self.central.primary_login_session_id == current_session_id
        return OperationalState(
            operational_session_id=self.central.operational_session_id,
            generation=self.central.generation,
            active_user_id=self.central.active_user_id,
            active_username=self.central.active_username,
            active_user_display_name=self.central.active_user_display_name,
            turn_id=self.central.turn_id,
            primary_device_id=self.central.primary_device_id,
            primary_login_session_id=self.central.primary_login_session_id,
            local_device_id=current_device_id,
            local_login_session_id=current_session_id,
            device_role=StationRole.PRIMARY,
            device_attached=True,
            user_matches_operational=matches,
            write_allowed=matches and login_matches,
            reason_code="ALLOWED" if login_matches else "READONLY_LOGIN_SESSION_STALE",
            message="Conectado." if login_matches else "Sesión de login anterior.",
            operational_source_id=self.central.operational_source_id,
            status=self.central.status,
            updated_at=self.central.updated_at,
        )

    def rebind_login_session_to_operational_state(self, **kwargs):
        self.rebind_calls += 1
        self.central = replace(
            self.central,
            primary_login_session_id=str(kwargs["login_session_id"]),
        )
        return DeviceAttachment(self.central, StationRole.PRIMARY, True, "Conectado.")


def _runtime(tmp_path: Path, user: dict, service: _SessionService):
    host = SimpleNamespace(
        user=user,
        device_id="PC-2",
        session_id="SECONDARY-LOGIN",
        device_name="Secundaria",
        current_shift={
            "turn_id": 315,
            "owner_username": "admin",
            "generation": 41,
        },
        connection_factory=lambda: None,
        logger=logging.getLogger("test.admission.reconciliation"),
    )
    database_path = tmp_path / "pacientes.db"
    _local_database(database_path)
    runtime = _HybridAdmissionRuntime(host)
    runtime.session_service = service
    runtime.store = OfflineAdmissionStore(database_path)
    runtime.store.initialize()
    runtime.sync_service = _SyncService()
    runtime._bound_database = SimpleNamespace()
    return runtime, host, database_path


def test_primary_same_user_relogin_rebinds_without_turn_or_generation_change(tmp_path):
    service = _PrimaryReloginService(
        _session(username="admin", user_id="1", generation=80)
    )
    runtime, host, _database_path = _runtime(
        tmp_path,
        {
            "id": 1,
            "username": "admin",
            "full_name": "Administrador del sistema",
            "role": "administrador",
        },
        service,
    )
    host.device_id = "PC-1"
    host.session_id = "PRIMARY-LOGIN-B"

    state = runtime.refresh_operational_state(force_remote=True)

    assert service.rebind_calls == 1
    assert state["role"] == "PRIMARY"
    assert state["writable"] is True
    assert state["turn_id"] == 316
    assert state["generation"] == 80
    assert service.central.primary_login_session_id == "PRIMARY-LOGIN-B"


def test_primary_auxiliary_then_operational_user_reattaches_same_turn(tmp_path):
    service = _PrimaryReloginService(
        _session(username="admin", user_id="1", generation=80)
    )
    runtime, host, _database_path = _runtime(
        tmp_path,
        {"id": 9, "username": "aux", "role": "auxiliar"},
        service,
    )
    host.device_id = "PC-1"
    host.session_id = "AUXILIARY-LOGIN"

    auxiliary_state = runtime.refresh_operational_state(force_remote=True)

    # The device is still bound to the previous PRIMARY login.  That stale
    # login binding is the most specific read-only cause; a different user
    # must never rebind it implicitly.
    assert auxiliary_state["reason_code"] == "READONLY_LOGIN_SESSION_STALE"
    assert auxiliary_state["writable"] is False
    assert auxiliary_state["turn_id"] == 316
    assert auxiliary_state["generation"] == 80
    assert service.rebind_calls == 0

    host.user = {
        "id": 1,
        "username": "admin",
        "full_name": "Administrador del sistema",
        "role": "administrador",
    }
    host.session_id = "PRIMARY-LOGIN-C"
    restored_state = runtime.refresh_operational_state(force_remote=True)

    assert service.rebind_calls == 1
    assert restored_state["role"] == "PRIMARY"
    assert restored_state["writable"] is True
    assert restored_state["turn_id"] == 316
    assert restored_state["generation"] == 80
    assert service.central.primary_login_session_id == "PRIMARY-LOGIN-C"


def test_matching_secondary_rebinds_and_applies_central_generation(tmp_path):
    service = _SessionService(_session())
    runtime, host, database_path = _runtime(
        tmp_path,
        {"id": 8, "username": "fernando", "role": "administrador"},
        service,
    )
    runtime.attachment = DeviceAttachment(
        _session(username="admin", user_id="7", generation=41),
        StationRole.SECONDARY,
        False,
    )

    state = runtime.synchronize()

    assert service.attach_calls == 1
    assert state["role"] == "SECONDARY"
    assert state["writable"] is True
    assert state["generation"] == 42
    assert state["turn_id"] == 316
    assert state["offline"] is False
    assert state["force_logout_required"] is False
    assert host.current_shift["owner_username"] == "fernando"
    assert state["local_mirror_pending"] is True
    assert state["sync_state"] == "LOCAL_MIRROR_PENDING"

    mirrored = runtime.apply_operational_mirror_to_v15()

    assert mirrored["sync_state"] == "ONLINE_SYNCED"
    with sqlite3.connect(database_path) as con:
        representative = con.execute(
            "SELECT representante FROM turnos WHERE estado='ABIERTO'"
        ).fetchone()[0]
        mirror = con.execute(
            "SELECT generation,operational_turn_id,active_username "
            "FROM sync_runtime_context WHERE singleton=1"
        ).fetchone()
    assert representative == "FERNANDO JHOEL GUERRERO"
    assert mirror == (42, 316, "fernando")


def test_bootstrap_snapshot_still_performs_atomic_secondary_attachment(tmp_path):
    service = _SessionService(_session())
    runtime, _host, _database_path = _runtime(
        tmp_path,
        {"id": 8, "username": "fernando", "role": "administrador"},
        service,
    )
    runtime._seed_bootstrap_operational_snapshot({
        "operational_session_id": "operational-1",
        "operational_source_id": "source-central",
        "active_user_id": "8",
        "active_username": "fernando",
        "active_display_name": "FERNANDO JHOEL GUERRERO",
        "turn_id": 316,
        "generation": 42,
        "primary_device_id": "PC-1",
        "status": "ACTIVE",
    })

    state = runtime.synchronize()

    assert service.attach_calls == 1
    assert state["role"] == "SECONDARY"
    assert state["writable"] is True
    assert state["turn_id"] == 316
    assert state["generation"] == 42


def test_local_mirror_failure_keeps_central_state_online_and_retryable(tmp_path):
    service = _SessionService(_session())
    runtime, host, _database_path = _runtime(
        tmp_path,
        {"id": 8, "username": "fernando", "role": "administrador"},
        service,
    )
    runtime.attachment = DeviceAttachment(
        _session(username="admin", user_id="7", generation=41),
        StationRole.SECONDARY,
        False,
    )
    runtime.synchronize()
    runtime.store.apply_remote_operational_state = lambda *_args, **_kwargs: (
        (_ for _ in ()).throw(PermissionError("locked"))
    )

    with pytest.raises(PermissionError):
        runtime.apply_operational_mirror_to_v15()
    retryable = runtime.mark_operational_mirror_pending()

    assert retryable["offline"] is False
    assert retryable["sync_state"] == "LOCAL_MIRROR_PENDING"
    assert retryable["generation"] == 42
    assert retryable["turn_id"] == 316
    assert host.current_shift["owner_username"] == "fernando"


def test_temporary_network_failure_retains_last_confirmed_turn_identity(tmp_path):
    service = _SessionService(_session())
    runtime, host, _database_path = _runtime(
        tmp_path,
        {"id": 8, "username": "fernando", "role": "administrador"},
        service,
    )
    confirmed = runtime.synchronize()
    assert confirmed["turn_id"] == 316
    runtime.apply_operational_mirror_to_v15()

    def unavailable(**_kwargs):
        raise DatabaseTemporarilyOffline("temporary")

    service.get_central_admission_operational_state = unavailable
    stale = runtime.synchronize()

    assert stale["turn_id"] == 316
    assert stale["generation"] == 42
    assert stale["operational_source_id"] == "source-central"
    assert stale["reason_code"] == "CENTRAL_TEMPORARILY_UNAVAILABLE"
    assert stale["offline"] is True
    assert host.current_shift["turn_id"] == 316


def test_old_secondary_user_reattaches_during_reconciliation(tmp_path):
    service = _SessionService(_session())
    runtime, host, _database_path = _runtime(
        tmp_path,
        {"id": 7, "username": "admin", "role": "administrador"},
        service,
    )
    runtime.attachment = DeviceAttachment(
        _session(username="admin", user_id="7", generation=41),
        StationRole.SECONDARY,
        False,
    )

    state = runtime.synchronize()

    # Reconciliation now performs the canonical attach immediately so the
    # UI cannot remain DETACHED until a second login.
    assert service.attach_calls == 1
    assert state["offline"] is False
    assert state["force_logout_required"] is False
    assert state["generation"] == 42
    assert host.current_shift["turn_id"] == 316
    assert host.current_shift["owner_username"] == "fernando"


def test_auxiliary_attaches_without_changing_central_operator_turn_or_generation(tmp_path):
    service = _SessionService(_session())
    runtime, host, _database_path = _runtime(
        tmp_path,
        {"id": 9, "username": "aux", "role": "auxiliar"},
        service,
    )

    state = runtime.synchronize()

    assert service.attach_calls == 1
    assert state["reason_code"] == "READONLY_DIFFERENT_USER"
    assert state["writable"] is False
    assert state["generation"] == 42
    assert state["turn_id"] == 316
    assert host.current_shift["owner_username"] == "fernando"
    assert service.central.active_username == "fernando"
    assert service.central.generation == 42


def test_secondary_never_reaches_primary_turn_mutation(tmp_path, caplog):
    service = _SessionService(_session(), attached=True)
    runtime, _host, _database_path = _runtime(
        tmp_path,
        {"id": 8, "username": "fernando", "role": "administrador"},
        service,
    )
    runtime.attachment = DeviceAttachment(
        _session(), StationRole.SECONDARY, True, "Conectado."
    )
    runtime._operational_state = service.get_central_admission_operational_state(
        current_user=runtime.current_user,
        current_session_id=runtime.login_session_id,
        current_device_id=runtime.device_id,
    )

    class Database:
        called = False

        def cerrar_turno_existente(self, *_args, **_kwargs):
            self.called = True

    database = Database()
    proxy = _HybridDatabaseProxy(database, runtime)

    assert proxy.get_operational_station_snapshot()["role"] == "SECONDARY"
    with caplog.at_level(logging.ERROR), pytest.raises(AdmissionWriteBlocked):
        proxy.cerrar_turno_existente({"turno_id": 315})

    assert database.called is False
    assert "SECONDARY_PRIMARY_TRANSITION_GUARD_TRIGGERED" in caplog.text


def test_v15_secondary_adopts_memory_snapshot_without_opening_turn_dialog(monkeypatch):
    _load_v15_modules(Path(DEFAULT_V15_ROOT))
    v15 = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    snapshot = {
        "role": "SECONDARY",
        "writable": True,
        "active_user_id": "8",
        "active_username": "fernando",
        "active_user_display_name": "FERNANDO JHOEL GUERRERO",
        "turn_id": 316,
        "generation": 42,
        "operational_session_id": "operational-1",
        "operational_source_id": "source-central",
    }
    app = object.__new__(v15.App)
    app.db = SimpleNamespace(
        get_operational_station_snapshot=lambda: dict(snapshot)
    )
    app.current_shift_context = {
        "owner_username": "admin",
        "turn_id": 315,
        "generation": 41,
    }
    app.session_context = SimpleNamespace(
        launched_from_billing=True,
        username="fernando",
    )
    app._actualizar_turno_visual_en_vivo = lambda: None
    app.set_status = lambda *_args: None
    app._crear_toplevel_estable = lambda *_args: pytest.fail(
        "SECONDARY no debe abrir Configurar turno"
    )
    monkeypatch.setattr(
        v15,
        "cargar_turno_config",
        lambda *_args, **_kwargs: pytest.fail(
            "SECONDARY no debe consultar la configuración local para decidir"
        ),
    )

    assert v15.App._asegurar_turno_de_sesion(app) is True
    v15.App._dialogo_turno(app)

    assert app.current_shift_context["owner_username"] == "fernando"
    assert app.current_shift_context["turn_id"] == 316
    assert app.current_shift_context["generation"] == 42


def test_incomplete_central_turn_does_not_reuse_expired_local_configuration(
    tmp_path, monkeypatch
):
    _load_v15_modules(Path(DEFAULT_V15_ROOT))
    v15 = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    service = _SessionService(_session(), attached=True)
    runtime, _host, _database_path = _runtime(
        tmp_path,
        {"id": 8, "username": "fernando", "role": "administrador"},
        service,
    )
    runtime._bound_database = object.__new__(v15.DatabaseManager)
    saved = {}
    now = datetime.now(timezone.utc).astimezone().replace(microsecond=0)
    expected_date = now.date() - (
        timedelta(days=1) if now.time() < time(8, 0) else timedelta()
    )
    monkeypatch.setattr(
        v15,
        "cargar_turno_config",
        lambda **_kwargs: {
            "representante": "Administrador sistema",
            "turno_codigo": "8AM_8AM",
            "fecha_base": expected_date - timedelta(days=5),
            "inicio_real_dt": now.replace(tzinfo=None) - timedelta(days=5),
        },
    )
    monkeypatch.setattr(
        v15,
        "guardar_turno_config",
        lambda representative, code, base, started: saved.update(
            representative=representative,
            code=code,
            base=base,
            started=started,
        ) or True,
    )

    runtime._mirror_v15_turn_config(
        replace(_session(), turn_started_at=now.isoformat())
    )

    # PostgreSQL is authoritative online.  A snapshot without a canonical
    # turn_code must not be completed from an expired local JSON mirror.
    assert saved == {}
