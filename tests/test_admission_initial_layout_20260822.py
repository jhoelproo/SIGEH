from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QStackedWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ADMISION_PYSIDE6_V15 import qt_compat as compat
from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App, theme_icon
from admission_v15_adapter import AdmissionV15EventBus, AdmissionV15Factory
from CALCULOS_QT import EmergencyWorkspacePage, visual_theme_tokens
from CALCULOS_QT import validate_visual_theme_contrast


def _build_admission_widget(tmp_path, monkeypatch, *, host_theme_is_dark=None):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    application = QApplication.instance() or QApplication([])
    host = SimpleNamespace(
        connection_factory=lambda: None,
        user={
            "id": 1,
            "username": "layout-test",
            "full_name": "Layout Test",
            "role": "administrador",
        },
        session_id="layout-session",
        device_id="layout-device",
        device_name="layout-station",
        current_shift={},
        configuration=(
            {
                "host_theme_is_dark": bool(host_theme_is_dark),
                "host_visual_theme": visual_theme_tokens(bool(host_theme_is_dark)),
            }
            if host_theme_is_dark is not None
            else {}
        ),
        logger=None,
        event_bus=AdmissionV15EventBus(),
    )
    factory = AdmissionV15Factory(
        host,
        session_checker=lambda: True,
        users_provider=list,
        credential_verifier=lambda _username, _password: None,
    )
    shell = QWidget()
    shell_layout = QVBoxLayout(shell)
    shell_layout.setContentsMargins(0, 0, 0, 0)
    stack = QStackedWidget(shell)
    billing = QWidget(stack)
    stack.addWidget(billing)
    admission = factory.create_widget(stack)
    stack.addWidget(admission)
    shell_layout.addWidget(stack)
    return application, shell, stack, billing, admission


def _geometry_snapshot(widget):
    app = widget.admission
    return {
        "host": (widget.width(), widget.height()),
        "root": (widget.root.width(), widget.root.height()),
        "content": (app.content_area.width(), app.content_area.height()),
        "name": app.entry_nombre.width(),
        "cedula": app.entry_cedula.width(),
        "telefono": app.entry_telefono.width(),
        "direccion": app.entry_direccion.width(),
        "nacionalidad": app.entry_nacionalidad.width(),
        "ars": app.entry_ars.width(),
        "nss": app.entry_nss.width(),
    }


def _assert_balanced_pair(left, right):
    tolerance = max(16, int((left + right) * 0.10))
    assert abs(left - right) <= tolerance


