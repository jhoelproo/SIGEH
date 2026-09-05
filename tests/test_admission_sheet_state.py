from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_sheet_state import (
    ConfirmedTurnConfig,
    SheetOperationalError,
    confirmed_turn_config,
    validate_sheet_snapshot_identity,
)
from admission_v15_adapter import load_v15_application_module


def state(**changes):
    return {
        "operational_session_id": "session",
        "operational_source_id": "source",
        "turn_id": 3949,
        "generation": 10,
        "operational_revision": 12,
        "active_user_id": "REP-A",
        "active_username": "representative",
        "active_user_display_name": "REPRESENTATIVE",
        "status": "ACTIVE",
        "turn_code": "8AM_8PM",
        "turn_started_at": "2026-09-04T08:00:00",
        "writable": True,
        "role": "PRIMARY",
        **changes,
    }


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"turn_id": None}, "NO_TURN_CONFIGURED"),
        ({"operational_source_id": ""}, "NO_TURN_CONFIGURED"),
        ({"operational_source_id": "   "}, "NO_TURN_CONFIGURED"),
        ({"turn_id": 0}, "NO_TURN_CONFIGURED"),
        ({"turn_id": -1}, "LOCAL_STATE_STALE"),
        ({"turn_id": "invalid"}, "LOCAL_STATE_STALE"),
        ({"status": "CLOSED"}, "NO_TURN_CONFIGURED"),
        ({"writable": False}, "SESSION_INVALID"),
        ({"offline": True, "writable": False}, "CENTRAL_UNAVAILABLE"),
        ({"turn_started_at": "invalid"}, "LOCAL_STATE_STALE"),
        ({"turn_code": ""}, "LOCAL_STATE_STALE"),
        ({"active_username": "", "active_user_display_name": ""}, "LOCAL_STATE_STALE"),
    ],
)
def test_invalid_state_is_not_blank_turn(changes, code):
    with pytest.raises(SheetOperationalError) as error:
        confirmed_turn_config(state(**changes))
    assert error.value.code == code


def test_no_snapshot_means_unknown_not_empty():
    with pytest.raises(SheetOperationalError) as error:
        confirmed_turn_config({})
    assert error.value.code == "CENTRAL_UNAVAILABLE"


@pytest.mark.parametrize("started", [datetime(2026, 9, 4, 8), "2026-09-04T08:00:00Z"])
def test_current_representative_not_authenticated_user(started):
    snapshot = state(turn_started_at=started, authenticated_user_id="USER-B")
    config = confirmed_turn_config(snapshot)
    assert isinstance(config, ConfirmedTurnConfig)
    assert config["turn_id"] == 3949
    assert config["representante"] == "REPRESENTATIVE"
    assert snapshot["generation"] == 10 and snapshot["operational_revision"] == 12


def test_offline_authorized_snapshot_remains_usable():
    assert confirmed_turn_config(state(offline=True))["turn_id"] == 3949


def test_legacy_date_validation_preserved_but_central_active_wins():
    v15 = load_v15_application_module()
    config = confirmed_turn_config(state())
    assert v15.turno_config_es_vigente(config, datetime(2026, 10, 1))
    assert not v15.turno_config_es_vigente(dict(config), datetime(2026, 10, 1))


def test_gui_generation_adopts_central_without_reading_json(monkeypatch):
    v15 = load_v15_application_module()
    snapshot = state()
    adopted = []
    window = SimpleNamespace(
        db=SimpleNamespace(get_operational_station_snapshot=lambda: snapshot),
        _snapshot_operacional_integrado=lambda: snapshot,
        apply_operational_snapshot=adopted.append,
    )
    monkeypatch.setattr(
        v15,
        "cargar_turno_config",
        lambda *_a, **_kw: pytest.fail("Local mirror used as authority"),
    )
    result = v15.App._generation_turn_config(window)
    assert result["turn_id"] == snapshot["turn_id"]
    assert adopted == [snapshot]


def test_sheet_worker_refreshes_then_applies_without_handoff():
    v15 = load_v15_application_module()
    events = []
    runtime = SimpleNamespace(
        offline=False,
        refresh_operational_state=lambda **_kw: events.append("read"),
        require_write=lambda: events.append("guard"),
        state=state,
    )

    def background(_message, work, *, al_terminar, al_error):
        al_terminar(work())

    window = SimpleNamespace(
        db=SimpleNamespace(_runtime=runtime),
        _ejecutar_en_segundo_plano=background,
        apply_operational_snapshot=lambda _state: events.append("adopt"),
        generar_pdf=lambda: events.append("resume"),
    )
    assert v15.App._begin_sheet_operational_validation(window)
    assert events == ["read", "guard", "adopt", "resume"]
    assert not v15.App._begin_sheet_operational_validation(window)


@pytest.mark.parametrize(
    "field", ["turn_id", "operational_source_id", "generation", "operational_revision"]
)
def test_prepared_sheet_cannot_cross_snapshot_revision(field):
    snapshot = state()
    config = confirmed_turn_config(snapshot)
    validate_sheet_snapshot_identity(config, snapshot)
    snapshot[field] = "changed"
    with pytest.raises(SheetOperationalError, match="estado operacional cambió"):
        validate_sheet_snapshot_identity(config, snapshot)


