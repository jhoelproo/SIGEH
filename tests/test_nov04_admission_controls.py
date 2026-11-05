from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDateEdit, QLineEdit

from admission_v15_adapter import load_v15_application_module
from tests.test_admission_history_manual_search_oct08 import (
    history_window as history_window,
    _action,
    _search,
    _run_scheduled,
)


@pytest.mark.parametrize("allowed", [True, False])
def test_operational_refresh_updates_button_as_well_as_menu(allowed):
    module = load_v15_application_module()
    button = Mock()
    owner = SimpleNamespace(
        boton_cambiar_turno=button,
        can_change_admission_turn=lambda: (allowed, "", ""),
        change_turn_menu_action=Mock(),
        db=SimpleNamespace(_runtime=None),
    )
    module.App._refresh_actions_menu_state(owner)
    button.configure.assert_called_once_with(state="normal" if allowed else "disabled")


def test_empty_repeated_search_does_not_leave_a_loading_row(history_window):
    controls = history_window
    controls.central.return_value = []
    _search(controls)
    _search(controls)
    assert "No se encontraron" in controls.table.item(0, 3).text()


def test_history_handles_uninsured_search_and_recovers_from_failed_load(history_window):
    controls = history_window
    controls.app.db.listar_atenciones_sin_seguro = Mock(return_value=[])
    _action(controls, "Sin seguro").trigger()
    _search(controls)
    assert controls.app.db.listar_atenciones_sin_seguro.called
    jobs = []
    controls.app._ejecutar_en_segundo_plano = lambda _label, operation, **handlers: (
        jobs.append(handlers)
    )
    _search(controls)
    jobs[-1]["al_error"](RuntimeError("synthetic"))
    _run_scheduled(controls)
    owner = SimpleNamespace()
    load_v15_application_module().App._refresh_turn_change_button(owner, True)


def test_search_during_initial_cache_load_is_not_lost(history_window):
    controls = history_window
    jobs = []
    controls.app._ejecutar_en_segundo_plano = lambda _label, function, **callbacks: (
        jobs.append((function, callbacks))
    )
    controls.app.request_history_refresh("attention_event", immediate=True)
    _run_scheduled(controls)
    controls.app.var_bus.set("PACIENTE")
    _search(controls)
    function, callbacks = jobs.pop(0)
    callbacks["al_terminar"](function())
    _run_scheduled(controls)
    function, callbacks = jobs.pop(0)
    callbacks["al_terminar"](function())
    assert controls.central.call_args.kwargs["filtro_texto"] == "PACIENTE"


def test_history_accepts_typing_immediately_and_interval_endpoints(history_window):
    controls = history_window
    controls.window.show()
    _run_scheduled(controls)
    QTest.qWait(20)
    entry = controls.window.findChild(QLineEdit)
    assert entry.hasFocus()
    QTest.keyClicks(entry, "123456")
    assert entry.text() == "123456"
    _action(controls, "Entre fechas").trigger()
    start, end = controls.window.findChildren(QDateEdit)
    _run_scheduled(controls)
    QTest.qWait(20)
    assert start.isVisible() and end.isVisible()
    start.setDate(QDate(2026, 11, 1))
    end.setDate(QDate(2026, 11, 4))
    _search(controls)
    assert controls.central.call_args.kwargs["fecha_txt"] == (
        "01/11/2026",
        "04/11/2026",
    )
    controls.window.grab().save("output/nov04-history-range.png")


@pytest.mark.parametrize("closing", ["close", "escape", "destroy"])
def test_turn_dialog_closing_releases_the_button_without_restart(closing):
    module = load_v15_application_module()
    dialog = module.Toplevel()
    controls = Mock()
    owner = SimpleNamespace(
        db=SimpleNamespace(
            _runtime=SimpleNamespace(
                state=lambda: {"active_username": "QA"},
                is_primary_shift_handover=lambda: True,
            )
        ),
        session_context=SimpleNamespace(role=module.ROLE_ADMIN, display_name="QA"),
        app_settings={},
        _turn_change_in_progress=True,
        _turn_change_committing=False,
        _snapshot_operacional_integrado=lambda: {"role": "PRIMARY"},
        _crear_toplevel_estable=lambda *_args: dialog,
        _crear_header_ventana=Mock(),
        _set_turn_change_controls_enabled=controls,
        _bind_esc_cerrar=lambda win: win.bind("<Escape>", lambda _event: win.reject()),
    )
    module.App._dialogo_turno(owner)
    dialog.show()
    QApplication.processEvents()
    if closing == "escape":
        QTest.keyClick(dialog, Qt.Key_Escape)
    else:
        getattr(dialog, closing)()
    QApplication.processEvents()
    assert not owner._turn_change_in_progress
    assert not owner._turn_change_committing
    controls.assert_called_with(True)
