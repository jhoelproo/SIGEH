"""Exercise the actual cart widgets without a production connection."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDoubleSpinBox,
    QLabel,
    QSpinBox,
    QTableWidget,
)

import CALCULOS_QT as app


@pytest.fixture
def cart():
    qt_app = QApplication.instance() or QApplication([])
    table = QTableWidget(0, 6)
    state = SimpleNamespace(
        cart_table=table,
        sala_spin=QDoubleSpinBox(),
        lbl_total=QLabel(),
        lbl_sub_medicamentos=QLabel(),
        lbl_sub_materiales=QLabel(),
        _install_cart_delete_action=Mock(),
    )

    def install(row, quantity):
        editor = QSpinBox(table)
        editor.setRange(1, 999)
        editor.setValue(quantity)
        table.setCellWidget(row, 2, editor)

    state._install_cart_quantity_editor = install
    state._cart_quantity_editor = lambda row: table.cellWidget(row, 2)
    state._cart_quantity_value = lambda row: table.cellWidget(row, 2).value()
    state._cart_quantity_changed = lambda *args, **kwargs: (
        app.MainWindow._cart_quantity_changed(state, *args, **kwargs)
    )
    state.update_totals = lambda: app.MainWindow.update_totals(state)
    yield state
    table.close()
    assert qt_app is not None


@pytest.mark.parametrize("price", [6.00, 5.99, 0.10])
def test_price_and_subtotal_survive_insert_increment_and_quantity_edit(cart, price):
    insert = app.MainWindow.insert_or_increment_cart_item
    assert insert(cart, "Medicamentos", "Synthetic item", price, 1) == "added"
    assert cart.cart_table.item(0, 3).data(Qt.UserRole) == price
    assert insert(cart, "Medicamentos", "Synthetic item", price, 2) == "incremented"
    assert cart.cart_table.item(0, 4).text() == f"${price * 3:,.2f}"
    cart._cart_quantity_changed(cart._cart_quantity_editor(0), 2)
    assert cart.lbl_total.text() == f"Total: RD$ {price * 2:,.2f}"


def test_mixed_categories_and_room_total_keep_cents(cart):
    for category, price in [
        ("Medicamentos", 6),
        ("Materiales", 5.99),
        ("Laboratorios", 0.10),
    ]:
        app.MainWindow.insert_or_increment_cart_item(cart, category, category, price, 3)
    cart.sala_spin.setValue(6)
    cart.update_totals()
    assert cart.lbl_sub_medicamentos.text() == "Medicamentos: RD$ 18.00"
    assert cart.lbl_sub_materiales.text() == "Materiales: RD$ 17.97"
    assert cart.lbl_total.text() == "Total: RD$ 42.27"


def test_empty_cart_displays_room_only(cart):
    cart.sala_spin.setValue(5.99)
    cart.update_totals()
    assert cart.lbl_total.text() == "Total: RD$ 5.99"
    assert cart.lbl_sub_medicamentos.text() == "Medicamentos: RD$ 0.00"