def test_first_show_matches_geometry_after_tab_reactivation(tmp_path, monkeypatch):
    application, shell, stack, billing, widget = _build_admission_widget(
        tmp_path, monkeypatch
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(120)
        first = _geometry_snapshot(widget)
        assert widget.sync_embedded_layout() is False
        widget._layout_sync_in_progress = True
        assert widget.sync_embedded_layout(force=True) is False
        widget._layout_sync_in_progress = False

        stack.setCurrentWidget(billing)
        QTest.qWait(30)
        assert widget.sync_embedded_layout(force=True) is False
        stack.setCurrentWidget(widget)
        QTest.qWait(120)
        second = _geometry_snapshot(widget)

        for key in (
            "host",
            "root",
            "name",
            "cedula",
            "telefono",
            "direccion",
            "nacionalidad",
            "ars",
            "nss",
        ):
            assert first[key] == second[key]
        assert first["content"][0] == second["content"][0]
        assert first["root"][0] == first["host"][0]
        assert 0 < first["root"][1] <= first["host"][1]
        assert first["direccion"] > first["name"]
        _assert_balanced_pair(first["cedula"], first["telefono"])
        _assert_balanced_pair(first["nacionalidad"], first["ars"])

        widget.admission.entry_nombre.setText("PACIENTE DE PRUEBA VISUAL")
        widget.admission.entry_cedula.setText("00100000001")
        entry_identities = tuple(
            id(entry) for entry in widget.admission.all_entries
        )

        geometries = {(1600, 900): first}
        for width, height in ((1920, 1080), (1366, 768)):
            shell.resize(width, height)
            QTest.qWait(120)
            geometry = _geometry_snapshot(widget)
            geometries[(width, height)] = geometry
            assert geometry["host"] == (width, height)
            assert geometry["root"][0] == width
            assert geometry["content"][0] <= geometry["root"][0]
            assert geometry["name"] >= 250
            assert geometry["direccion"] > geometry["name"]
            _assert_balanced_pair(geometry["cedula"], geometry["telefono"])
            _assert_balanced_pair(geometry["nacionalidad"], geometry["ars"])
            assert geometry["nss"] == geometry["cedula"]

        shell.resize(1920, 1080)
        QTest.qWait(120)
        maximized_geometry = _geometry_snapshot(widget)
        shell.resize(1366, 768)
        QTest.qWait(120)
        restored_geometry = _geometry_snapshot(widget)

        assert widget.admission.entry_nombre.text() == "PACIENTE DE PRUEBA VISUAL"
        assert widget.admission.entry_cedula.text() == "00100000001"
        assert maximized_geometry["direccion"] > geometries[(1366, 768)]["direccion"]
        for key in (
            "name",
            "cedula",
            "telefono",
            "direccion",
            "nacionalidad",
            "ars",
            "nss",
        ):
            assert restored_geometry[key] == geometries[(1366, 768)][key]
        assert tuple(id(entry) for entry in widget.admission.all_entries) == entry_identities
        assert widget.sizePolicy().horizontalPolicy().name == "Expanding"
        assert widget.root.sizePolicy().horizontalPolicy().name == "Expanding"
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_product_layout_sync_has_no_event_pump_or_sleep_hack():
    source = Path(
        "ADMISION_PYSIDE6_V15/admission_widget.py"
    ).read_text(encoding="utf-8")
    sync_source = source.split("def sync_embedded_layout", 1)[1].split(
        "def showEvent", 1
    )[0]

    assert "processEvents" not in sync_source
    assert "sleep(" not in sync_source
    assert "setFixedWidth" not in sync_source
    assert "setGeometry" not in sync_source


def test_forced_layout_restores_entry_geometry_at_the_same_host_size(
    tmp_path, monkeypatch
):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(120)
        baseline = {
            entry_name: getattr(widget.admission, entry_name).height()
            for entry_name in (
                "entry_nombre",
                "entry_cedula",
                "entry_direccion",
                "entry_nss",
            )
        }
        host_size = (widget.width(), widget.height())

        # This represents the internal Qt style invalidation observed after the
        # external PDF viewer takes focus; the outer host size stays identical.
        widget.admission.entry_nombre.setStyleSheet("QLineEdit { min-height: 1px; }")
        widget.admission.entry_nombre.setMinimumHeight(1)
        widget.request_layout_stabilization("pdf_complete", force=True)
        QTest.qWait(80)

        assert (widget.width(), widget.height()) == host_size
        for entry_name, expected_height in baseline.items():
            actual_height = getattr(widget.admission, entry_name).height()
            assert abs(actual_height - expected_height) <= 2
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_application_reactivation_forces_layout_without_tab_switch(
    tmp_path, monkeypatch
):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(120)
        baseline = widget.admission.entry_nombre.height()
        widget._on_application_state_changed(Qt.ApplicationState.ApplicationActive)
        QTest.qWait(80)
        assert abs(widget.admission.entry_nombre.height() - baseline) <= 2
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_ten_pdf_completion_visual_events_preserve_entry_heights(
    tmp_path, monkeypatch
):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(120)
        baseline = {
            name: getattr(widget.admission, name).height()
            for name in (
                "entry_nombre",
                "entry_cedula",
                "entry_direccion",
                "entry_nss",
            )
        }
        for _ in range(10):
            widget.admission._notify_embedded_visual_event("pdf_complete")
            QTest.qWait(20)
        for name, expected_height in baseline.items():
            assert abs(getattr(widget.admission, name).height() - expected_height) <= 2
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_embedded_host_theme_is_runtime_only_and_idempotent(tmp_path, monkeypatch):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=True
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(120)
        app = widget.admission
        persisted_theme = app.app_settings.get("theme")

        widget.apply_host_theme(False, visual_theme_tokens(False))
        QTest.qWait(40)
        assert app._paleta_visual_actual()["mode"] == "claro"
        assert app.app_settings.get("theme") == persisted_theme
        assert "#EEF2F6" in widget.root._central.styleSheet()

        for index in range(20):
            widget.apply_host_theme(bool(index % 2))
        assert app._paleta_visual_actual()["mode"] == "oscuro"
        assert app.app_settings.get("theme") == persisted_theme
        app.app_settings["high_contrast"] = True
        widget.apply_host_theme(False)
        assert app._paleta_visual_actual()["mode"] == "claro_alto"
        widget.apply_host_theme(True)
        assert app._paleta_visual_actual()["mode"] == "oscuro_alto"

        # Compact mode may reduce spacing, but must never collapse the inputs.
        app.app_settings["compact_mode"] = True
        widget.request_layout_stabilization("compact_mode", force=True)
        QTest.qWait(40)
        compact_heights = (app.entry_nombre.height(), app.entry_cedula.height())
        app._notify_embedded_visual_event("pdf_complete")
        QTest.qWait(40)
        assert app.entry_nombre.height() >= 32
        assert app.entry_cedula.height() >= 32
        assert (app.entry_nombre.height(), app.entry_cedula.height()) == compact_heights

        class NoDataAccess:
            def __getattr__(self, name):
                raise AssertionError(f"Theme application attempted data access: {name}")

        # Theme propagation is purely visual: it must not touch the data layer.
        app.db = NoDataAccess()
        widget.apply_host_theme(False)
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_standalone_theme_and_window_size_remain_independent_preferences():
    standalone = object.__new__(App)
    standalone._standalone = True
    standalone._host_theme_controlled = False
    standalone._host_theme_is_dark = None
    standalone.app_settings = {
        "theme": "claro",
        "high_contrast": False,
        "accent_color": "Azul hospitalario",
    }
    assert standalone._paleta_visual_actual()["mode"] == "claro"
    assert standalone.apply_host_theme(True) is False
    assert standalone.app_settings["theme"] == "claro"

    class Root:
        def __init__(self):
            self.geometry_calls = []

        def geometry(self, value):
            self.geometry_calls.append(value)

        def updateGeometry(self):
            return None

        def layout(self):
            return None

    root = Root()
    embedded = object.__new__(App)
    embedded._standalone = False
    embedded.root = root
    embedded.app_settings = {"window_size": "1600x900"}
    embedded._configurar_estilos_desde_preferencias = lambda: None
    embedded._aplicar_modo_responsivo = lambda: None
    embedded._aplicar_paridad_visual_inicio = lambda: None
    embedded._aplicar_preferencias_en_vivo()
    assert root.geometry_calls == []

    standalone_window = object.__new__(App)
    standalone_window._standalone = True
    standalone_window.root = root
    standalone_window.app_settings = {"window_size": "1600x900"}
    standalone_window._configurar_estilos_desde_preferencias = lambda: None
    standalone_window._aplicar_modo_responsivo = lambda: None
    standalone_window._aplicar_paridad_visual_inicio = lambda: None
    standalone_window._aplicar_preferencias_en_vivo()
    assert root.geometry_calls == ["1600x900"]


def test_emergency_workspace_routes_global_theme_to_embedded_v15():
    class EmbeddedAdmission:
        def __init__(self):
            self.received = []

        def apply_host_theme(self, is_dark):
            self.received.append(bool(is_dark))

    embedded = EmbeddedAdmission()
    page = SimpleNamespace(full_page=embedded)

    EmergencyWorkspacePage.apply_theme(page, True)
    EmergencyWorkspacePage.apply_theme(page, False)

    assert embedded.received == [True, False]


def test_host_theme_updates_open_history_configuration_and_menus(
    tmp_path, monkeypatch
):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=True
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(120)
        app = widget.admission
        app.abrir_historial()
        app._abrir_configuracion_interna()
        QTest.qWait(100)
        assert app.historial_win is not None and app.historial_win.isVisible()
        assert (
            app.configuracion_interna_win is not None
            and app.configuracion_interna_win.isVisible()
        )
        assert app._paleta_visual_actual()["mode"] == "oscuro"
        assert "#0A1420" in app.historial_win.styleSheet()
        assert "#0A1420" in app.configuracion_interna_win.styleSheet()
        history_tables = app.historial_win.findChildren(QTableWidget)
        assert history_tables
        assert "#0F1B2A" in history_tables[0].styleSheet()

        widget.apply_host_theme(False, visual_theme_tokens(False))
        QTest.qWait(40)
        assert "#FFFFFF" in app.actions_menu.styleSheet()
        assert "#FFFFFF" in app.menu_contextual.styleSheet()
        assert "#FFFFFF" in app.combo_unidad.styleSheet()
        assert "#EEF2F6" in app.historial_win.styleSheet()
        assert "#EEF2F6" in app.configuracion_interna_win.styleSheet()
        assert "#FFFFFF" in history_tables[0].styleSheet()
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_semantic_theme_tokens_meet_the_required_text_contrast():
    for is_dark in (False, True):
        contrast = validate_visual_theme_contrast(is_dark)
        assert all(value >= 4.5 for value in contrast.values())


def test_nested_admission_dialogs_are_polished_before_their_first_frame(
    tmp_path, monkeypatch
):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=False
    )
    first = None
    nested = None
    try:
        shell.resize(1366, 768)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(80)

        first = compat.Toplevel(widget.root)
        first_label = compat.Label(
            first,
            text="Diálogo de prueba",
            background="#0E1B2B",
            foreground="#FFFFFF",
        )
        first_label.pack()
        nested = compat.Toplevel(first)
        nested_label = compat.Label(
            nested,
            text="Diálogo anidado",
            background="#0E1B2B",
            foreground="#FFFFFF",
        )
        nested_label.pack()
        QTest.qWait(100)

        assert first.isVisible() and nested.isVisible()
        assert "#EEF2F6" in first.styleSheet()
        assert "#EEF2F6" in nested.styleSheet()
        assert "background-color:transparent" in first_label.styleSheet().replace(" ", "")
        assert "background-color:transparent" in nested_label.styleSheet().replace(" ", "")

        widget.apply_host_theme(True, visual_theme_tokens(True))
        QTest.qWait(40)
        assert "#0A1420" in first.styleSheet()
        assert "#0A1420" in nested.styleSheet()
        assert "#E5EEF8" in nested_label.styleSheet()
    finally:
        for window in (nested, first):
            if window is not None:
                window.close()
                window.deleteLater()
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()


