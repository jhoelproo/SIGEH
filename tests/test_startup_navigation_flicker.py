import inspect
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout

import CALCULOS_QT as app
import lanzador


class _TopLevelShowTrace(QObject):
    def __init__(self):
        super().__init__()
        self.shown = []

    def eventFilter(self, watched, event):
        if (
            event.type() == QEvent.Show
            and hasattr(watched, "isWindow")
            and watched.isWindow()
        ):
            self.shown.append(type(watched).__name__)
        return False


def _application():
    return QApplication.instance() or QApplication([])


def test_period_selector_never_shows_parentless_controls():
    qt_app = _application()
    trace = _TopLevelShowTrace()
    qt_app.installEventFilter(trace)
    try:
        selector = app.PeriodSelectorWidget()
        for label, field in selector._fields:
            assert label.parent() is selector
            assert field.parent() is selector
        assert trace.shown == []
        selector.deleteLater()
    finally:
        qt_app.removeEventFilter(trace)


def test_launcher_validates_updates_before_opening_the_application(monkeypatch):
    calls = []
    monkeypatch.setattr(
        lanzador,
        "launch_main_app_immediately",
        lambda: calls.append("local") or 17,
    )
    monkeypatch.setattr(
        lanzador,
        "run_update_check_ui",
        lambda: calls.append("update") or 23,
    )

    assert lanzador.main([]) == 23
    assert calls == ["update"]

    calls.clear()
    assert lanzador.main(["--check-updates"]) == 23
    assert calls == ["update"]


def test_launcher_self_test_fast_path_precedes_requests_and_qt_imports():
    source = inspect.getsource(lanzador)
    early_exit = source.index('if __name__ == "__main__" and _EARLY_ARGS == ["--self-test"]')
    requests_import = source.index("import requests")
    qt_import = source.index("from PySide6.QtCore")
    assert early_exit < requests_import
    assert early_exit < qt_import


def test_three_cold_and_three_warm_launcher_dispatches_use_update_gate(monkeypatch):
    calls = []
    monkeypatch.setattr(
        lanzador,
        "run_update_check_ui",
        lambda: calls.append("update") or 0,
    )
    for _cycle in range(3):
        assert lanzador.main([]) == 0
    for _cycle in range(3):
        assert lanzador.main([]) == 0
    assert calls == ["update"] * 6


def test_launcher_uses_portable_bootstrap_after_update_gate(monkeypatch):
    monkeypatch.setattr(
        lanzador,
        "_fast_launch_main_before_gui_imports",
        lambda: 31,
    )
    assert lanzador.launch_main_app_immediately() == 31


def test_launcher_self_test_does_not_open_update_gate(monkeypatch):
    monkeypatch.setattr(
        lanzador,
        "run_update_check_ui",
        lambda: (_ for _ in ()).throw(AssertionError("update gate opened")),
    )
    assert lanzador.main(["--self-test"]) == 0


def test_launcher_dialog_dispatches_main_and_reports_bootstrap_failure(monkeypatch):
    qt_app = _application()
    errors = []
    monkeypatch.setattr(lanzador, "launch_main_app_immediately", lambda: 0)
    monkeypatch.setattr(qt_app, "quit", lambda: None)
    lanzador.LauncherDialog.launch_main_app(None)

    monkeypatch.setattr(lanzador, "launch_main_app_immediately", lambda: 5)
    monkeypatch.setattr(
        lanzador.QMessageBox,
        "critical",
        lambda *args: errors.append(args),
    )
    lanzador.LauncherDialog.launch_main_app(None)
    assert len(errors) == 1


def test_reports_period_controls_open_five_times_without_ghost_top_levels():
    qt_app = _application()
    trace = _TopLevelShowTrace()
    qt_app.installEventFilter(trace)
    try:
        for _cycle in range(5):
            dialog = QDialog()
            dialog.setObjectName("ReportsDialogValidation")
            layout = QVBoxLayout(dialog)
            # Administradores crean dos selectores (panel + generación).
            layout.addWidget(app.PeriodSelectorWidget(dialog))
            layout.addWidget(app.PeriodSelectorWidget(dialog))
            dialog.show()
            qt_app.processEvents()
            dialog.close()
            dialog.deleteLater()
            qt_app.processEvents()
        assert trace.shown == ["QDialog"] * 5
    finally:
        qt_app.removeEventFilter(trace)


def test_theme_and_noncritical_services_run_after_hidden_construction():
    controller_source = inspect.getsource(app.AppController.run)
    theme_position = controller_source.index("self._on_theme_toggled")
    show_position = controller_source.index("showMaximized")
    deferred_position = controller_source.index("start_deferred_services")
    assert theme_position < show_position < deferred_position

    constructor_source = inspect.getsource(app.MainWindow.__init__)
    assert "self._start_pdf_services()" not in constructor_source
    assert "self.safe_startup_load()" not in constructor_source

    deferred_source = inspect.getsource(app.MainWindow.start_deferred_services)
    assert "self._start_pdf_services()" in deferred_source
    assert "StartupMaintenanceWorker" in deferred_source
