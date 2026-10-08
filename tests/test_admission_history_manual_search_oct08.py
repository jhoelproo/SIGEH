from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QDateEdit,
    QMenu,
    QTableWidget,
)
import pytest

from admission_v15_adapter import load_v15_application_module


class _FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 8, 14, 30, tzinfo=tz)


@pytest.fixture
def history_window(monkeypatch):
    v15 = load_v15_application_module()
    monkeypatch.setattr(v15, "datetime", _FixedClock)
    root = v15.tb.Window()
    window = v15.tk.Toplevel(root)
    scheduled = []

    def schedule(delay, callback):
        scheduled.append((delay, callback))
        return len(scheduled)

    window.after = schedule
    window.after_cancel = lambda _token: None
    attention = {
        "id": 342,
        "fecha": "07/10/2026",
        "hora": "22:00",
        "nombre": "PACIENTE",
        "hoja": "GENERAL",
        "ars": "HUMANO",
        "nss": "190065476",
        "cedula": "",
    }
    central = Mock(return_value=[attention])
    cached = Mock(return_value=[])
    runtime = SimpleNamespace(_network_retry_gate=SimpleNamespace(ready=True))
    database = SimpleNamespace(
        uses_central_history=True,
        _runtime=runtime,
        obtener_turnos_historial=lambda: {},
        listar_atenciones_filtradas=central,
        list_history_cache_local=cached,
    )
    app = v15.App.__new__(v15.App)
    app._crear_toplevel_estable = lambda *_args: window
    app._paleta_visual_actual = lambda: {
        "card": "#0E1B2B",
        "text": "#FFFFFF",
        "entry": "#112235",
        "border": "#334455",
        "selected_bg": "#004488",
        "selected_fg": "#FFFFFF",
        "accent": "#0088FF",
    }
    app._bind_esc_cerrar = lambda _win: None
    app._crear_header_ventana = lambda *_args: None
    app._puede = lambda _capability: False
    app._obtener_ars_cache = lambda **_kwargs: ["HUMANO"]
    app.app_settings = {}
    app.db = database
    app.event_bus = SimpleNamespace()
    app.set_status = lambda *_args: None

    def run_worker(_label, function, *, al_terminar, al_error):
        try:
            al_terminar(function())
        except Exception as exc:
            al_error(exc)
            raise

    app._ejecutar_en_segundo_plano = run_worker
    app.abrir_historial()
    QApplication.processEvents()
    for delay, callback in tuple(scheduled):
        if delay in (10, 50):
            callback()
    scheduled.clear()
    controls = SimpleNamespace(
        app=app,
        window=window,
        central=central,
        cached=cached,
        runtime=runtime,
        scheduled=scheduled,
        date=window.findChild(QDateEdit),
        table=window.findChild(QTableWidget),
    )
    try:
        yield controls
    finally:
        window.destroy()
        root.destroy()
        QApplication.processEvents()


def _action(controls, name):
    return next(
        action
        for menu in controls.window.findChildren(QMenu)
        for action in menu.actions()
        if action.text() == name
    )


def _run_scheduled(controls):
    while controls.scheduled:
        _delay, callback = controls.scheduled.pop(0)
        callback()


def _search(controls):
    next(
        button
        for button in controls.window.findChildren(QAbstractButton)
        if "Buscar" in button.text()
    ).click()
    _run_scheduled(controls)


def test_manual_search_queries_central_records_missing_from_local_cache(history_window):
    controls = history_window
    _action(controls, "Por fecha").trigger()
    controls.date.setDate(QDate(2026, 10, 7))
    _run_scheduled(controls)
    _search(controls)
    assert controls.central.call_args.kwargs["fecha_txt"] == "07/10/2026"
    assert controls.table.rowCount() == 1
    assert controls.table.item(0, 0).text() == "342"


@pytest.mark.parametrize("signal", ["calendar", "editing"])
def test_calendar_and_manual_date_completion_refresh_selected_date(
    history_window, signal
):
    controls = history_window
    _action(controls, "Por fecha").trigger()
    _run_scheduled(controls)
    controls.central.reset_mock()
    controls.date.setDate(QDate(2026, 10, 7))
    if signal == "calendar":
        controls.date.calendarWidget().clicked.emit(QDate(2026, 10, 7))
    else:
        controls.date.editingFinished.emit()
    _run_scheduled(controls)
    assert controls.central.call_count == 1
    assert controls.central.call_args.kwargs["fecha_txt"] == "07/10/2026"


def test_yesterday_shortcut_sets_calendar_date_and_runs_search(history_window):
    controls = history_window
    _action(controls, "Ayer").trigger()
    _run_scheduled(controls)
    assert controls.date.date() == QDate(2026, 10, 7)
    assert controls.central.call_args.kwargs["modo"] == "Por fecha"
    assert controls.central.call_args.kwargs["fecha_txt"] == "07/10/2026"


def test_manual_search_keeps_cache_while_network_retry_is_deferred(history_window):
    controls = history_window
    controls.runtime._network_retry_gate.ready = False
    _search(controls)
    assert controls.central.call_count == 0
    assert controls.cached.call_count == 2


def test_automatic_attention_refresh_keeps_central_queries_coalesced_out(
    history_window,
):
    controls = history_window
    controls.app.request_history_refresh("attention_event", immediate=True)
    _run_scheduled(controls)
    assert controls.central.call_count == 0
    assert controls.cached.call_count == 2


def test_manual_search_without_hybrid_retry_gate_preserves_existing_database_api(
    history_window,
):
    controls = history_window
    del controls.app.db._runtime
    _search(controls)
    assert controls.central.call_count == 1


def test_automatic_event_cannot_replace_a_scheduled_manual_central_search(
    history_window,
):
    controls = history_window
    controls.app.request_history_refresh("manual_search", immediate=True)
    controls.app.request_history_refresh("attention_event")
    _run_scheduled(controls)
    assert controls.central.call_count == 1


def test_manual_search_queued_while_busy_survives_later_automatic_event(history_window):
    controls = history_window
    jobs = []
    controls.app._ejecutar_en_segundo_plano = lambda _label, function, **callbacks: (
        jobs.append((function, callbacks))
    )
    controls.app.request_history_refresh("attention_event", immediate=True)
    _run_scheduled(controls)
    _search(controls)
    controls.app.request_history_refresh("attention_event")
    function, callbacks = jobs.pop(0)
    callbacks["al_terminar"](function())
    _run_scheduled(controls)
    function, callbacks = jobs.pop(0)
    callbacks["al_terminar"](function())
    assert controls.central.call_count == 1
    controls.app.request_history_refresh("attention_event", immediate=True)
    _run_scheduled(controls)
    function, callbacks = jobs.pop(0)
    callbacks["al_terminar"](function())
    assert controls.central.call_count == 1


def test_coalesced_manual_search_respects_network_backoff(history_window):
    controls = history_window
    controls.runtime._network_retry_gate.ready = False
    _search(controls)
    controls.app.request_history_refresh("attention_event")
    _run_scheduled(controls)
    assert controls.central.call_count == 0
    controls.runtime._network_retry_gate.ready = True
    _search(controls)
    assert controls.central.call_count == 1