def test_report_window_and_themed_icons_follow_the_host_theme_in_place(
    tmp_path, monkeypatch
):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=False
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        QTest.qWait(80)
        app = widget.admission
        app.abrir_ventana_reporte()
        QTest.qWait(80)
        assert app.reporte_win is not None and app.reporte_win.isVisible()
        assert "#EEF2F6" in app.reporte_win.styleSheet()
        assert "#FFFFFF" in app.combo_unidad.styleSheet()

        light_icon = theme_icon("history.svg", "#25384C", size=24).pixmap(24, 24)
        dark_icon = theme_icon("history.svg", "#F2F6FA", size=24).pixmap(24, 24)
        light_colors = {
            light_icon.toImage().pixelColor(x, y).name().upper()
            for x in range(light_icon.width())
            for y in range(light_icon.height())
            if light_icon.toImage().pixelColor(x, y).alpha() > 0
        }
        dark_colors = {
            dark_icon.toImage().pixelColor(x, y).name().upper()
            for x in range(dark_icon.width())
            for y in range(dark_icon.height())
            if dark_icon.toImage().pixelColor(x, y).alpha() > 0
        }
        assert "#25384C" in light_colors
        assert "#F2F6FA" in dark_colors

        widget.apply_host_theme(True, visual_theme_tokens(True))
        QTest.qWait(40)
        assert "#0A1420" in app.reporte_win.styleSheet()
        assert "#0F1B2A" in app.combo_unidad.styleSheet()
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()
