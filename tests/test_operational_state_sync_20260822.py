from __future__ import annotations

import logging
import importlib
from pathlib import Path
from types import SimpleNamespace

from admission_hybrid import ConnectivityState, OperationalState, StationRole
from admission_v15_adapter import DEFAULT_V15_ROOT, _HybridAdmissionRuntime, _load_v15_modules


def _state(
    *,
    representative_id: str = "22",
    representative_username: str = "aux-test",
    representative_name: str = "AUX TEST",
    turn_id: int | None = 702,
    turn_code: str = "8AM_8AM",
    generation: int = 20,
    operational_revision: int = 50,
    lease_generation: int = 3,
) -> OperationalState:
    return OperationalState(
        operational_session_id="operational-central",
        generation=generation,
        active_user_id=representative_id,
        active_username=representative_username,
        active_user_display_name=representative_name,
        turn_id=turn_id,
        turn_code=turn_code,
        primary_device_id="PC-ADMIN",
        primary_login_session_id="login-admin",
        local_device_id="PC-TEST",
        local_login_session_id="login-test",
        device_role=StationRole.SECONDARY,
        device_attached=True,
        user_matches_operational=True,
        write_allowed=True,
        connection_state=ConnectivityState.CONNECTED,
        operational_source_id="source-central",
        status="ACTIVE",
        turn_started_at="2026-08-22T08:00:00+00:00",
        turn_ends_at="2026-08-23T08:00:00+00:00",
        operational_revision=operational_revision,
        lease_generation=lease_generation,
    )


def _runtime(*, cached_shift=None):
    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime.host = SimpleNamespace(
        current_shift=dict(cached_shift or {}),
        device_id="PC-TEST",
        session_id="login-test",
        user={"id": 22, "username": "aux-test", "role": "auxiliar"},
    )
    runtime.attachment = None
    runtime._operational_state = None
    runtime.offline = True
    runtime.status_message = ""
    runtime.logger = logging.getLogger("test.operational-state-sync")
    runtime.StationRole = StationRole
    return runtime


def test_same_central_snapshot_is_applied_identically_by_two_stations():
    central = _state()
    pc1 = _runtime()
    pc2 = _runtime()

    assert pc1.apply_operational_snapshot(central, source="pc1") is True
    assert pc2.apply_operational_snapshot(central, source="pc2") is True

    expected = {
        "owner_user_id": "22",
        "owner_username": "aux-test",
        "representative_display_name": "AUX TEST",
        "turn_id": 702,
        "turn_code": "8AM_8AM",
        "generation": 20,
        "operational_revision": 50,
        "operational_session_id": "operational-central",
        "operational_source_id": "source-central",
    }
    for runtime in (pc1, pc2):
        for field, value in expected.items():
            assert runtime.host.current_shift[field] == value


def test_remote_representative_and_turn_change_apply_without_relogin():
    runtime = _runtime()
    assert runtime.apply_operational_snapshot(_state(), source="initial") is True

    changed = _state(
        representative_id="24",
        representative_username="genesis",
        representative_name="GENESIS TORRES",
        turn_id=703,
        turn_code="8PM_8AM",
        generation=21,
        operational_revision=51,
    )
    assert runtime.apply_operational_snapshot(changed, source="remote_poll") is True

    assert runtime.host.current_shift["owner_user_id"] == "24"
    assert runtime.host.current_shift["representative_display_name"] == "GENESIS TORRES"
    assert runtime.host.current_shift["turn_id"] == 703
    assert runtime.host.current_shift["turn_code"] == "8PM_8AM"
    assert runtime.host.current_shift["operational_revision"] == 51


def test_restart_central_snapshot_replaces_stale_local_cache():
    runtime = _runtime(
        cached_shift={
            "operational_session_id": "operational-central",
            "owner_username": "admin",
            "turn_id": 701,
            "turn_code": "8AM_8PM",
            "generation": 19,
            "operational_revision": 49,
        }
    )

    runtime._seed_bootstrap_operational_snapshot(_state().as_mapping())

    assert runtime.host.current_shift["owner_username"] == "aux-test"
    assert runtime.host.current_shift["turn_id"] == 702
    assert runtime.host.current_shift["turn_code"] == "8AM_8AM"
    assert runtime.host.current_shift["operational_revision"] == 50


def test_older_snapshot_cannot_rollback_current_operational_state():
    runtime = _runtime()
    current = _state(operational_revision=50, generation=20)
    stale = _state(
        representative_username="admin",
        representative_name="ADMIN",
        turn_id=701,
        turn_code="8AM_8PM",
        operational_revision=49,
        generation=19,
    )

    assert runtime.apply_operational_snapshot(current, source="current") is True
    assert runtime.apply_operational_snapshot(stale, source="stale_remote") is False
    assert runtime.host.current_shift["owner_username"] == "aux-test"
    assert runtime.host.current_shift["turn_id"] == 702
    assert runtime.host.current_shift["operational_revision"] == 50


def test_v15_header_sidebar_and_open_config_use_the_central_snapshot(monkeypatch):
    _load_v15_modules(Path(DEFAULT_V15_ROOT))
    v15 = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")

    class _Value:
        def __init__(self):
            self.value = ""

        def set(self, value):
            self.value = value

    app = object.__new__(v15.App)
    app.current_shift_context = {
        "operational_session_id": "operational-central",
        "owner_username": "admin",
        "turn_id": 701,
        "turn_code": "8AM_8PM",
        "generation": 19,
        "operational_revision": 49,
    }
    app.context = SimpleNamespace(current_shift={})
    app.turno_header_var = _Value()
    app.turno_panel_var = _Value()
    app.representante_panel_var = _Value()
    app._refrescar_resumen_en_vivo = lambda: None
    app._set_turn_change_controls_enabled = lambda _enabled: None
    config_refreshes = []
    app._refresh_primary_config_panel = lambda: config_refreshes.append(True)
    app.root = SimpleNamespace(update_idletasks=lambda: None)
    monkeypatch.setattr(
        v15,
        "cargar_turno_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("La UI central no puede leer turnos_config.json")
        ),
    )

    assert v15.App.apply_operational_snapshot(app, _state().as_mapping()) is True

    assert app.turno_header_var.value == "8:00 AM → 8:00 AM"
    assert app.turno_panel_var.value == "Turno:\n8:00 AM → 8:00 AM"
    assert app.representante_panel_var.value == "Representante:\nAUX TEST"
    assert app.context.current_shift["turn_id"] == 702
    assert app.context.current_shift["representative_display_name"] == "AUX TEST"
    assert config_refreshes == [True]
