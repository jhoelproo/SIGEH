"""The real application exposes the manual update check at laptop resolutions."""

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QMessageBox

import update_controls
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


@pytest.mark.parametrize("width,height", [(1280, 680), (1920, 1000)])
def test_update_button_in_actual_window(main_window, monkeypatch, width, height):
    window = main_window
    window.resize(width, height)
    QApplication.processEvents()
    button = window.update_control.button
    assert button.isVisible() and button.isEnabled()
    assert window.rect().contains(QRect(button.mapTo(window, QPoint()), button.size()))
    messages = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: messages.append(args))
    window.update_control._checked("1.0.0", False)
    assert "actualizado" in messages[0][2]
    assert isinstance(window.update_control, update_controls.UpdateCheckControl)
    destination = Path("output") / f"oct23-window-{width}.png"
    assert window.grab().save(str(destination))
