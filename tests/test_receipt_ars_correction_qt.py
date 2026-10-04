import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
)

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


def form():
    combo = QComboBox()
    combo.addItems(["APS", "NUEVA"])
    combo.setCurrentText("NUEVA")
    table = QTableWidget(1, 5)
    for column, text in (
        (0, "Procedimientos"),
        (1, "PRUEBA"),
        (3, "$100.00"),
        (4, "$200.00"),
    ):
        table.setItem(0, column, QTableWidgetItem(text))
    room = QDoubleSpinBox()
    room.setMaximum(10000)
    room.setValue(100)
    return SimpleNamespace(
        current_ars="NUEVA",
        locked_ars="APS",
        ars_combo=combo,
        _pending_ars_correction={"previous": "APS", "target": "NUEVA"},
        cart_table=table,
        _cart_quantity_value=lambda index: 2,
        cart_has_ars_items=lambda: True,
        universal={},
        sala_spin=room,
        service_type="EMERGENCIA",
        ars_cache={},
        refresh_picker=Mock(),
        update_totals=Mock(),
        _update_document_flow_ui=Mock(),
    )


def runtime(**changes):
    result = {
        "ars_id": 1,
        "sala_emergencia": 460,
        "catalogs": {"Procedimientos": {"PRUEBA": 125}},
    }
    result.update(changes)
    return result


def test_tariff_callback_recalculates_visible_cart_and_room(qt_application):
    window = form()
    app.MainWindow._apply_ars_runtime_data(window, "NUEVA", runtime())
    assert window.cart_table.item(0, 3).text() == "$125.00"
    assert window.cart_table.item(0, 4).text() == "$250.00"
    assert window.sala_spin.value() == 460 and window.locked_ars == "NUEVA"
    assert window._pending_ars_correction is None
    window.update_totals.assert_called_once()


@pytest.mark.parametrize("data", [runtime(ars_id=0), runtime(catalogs={})])
def test_missing_tariff_reverts_without_partial_changes(
    qt_application, monkeypatch, data
):
    monkeypatch.setattr(app.QMessageBox, "warning", Mock())
    window = form()
    app.MainWindow._apply_ars_runtime_data(window, "NUEVA", data)
    assert window.current_ars == window.ars_combo.currentText() == "APS"
    assert window.cart_table.item(0, 3).text() == "$100.00"
    assert window.sala_spin.value() == 100 and window.locked_ars == "APS"
    window.update_totals.assert_not_called()


def test_stale_callback_does_not_apply(qt_application):
    window = form()
    app.MainWindow._apply_ars_runtime_data(window, "APS", runtime())
    assert window.cart_table.item(0, 3).text() == "$100.00"


def test_network_failure_reverts_and_disallows_save_until_loaded(
    qt_application, monkeypatch
):
    monkeypatch.setattr(app.QMessageBox, "warning", Mock())
    monkeypatch.setattr(app.QMessageBox, "information", Mock())
    window = form()
    window._start_pending_ars_runtime_load = Mock()
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    app.MainWindow.generate_pdf(window)
    app.QMessageBox.information.assert_called_once()
    app.MainWindow._on_ars_runtime_failed(window, "NUEVA", "OSError", 1)
    assert window.current_ars == "APS" and window._pending_ars_correction is None


def test_admin_selection_starts_recalculation_instead_of_cart_lock(qt_application):
    window = form()
    window.current_ars = "APS"
    window._pending_ars_correction = None
    window.editing_recibo_id = 1
    window.current_user = {"role": app.ROLE_ADMIN}
    window.receipt_read_only = False
    window.coverage_combo = QComboBox()
    window.coverage_combo.addItem("Asegurado")
    window.mark_activity = Mock()
    window.set_current_ars_from_cache = Mock()
    app.MainWindow.on_ars_changed(window, "NUEVA")
    assert window._pending_ars_correction == {"previous": "APS", "target": "NUEVA"}
    window.set_current_ars_from_cache.assert_called_once_with("NUEVA")


def test_duplicate_selection_waits_for_current_repricing(qt_application):
    window = form()
    window.mark_activity = Mock()
    window.set_current_ars_from_cache = Mock()
    app.MainWindow.on_ars_changed(window, "APS")
    assert window.ars_combo.currentText() == "NUEVA"
    window.set_current_ars_from_cache.assert_not_called()
