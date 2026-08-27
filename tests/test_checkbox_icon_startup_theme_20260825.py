"""Offscreen regressions for Admisión's first-frame visual contract."""

from __future__ import annotations

import os
from types import SimpleNamespace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

import CALCULOS_QT as shell
from ADMISION_PYSIDE6_V15 import qt_compat
from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import theme_icon
from admission_v15_adapter import AdmissionV15EventBus, AdmissionV15Factory


def _application():
    return QApplication.instance() or QApplication([])


def _choice_style(is_dark: bool):
    tokens = shell.visual_theme_tokens(is_dark)
    style = qt_compat.Style()
    values = {
        "font": ("Segoe UI", 11),
        "foreground": tokens["text_primary"],
        "background": "transparent",
        "indicator_bg": tokens["checkbox_indicator_bg"],
        "indicator_border": tokens["checkbox_indicator_border"],
        "indicator_checked": tokens["checkbox_checked_bg"],
        "focus_border": tokens["border_focus"],
        "disabledforeground": tokens["text_disabled"],
        "disabled_background": tokens["input_disabled_bg"],
    }
    style.configure("TCheckbutton", **values)
    style.configure("TRadiobutton", **values)
    return tokens


def _image_has_colour(image, colour: str) -> bool:
    wanted = QColor(colour)
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() and pixel.rgb() == wanted.rgb():
                return True
    return False


def _choice_host(is_dark: bool):
    application = _application()
    tokens = _choice_style(is_dark)
    host = QWidget()
    host.setStyleSheet(f"background-color: {tokens['panel_bg']};")
    layout = QVBoxLayout(host)
    layout.setContentsMargins(20, 20, 20, 20)
    check = qt_compat.Checkbutton(host, text="Embarazada")
    radio = qt_compat.Radiobutton(host, text="Femenino")
    plain_check = QCheckBox(
        "Guardar estos datos institucionales como predeterminados para esta ARS",
        host,
    )
    layout.addWidget(check)
    layout.addWidget(radio)
    layout.addWidget(plain_check)
    check.show()
    radio.show()
    host.resize(560, 150)
    host.show()
    QTest.qWait(20)
    return application, host, check, radio, plain_check, tokens


def _assert_no_full_control_focus_frame(control, focus_colour: str):
    """Keep keyboard focus on the indicator, never around the entire label."""
    image = control.grab().toImage()
    wanted = QColor(focus_colour).rgb()
    right_of_indicator = min(24, max(0, image.width() - 1))
    for x in range(right_of_indicator, image.width()):
        assert image.pixelColor(x, 0).rgb() != wanted
        assert image.pixelColor(x, image.height() - 1).rgb() != wanted


def _indicator_contains_colour(control, colour: str) -> bool:
    image = control.grab().toImage()
    wanted = QColor(colour).rgb()
    limit_x = min(22, image.width())
    return any(
        image.pixelColor(x, y).rgb() == wanted
        for y in range(image.height())
        for x in range(limit_x)
    )


def _build_admission_widget(tmp_path, monkeypatch, *, is_dark: bool):
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    application = _application()
    tokens = shell.visual_theme_tokens(is_dark)
    host = SimpleNamespace(
        connection_factory=lambda: None,
        user={
            "id": 1,
            "username": "visual-test",
            "full_name": "Visual Test",
            "role": "administrador",
        },
        session_id="visual-session",
        device_id="visual-device",
        device_name="visual-station",
        current_shift={},
        configuration={
            "host_theme_is_dark": is_dark,
            "host_visual_theme": tokens,
        },
        logger=None,
        event_bus=AdmissionV15EventBus(),
    )
    factory = AdmissionV15Factory(
        host,
        session_checker=lambda: True,
        users_provider=list,
        credential_verifier=lambda _username, _password: None,
    )
    shell_widget = QWidget()
    shell_layout = QVBoxLayout(shell_widget)
    stack = QStackedWidget(shell_widget)
    stack.addWidget(QWidget(stack))
    admission = factory.create_widget(stack)
    stack.addWidget(admission)
    shell_layout.addWidget(stack)
    return application, shell_widget, stack, admission


