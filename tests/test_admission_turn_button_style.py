"""The handoff action stays visible through theme and layout refreshes."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtTest import QTest

from CALCULOS_QT import visual_theme_tokens
from tests.test_admission_initial_layout_20260822 import _build_admission_widget


@pytest.mark.parametrize("is_dark", [False, True])
def test_turn_button_uses_primary_blue_after_theme_and_layout_refresh(
    tmp_path, monkeypatch, is_dark
):
    application, window, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=is_dark
    )
    try:
        window.resize(1366, 768)
        stack.setCurrentWidget(widget)
        window.show()
        QTest.qWait(60)
        button = widget.admission.boton_cambiar_turno
        for dark_mode in (is_dark, not is_dark, is_dark):
            palette = visual_theme_tokens(dark_mode)
            widget.apply_host_theme(dark_mode, palette)
            widget.sync_embedded_layout(force=True, reason="turn_button_style_test")
            QTest.qWait(10)

            stylesheet = button.styleSheet()
            assert f"background-color: {palette['button_primary_bg']}" in stylesheet
            assert f"color: {palette['button_primary_text']}" in stylesheet
            assert f"background-color: {palette['button_primary_hover']}" in stylesheet
            assert "QPushButton:pressed" in stylesheet
            assert "QPushButton:disabled" in stylesheet
            assert palette["input_disabled_bg"] in stylesheet
            assert button.property("admissionVisualRole") == "primary"
            assert button.property("admissionIconSource") == "turno.svg"
            assert not button.icon().isNull()
            assert button.text() == "Cambiar Turno"

            button.setEnabled(False)
            assert not button.isEnabled()
            button.setEnabled(True)
            assert button.isEnabled()
            assert button.styleSheet() == stylesheet
    finally:
        widget.shutdown()
        window.close()
        window.deleteLater()
        application.processEvents()
