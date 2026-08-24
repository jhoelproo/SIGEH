import os
import sys
import inspect
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QPushButton

import CALCULOS_QT as app
from app_icons import APP_ICONS, AppIcons
from report_engine.html_renderer import bar_chart_svg


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_shift_closure_title_uses_closed_turn_representative():
    assert app.shift_closure_report_type(
        {"representative": "  MARÍA   PÉREZ ", "closed_by": "OTRO"}
    ) == "Cierre automático del turno de MARÍA PÉREZ"


def test_shift_closure_open_and_print_uses_v15_sumatra_path(tmp_path):
    pdf_path = tmp_path / "cierre.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF")

    class EmergencyWorkspace:
        def __init__(self):
            self.calls = []

        def print_pdf_with_v15(self, path, copies=1):
            self.calls.append((path, copies))
            return True

    class Window:
        emergency_workspace = EmergencyWorkspace()

    closure = {"source_instance_id": "V15", "turn_id": 7}
    with (
        patch.object(app, "open_file_path", return_value=True) as opened,
        patch.object(app, "claim_shift_print_once", return_value=True),
        patch.object(app, "finish_shift_print") as finished,
        patch.object(app, "write_runtime_log"),
    ):
        app.MainWindow._open_and_print_shift_report(Window(), closure, str(pdf_path))

    opened.assert_called_once_with(str(pdf_path))
    assert Window.emergency_workspace.calls == [(str(pdf_path), 1)]
    finished.assert_called_once_with(closure)


def test_billing_specific_icons_are_non_null_and_semantic():
    _qt_app()
    for text, key in (
        ("Recibos Guardados", "billing_receipts"),
        ("Reportes", "billing_reports"),
        ("Gestión ARS", "billing_ars"),
        ("Importar Word", "billing_word_import"),
        ("VERIFICAR PACIENTE PARA FACTURAR", "verify"),
    ):
        button = QPushButton(text)
        button.setProperty("semanticIcon", key)
        assert APP_ICONS.decorate_button(button) == key
        assert AppIcons.semantic_key(button) == key
        assert not button.icon().isNull()


def test_theme_moon_sun_and_settings_gear_are_available_in_both_states():
    _qt_app()
    button = QPushButton()
    for key in (
        "billing_theme_to_dark",
        "billing_theme_to_light",
        "billing_settings",
    ):
        assert not AppIcons.icon(key, button, 20).isNull()


def test_reports_center_keeps_close_but_removes_logout_action():
    source = inspect.getsource(app.ReportsDialog.__init__)
    assert "self.btn_logout = None" in source
    assert 'close_top = QPushButton("Cerrar")' in source
    assert "add_secondary_logout_button" not in source


def test_authorizations_by_ars_uses_large_wrapped_labels():
    svg = bar_chart_svg(
        [
            {"label": "ASEGURADORA MÉDICA MUY EXTENSA", "authorized": 4},
            {"label": "FUTURO", "authorized": 2},
        ],
        "authorized",
        "Autorizaciones por ARS",
    )
    assert "chart-label-ars" in svg
    assert "ASEGURADORA" in svg
    assert "MÉDICA MUY" in svg
    assert "EXTENSA" not in svg  # dos líneas controladas, sin invadir el pie


def test_v15_packaged_resource_path_prefers_packaged_namespace(tmp_path):
    source_parent = Path(
        r"C:\Users\ampar\OneDrive\Desktop\ADMISION_PYSIDE6_V15"
    )
    sys.path.insert(0, str(source_parent))
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as v15

    icon_path = tmp_path / "ADMISION_PYSIDE6_V15" / "assets" / "history.svg"
    icon_path.parent.mkdir(parents=True)
    icon_path.write_text("<svg/>", encoding="utf-8")
    with patch.object(v15.sys, "_MEIPASS", str(tmp_path), create=True):
        assert Path(v15.resource_path("assets/history.svg")) == icon_path


def test_all_original_v15_button_icons_exist_and_load():
    _qt_app()
    from admission_v15_adapter import DEFAULT_V15_ROOT
    assets = DEFAULT_V15_ROOT / "assets"
    for name in (
        "history.svg",
        "report.svg",
        "excel.svg",
        "uninsured.svg",
        "edit.svg",
        "config.svg",
        "turno.svg",
        "menu.svg",
        "clear.svg",
        "pdf.svg",
    ):
        path = assets / name
        assert path.is_file(), name
        assert not QIcon(str(path)).isNull(), name