def _button_visual_snapshot(button):
    size = button.iconSize()
    pixmap = button.icon().pixmap(size)
    return {
        "fingerprint": str(button.property("admissionIconFingerprint") or ""),
        "role": str(button.property("admissionVisualRole") or ""),
        "source": str(button.property("admissionIconSource") or ""),
        "is_null": button.icon().isNull(),
        "has_visible_pixels": any(
            pixmap.toImage().pixelColor(x, y).alpha() > 0
            for y in range(pixmap.height())
            for x in range(pixmap.width())
        ),
    }


def test_checkbox_and_radio_use_only_indicator_borders_in_light_and_dark():
    for is_dark in (False, True):
        application, host, check, radio, plain_check, tokens = _choice_host(is_dark)
        try:
            global_qss = shell.get_stylesheet(is_dark)
            assert "border: none; outline: none; padding: 0; margin: 0;" in global_qss
            assert (
                "QCheckBox::indicator:focus, QRadioButton::indicator:focus"
                in global_qss
            )
            assert "QCheckBox:focus::indicator" not in global_qss
            for control, selector in ((check, "QCheckBox"), (radio, "QRadioButton")):
                qss = control.styleSheet()
                assert f"{selector}{{background:transparent" in qss
                assert "border:none;outline:none;padding:0px;margin:0px;" in qss
                assert f"{selector}::indicator{{" in qss
                assert f"{selector}::indicator:focus" in qss
                assert f"{selector}:focus::indicator" not in qss
                assert "box-shadow" not in qss
                # Both unused outer corners stay equal to the host panel;
                # only the indicator is allowed to differ.
                image = host.grab().toImage()
                assert (
                    image.pixelColor(
                        control.geometry().right(), control.geometry().top()
                    ).rgb()
                    == QColor(tokens["panel_bg"]).rgb()
                )
                control.setChecked(True)
                assert "indicator:checked" in control.styleSheet()
                control.setFocus(Qt.TabFocusReason)
                application.processEvents()
                _assert_no_full_control_focus_frame(control, tokens["border_focus"])
                # Offscreen backends do not always grant window focus, but
                # controls remain keyboard-focusable and style that focus on
                # their indicator rather than on a permanent outer frame.
                assert control.focusPolicy() != Qt.NoFocus
                control.setEnabled(False)
                assert "indicator:disabled" in control.styleSheet()

            plain_check.setFocus(Qt.TabFocusReason)
            application.processEvents()
            _assert_no_full_control_focus_frame(plain_check, tokens["border_focus"])
        finally:
            host.close()
            host.deleteLater()
            application.processEvents()


def test_admission_sex_radio_uses_checkbox_checkmark_without_changing_radio_logic():
    application, host, _check, radio, _plain_check, _tokens = _choice_host(True)
    try:
        radio.configure(radio_checkmark=True)
        radio.setChecked(True)
        application.processEvents()
        qss = radio.styleSheet()
        assert "QRadioButton::indicator{width:15px;height:15px;border-radius:3px" in qss
        assert "QRadioButton::indicator:checked" in qss
        assert "image:url(" in qss
        assert _indicator_contains_colour(radio, "#FFFFFF")
        assert radio.isChecked() is True
    finally:
        host.close()
        host.deleteLater()
        application.processEvents()


def test_embedded_admission_applies_checkmarks_to_both_sex_options(
    tmp_path, monkeypatch
):
    application, host, _stack, widget = _build_admission_widget(
        tmp_path, monkeypatch, is_dark=True
    )
    try:
        app = widget.admission
        app.lbl_sexo_f.setChecked(True)
        application.processEvents()
        for option in (app.lbl_sexo_m, app.lbl_sexo_f):
            qss = option.styleSheet()
            assert "QRadioButton::indicator{width:15px;height:15px;border-radius:3px" in qss
            assert "QRadioButton::indicator:checked" in qss
            assert "image:url(" in qss
        assert app.lbl_sexo_f.isChecked() is True
        assert app.lbl_sexo_m.isChecked() is False
    finally:
        widget.shutdown()
        host.close()
        host.deleteLater()
        application.processEvents()