def test_gui_config_legacy_fallback_and_invalid_central(monkeypatch):
    v15 = load_v15_application_module()
    legacy = {"legacy": True}
    monkeypatch.setattr(v15, "cargar_turno_config", lambda: legacy)
    assert v15.App._generation_turn_config(SimpleNamespace(db=object())) is legacy
    window = SimpleNamespace(
        db=SimpleNamespace(get_operational_station_snapshot=lambda: {}),
        _snapshot_operacional_integrado=lambda: {},
    )
    with pytest.raises(SheetOperationalError) as error:
        v15.App._generation_turn_config(window)
    assert error.value.code == "CENTRAL_UNAVAILABLE"


@pytest.mark.parametrize("failure", [False, True])
def test_background_offline_or_failure_never_creates_turn(failure):
    v15 = load_v15_application_module()
    runtime = SimpleNamespace(
        offline=True,
        refresh_operational_state=Mock(
            side_effect=AssertionError("Offline network read")
        ),
        require_write=Mock(
            side_effect=RuntimeError("offline lease expired") if failure else None
        ),
        state=lambda: state(offline=True),
    )
    callbacks = {}
    window = SimpleNamespace(
        db=SimpleNamespace(_runtime=runtime),
        _ejecutar_en_segundo_plano=lambda message, work, **kwargs: callbacks.update(
            work=work, **kwargs
        ),
        apply_operational_snapshot=Mock(),
        generar_pdf=Mock(),
        boton_generar_pdf=Mock(),
        set_status=Mock(),
    )
    assert v15.App._begin_sheet_operational_validation(window)
    assert v15.App._begin_sheet_operational_validation(window)  # single flight
    try:
        result = callbacks["work"]()
    except RuntimeError as exc:
        callbacks["al_error"](exc)
    else:
        callbacks["al_terminar"](result)
    assert not window._sheet_validation_running
    assert window.generar_pdf.call_count == (0 if failure else 1)
    assert not runtime.refresh_operational_state.called
    if failure:
        window.boton_generar_pdf.config.assert_called_once()
        assert not window.apply_operational_snapshot.called


def test_delayed_validation_cannot_apply_previous_turn():
    v15 = load_v15_application_module()
    runtime = SimpleNamespace(
        offline=False,
        refresh_operational_state=Mock(),
        require_write=Mock(),
        state=state,
    )
    callbacks = {}
    window = SimpleNamespace(
        db=SimpleNamespace(_runtime=runtime),
        _ejecutar_en_segundo_plano=lambda message, work, **kwargs: callbacks.update(
            work=work, **kwargs
        ),
        apply_operational_snapshot=Mock(),
        generar_pdf=Mock(),
        boton_generar_pdf=Mock(),
        set_status=Mock(),
    )
    assert v15.App._begin_sheet_operational_validation(window)
    old = callbacks["work"]()
    runtime.state = lambda: state(turn_id=3950, generation=11)
    callbacks["al_terminar"](old)
    assert not window.apply_operational_snapshot.called
    assert not window.generar_pdf.called
    assert not window._sheet_validation_running
    window.boton_generar_pdf.config.assert_called_once()


def test_standalone_does_not_start_central_worker():
    v15 = load_v15_application_module()
    assert not v15.App._begin_sheet_operational_validation(SimpleNamespace(db=object()))


@pytest.mark.parametrize("positional", [False, True])
def test_write_proxy_rejects_handoff_between_validation_and_save(positional):
    from admission_v15_adapter import _HybridDatabaseProxy

    config = confirmed_turn_config(state())
    database = SimpleNamespace(guardar_atencion=Mock(return_value=1))
    runtime = SimpleNamespace(require_write=Mock(), state=lambda: state(turn_id=3950))
    proxy = _HybridDatabaseProxy(database, runtime)
    args, kwargs = (
        (({}, "sheet", config), {})
        if positional
        else (({}, "sheet"), {"turno_cfg": config})
    )
    with pytest.raises(SheetOperationalError):
        proxy.guardar_atencion(*args, **kwargs)
    database.guardar_atencion.assert_not_called()
    runtime.state = state
    assert proxy.guardar_atencion(*args, **kwargs) == 1


@pytest.mark.parametrize("pending", [False, True])
def test_generate_sheet_waits_for_validation_then_saves_once(monkeypatch, pending):
    v15 = load_v15_application_module()
    config = confirmed_turn_config(state())
    window = Mock(
        _final_revalidation_ready=False,
        _final_revalidation_in_progress=False,
        _sheet_validation_running=pending,
        app_settings={"validation_confirm_before_generate": False},
    )
    window._validar_campos_o_alertar.return_value = (
        {"Nombre": "SYNTHETIC", "Aseguradora (ARS)": "FUTURO"},
        "GENERAL",
    )
    window._begin_final_patient_revalidation.return_value = False
    window._begin_sheet_operational_validation.return_value = pending
    window._generation_turn_config.return_value = config
    window._buscar_duplicado_turno_actual.return_value = None
    window.db.guardar_atencion.return_value = 1
    window.db.obtener_revision_nss_atencion.return_value = None
    monkeypatch.setattr(
        v15.messagebox,
        "showerror",
        Mock(side_effect=AssertionError("Unexpected GUI error")),
    )
    v15.App.generar_pdf(window)
    assert window.db.guardar_atencion.call_count == int(not pending)
    assert window._iniciar_salida_atencion.call_count == int(not pending)
    window._dialogo_turno.assert_not_called()
    window._turno_pertenece_a_sesion.assert_not_called()
    if not pending:
        assert window.db.guardar_atencion.call_args.kwargs["turno_cfg"] is config
