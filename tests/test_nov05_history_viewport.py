"""The real Admisión history, including its header and administrator actions."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QAbstractButton, QDateEdit, QMenu, QTableWidget
import CALCULOS_QT as app

from ADMISION_PYSIDE6_V15.qt_compat import Toplevel
from tests.test_admission_initial_layout_20260822 import _build_admission_widget


@pytest.mark.parametrize("dark", [False, True])
@pytest.mark.parametrize("width", [1024, 1280, 1920])
def test_administrator_history_fits_the_monitor_and_shows_both_dates(
    tmp_path, monkeypatch, width, dark
):
    height = 680 if width < 1920 else 1000
    available = QRect(0, 0, width, height)
    monkeypatch.setattr(
        Toplevel,
        "screen",
        lambda _self: SimpleNamespace(availableGeometry=lambda: available),
    )
    qt, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=dark
    )
    admission = widget.admission
    original_style = qt.styleSheet()
    original_font = qt.font()
    for filename in (
        "segoeui.ttf",
        "segoeuib.ttf",
        "arial.ttf",
        "arialbd.ttf",
        "seguisym.ttf",
        "seguiemj.ttf",
    ):
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/" + filename)
    qt.setFont(QFont("Segoe UI", 10))
    qt.setStyleSheet(app.get_stylesheet(dark) + app.modern_module_stylesheet(dark))
    admission.db = SimpleNamespace(
        uses_central_history=True,
        obtener_turnos_historial=lambda: {},
        listar_atenciones_filtradas=Mock(return_value=[]),
        list_history_cache_local=Mock(return_value=[]),
    )
    admission._obtener_ars_cache = lambda **_kwargs: []
    admission._ejecutar_en_segundo_plano = lambda _label, operation, **handlers: (
        handlers["al_terminar"](operation())
    )
    shell.resize(width, height)
    stack.setCurrentWidget(widget)
    shell.show()
    try:
        admission.abrir_historial()
        window = admission.historial_win
        QTest.qWait(100)
        action = next(
            action
            for menu in window.findChildren(QMenu)
            for action in menu.actions()
            if action.text() == "Entre fechas"
        )
        action.trigger()
        QTest.qWait(80)
        start, end = window.findChildren(QDateEdit)
        assert start.isVisible() and end.isVisible()
        assert window.width() <= width and window.height() <= height
        table = window.findChild(QTableWidget)
        assert table.height() >= 180
        for button in window.findChildren(QAbstractButton):
            if button.isVisible():
                top_left = window.mapFromGlobal(button.mapToGlobal(QPoint(0, 0)))
                assert window.rect().contains(top_left), button.text()
                assert window.rect().contains(
                    top_left + QPoint(button.width() - 1, button.height() - 1)
                ), button.text()
                if not table.isAncestorOf(button):
                    button_rect = QRect(
                        table.parentWidget().mapFromGlobal(
                            button.mapToGlobal(QPoint(0, 0))
                        ),
                        button.size(),
                    )
                    assert not table.geometry().intersects(button_rect), button.text()
        assert window.grab().save(
            str(
                Path("output")
                / f"nov05-history-{width}-{'dark' if dark else 'light'}.png"
            )
        )
        window.destroy()
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        qt.processEvents()
        qt.setStyleSheet(original_style)
        qt.setFont(original_font)