def test_checked_choice_controls_keep_stable_width_and_text_guard():
    application, host, check, radio, _plain_check, _tokens = _choice_host(False)
    try:
        radio.configure(radio_checkmark=True)
        for control in (check, radio):
            control.setChecked(False)
            application.processEvents()
            unchecked_width = control.sizeHint().width()
            minimum_width = control.minimumSizeHint().width()
            control.setChecked(True)
            application.processEvents()
            checked_width = control.sizeHint().width()
            font_width = control.fontMetrics().horizontalAdvance(control.text())
            assert checked_width == unchecked_width
            assert minimum_width >= checked_width
            assert checked_width >= font_width + 15 + 7 + 4
    finally:
        host.close()
        host.deleteLater()
        application.processEvents()


def test_svg_icons_are_theme_role_and_dpi_aware_with_visible_pixels():
    _application()
    for is_dark in (False, True):
        tokens = shell.visual_theme_tokens(is_dark)
        foreground = tokens["button_danger_text"]
        for ratio in (1.0, 1.25, 1.5):
            icon = theme_icon(
                "clear.svg",
                foreground,
                size=18,
                theme_mode=tokens["mode"],
                role="danger",
                device_pixel_ratio=ratio,
            )
            pixmap = icon.pixmap(18, 18)
            assert not icon.isNull()
            assert not pixmap.isNull()
            assert _image_has_colour(pixmap.toImage(), foreground)
        assert shell.theme_contrast_ratio(
            foreground, tokens["button_danger_bg"]
        ) >= 4.5


def test_cold_start_light_icons_match_toggled_light_without_a_post_show_repair(
    tmp_path, monkeypatch
):
    application, host, stack, widget = _build_admission_widget(
        tmp_path, monkeypatch, is_dark=False
    )
    try:
        app = widget.admission
        buttons = (app.boton_limpiar, app.boton_generar_pdf)
        cold = tuple(_button_visual_snapshot(button) for button in buttons)
        assert all(not snapshot["is_null"] for snapshot in cold)
        assert all(snapshot["has_visible_pixels"] for snapshot in cold)
        assert [snapshot["role"] for snapshot in cold] == ["danger", "primary"]
        assert [snapshot["source"] for snapshot in cold] == ["clear.svg", "pdf.svg"]
        assert all(snapshot["fingerprint"].startswith("claro:") for snapshot in cold)

        host.resize(1600, 900)
        stack.setCurrentWidget(widget)
        host.show()
        QTest.qWait(40)
        for index in range(20):
            widget.apply_host_theme(bool(index % 2), shell.visual_theme_tokens(bool(index % 2)))
        widget.apply_host_theme(False, shell.visual_theme_tokens(False))
        toggled = tuple(_button_visual_snapshot(button) for button in buttons)
        assert toggled == cold
    finally:
        widget.shutdown()
        host.close()
        host.deleteLater()
        application.processEvents()


def test_user_theme_is_resolved_before_main_window_child_construction():
    assert shell.resolve_startup_theme_is_dark(
        {"preferences": {"theme": "claro"}}, fallback=True
    ) is False
    assert shell.resolve_startup_theme_is_dark(
        {"preferences": {"theme": "oscuro"}}, fallback=False
    ) is True
    assert shell.resolve_startup_theme_is_dark({}, fallback=True) is True


def test_initial_theme_path_has_no_post_show_timer_or_double_toggle_hack():
    widget_source = Path(
        "ADMISION_PYSIDE6_V15/admission_widget.py"
    ).read_text(encoding="utf-8")
    shell_source = Path("CALCULOS_QT.py").read_text(encoding="utf-8")
    method = widget_source.split("def _apply_initial_host_theme", 1)[1].split(
        "def remember_focus", 1
    )[0]
    assert "QTimer" not in method
    assert "toggle_theme" not in method
    startup_block = shell_source.split("effective_dark = resolve_startup_theme_is_dark", 1)[1].split(
        "self.main_window = MainWindow", 1
    )[0]
    assert startup_block.count("_on_theme_toggled") == 1
