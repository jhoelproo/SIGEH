"""Regression coverage for the shared Facturación/Admisión visual contract."""

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

import CALCULOS_QT as shell
from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App
from ADMISION_PYSIDE6_V15 import qt_compat
from PySide6.QtWidgets import QApplication, QPushButton


def test_host_tokens_cover_all_admission_visual_roles():
    required = {
        "root", "card", "card2", "entry", "tree", "heading", "text",
        "muted", "accent", "border", "separator", "selected_bg",
        "selected_fg", "button_fg", "success", "warning", "danger", "info",
    }
    for is_dark in (False, True):
        tokens = shell.visual_theme_tokens(is_dark)
        assert required <= tokens.keys()
        assert tokens["root"] == tokens["bg"]
        assert tokens["entry"] == tokens["input_bg"]
        stylesheet = shell.get_stylesheet(is_dark)
        assert tokens["root"] in stylesheet
        assert tokens["entry"] in stylesheet
        assert tokens["text"] in stylesheet


def test_embedded_theme_refresh_uses_the_host_token_set_without_persistence():
    admission = object.__new__(App)
    admission._host_theme_controlled = True
    admission._host_theme_is_dark = True
    admission._host_visual_theme = shell.visual_theme_tokens(True)
    admission.shared_configuration = {}
    admission.app_settings = {"theme": "oscuro"}
    calls = []
    admission._aplicar_preferencias_en_vivo = lambda **kwargs: calls.append(kwargs)
    admission._refresh_embedded_theme_indicators = lambda: calls.append("indicator")

    light_tokens = shell.visual_theme_tokens(False)
    assert admission.apply_host_theme(False, theme_tokens=light_tokens) is True
    assert admission._host_visual_theme == light_tokens
    assert admission.shared_configuration["host_visual_theme"] == light_tokens
    assert admission.shared_configuration["host_theme_is_dark"] is False
    assert calls == [{"forzar_todo": True}, "indicator"]
    assert admission.app_settings["theme"] == "oscuro"


def test_emergency_workspace_passes_tokens_and_preserves_old_widget_contract():
    received = []

    class ModernEmbeddedAdmission:
        def apply_host_theme(self, is_dark, theme_tokens):
            received.append((is_dark, theme_tokens))

    page = SimpleNamespace(full_page=ModernEmbeddedAdmission())
    shell.EmergencyWorkspacePage.apply_theme(page, True)
    assert received == [(True, shell.visual_theme_tokens(True))]

    legacy_received = []

    class LegacyEmbeddedAdmission:
        def apply_host_theme(self, is_dark):
            legacy_received.append(is_dark)

    legacy_page = SimpleNamespace(full_page=LegacyEmbeddedAdmission())
    shell.EmergencyWorkspacePage.apply_theme(legacy_page, False)
    assert legacy_received == [False]


def test_dark_button_roles_do_not_keep_light_disabled_surfaces():
    application = QApplication.instance() or QApplication([])
    button = QPushButton("Acción")
    shell.set_button_role(button, "neutral", is_dark=True)
    stylesheet = button.styleSheet()
    assert "#283847" in stylesheet
    assert "#8EA1B2" in stylesheet
    button.deleteLater()
    application.processEvents()


def test_compat_modals_follow_the_current_visual_contract():
    light = shell.visual_theme_tokens(False)
    dark = shell.visual_theme_tokens(True)

    qt_compat.set_compat_theme_tokens(light)
    light_qss = qt_compat._message_box_qss()
    assert light["window_bg"] in light_qss
    assert light["text_primary"] in light_qss
    assert light["button_primary_bg"] in light_qss

    qt_compat.set_compat_theme_tokens(dark)
    dark_qss = qt_compat._message_box_qss()
    assert dark["window_bg"] in dark_qss
    assert dark["text_primary"] in dark_qss
    assert dark["button_primary_bg"] in dark_qss
    assert light["window_bg"] not in dark_qss


def test_admission_action_icons_are_not_fixed_white_assets():
    source = Path(
        "ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py"
    ).read_text(encoding="utf-8")
    assert "QIcon(resource_path" not in source
    for icon_name in (
        "turno.svg",
        "menu.svg",
        "history.svg",
        "clear.svg",
        "pdf.svg",
        "report.svg",
        "excel.svg",
        "uninsured.svg",
        "edit.svg",
        "config.svg",
    ):
        assert f'"{icon_name}"' in source
    assert "theme_icon(icon_map.get" in source
