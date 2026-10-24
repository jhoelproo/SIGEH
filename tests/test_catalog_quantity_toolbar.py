"""Quantity belongs beside catalog search and keeps existing billing actions."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QPushButton, QToolButton

import CALCULOS_QT as app
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


@pytest.mark.parametrize("width,height", [(1280, 680), (1366, 728), (1920, 1000)])
def test_quantity_replaces_sort_beside_search_in_actual_window(
    main_window, width, height
):
    window = main_window
    window.resize(width, height)
    QTest.qWait(150)
    controls = window.qty.parentWidget()
    search = QRect(window.search.mapTo(window, QPoint()), window.search.size())
    quantity = QRect(controls.mapTo(window, QPoint()), controls.size())
    assert abs(search.center().y() - quantity.center().y()) <= 5
    assert search.right() < quantity.left()
    assert window.rect().contains(quantity)
    assert window.qty.isVisible() and window.qty.isEnabled()
    assert not hasattr(window.catalog_workspace, "sort")
    assert window.catalog_workspace.quantity is controls
    labels = [
        label
        for label in window.catalog_panel.findChildren(QLabel)
        if label.text() == "Cant:"
    ]
    assert len(labels) == 1 and labels[0].parentWidget() is controls
    assert window.grab().save(str(Path("output") / f"catalog-quantity-{width}.png"))


def test_moved_quantity_keeps_arrows_limits_and_direct_editing(main_window):
    window = main_window
    arrows = {
        button.text(): button
        for button in window.qty.parentWidget().findChildren(
            QToolButton, "SpinArrowBtn"
        )
    }
    window.qty.setValue(1)
    arrows["▼"].click()
    assert window.qty.value() == 1
    arrows["▲"].click()
    assert window.qty.value() == 2
    window.qty.setFocus()
    window.qty.selectAll()
    QTest.keyClicks(window.qty, "25")
    QTest.keyClick(window.qty, Qt.Key_Tab)
    assert window.qty.value() == 25
    window.qty.setValue(300)
    arrows["▲"].click()
    assert window.qty.value() == 300
    arrows["▼"].click()
    assert window.qty.value() == 299


def test_row_add_uses_top_quantity_and_preserves_material_scaling(
    main_window, monkeypatch
):
    window = main_window
    window.universal["Medicamentos"] = {"MED QA": 10}
    window.universal["Materiales"] = {"JERINGA QA": 2}
    window.material_cart.rules = {
        "MED QA": [{"name": "JERINGA QA", "units": 2, "active": True}]
    }
    monkeypatch.setattr(window, "maybe_prompt_honorario", Mock())
    window.tabs.setCurrentIndex(app.ALL_CATEGORIES.index("Medicamentos"))
    window.refresh_picker()
    window.qty.setValue(3)
    listing = window.source_lists["Medicamentos"]
    listing.itemWidget(listing.item(0)).findChild(QPushButton, "DesignAdd").click()
    assert window.material_cart._quantity("Medicamentos", "MED QA") == 3
    assert window.material_cart._quantity("Materiales", "JERINGA QA") == 6
    assert window.qty.value() == 1
