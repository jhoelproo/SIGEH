"""Real Qt cart cells with production insertion and quantity callbacks."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QLabel, QDoubleSpinBox

import CALCULOS_QT as app
from billing_material_cart import MedicationMaterialCart


@pytest.fixture
def cart():
    owner = SimpleNamespace(
        cart_table=app.ReceiptItemsTable(0, 6),
        is_dark_mode=True,
        _responsive_mode=app.PROFILE_COMPACT,
        universal={"Materiales": {"GUANTES": 2, "JERINGA": 3}},
        sala_spin=QDoubleSpinBox(),
        lbl_total=QLabel(),
        lbl_sub_medicamentos=QLabel(),
        lbl_sub_materiales=QLabel(),
        _install_cart_delete_action=Mock(),
    )
    for name in (
        "_cart_quantity_editor",
        "_cart_quantity_value",
        "_cart_quantity_metrics",
        "_cart_quantity_arrow_icon",
        "_install_cart_quantity_editor",
        "_cart_quantity_changed",
        "insert_or_increment_cart_item",
        "update_totals",
    ):
        setattr(owner, name, MethodType(getattr(app.MainWindow, name), owner))
    owner.errors = []
    owner.material_cart = MedicationMaterialCart(owner, owner.errors.append)
    owner.material_cart.rules = {
        "MED A": [
            {"name": "GUANTES", "units": 2, "active": True},
            {"name": "JERINGA", "units": 1, "active": True},
        ],
        "MED B": [{"name": "GUANTES", "units": 3, "active": True}],
        "VACIO": [],
    }
    yield owner
    owner.cart_table.close()


def add(cart, name, quantity):
    configured = cart.material_cart.prepare_add(name, quantity)
    cart.insert_or_increment_cart_item("Medicamentos", name, 10, quantity)
    if configured:
        cart.material_cart.finish_add(name)


def quantity(cart, category, name):
    return cart.material_cart._quantity(category, name)


def edit(cart, name, value, category="Medicamentos"):
    row = cart.material_cart.row(category, name)
    cart._cart_quantity_editor(row).setValue(value)


def remove(cart, name, category="Medicamentos"):
    row = cart.material_cart.row(category, name)
    item = cart.cart_table.item(row, 1)
    cart.material_cart.removing(row)
    cart.cart_table.removeRow(cart.cart_table.row(item))


def test_proportional_add_increment_edit_and_remove(cart):
    add(cart, "MED A", 3)
    assert quantity(cart, "Materiales", "GUANTES") == 6
    add(cart, "MED A", 2)
    assert quantity(cart, "Materiales", "GUANTES") == 10
    edit(cart, "MED A", 1)
    assert quantity(cart, "Materiales", "GUANTES") == 2
    edit(cart, "MED A", 300)
    assert quantity(cart, "Materiales", "GUANTES") == 600
    assert quantity(cart, "Materiales", "JERINGA") == 300
    cart.update_totals()
    assert cart.lbl_total.text() == "Total: RD$ 5,100.00"
    remove(cart, "MED A")
    assert cart.cart_table.rowCount() == 0


def test_shared_materials_keep_manual_floor_and_other_medication(cart):
    cart.insert_or_increment_cart_item("Materiales", "GUANTES", 2, 5)
    add(cart, "MED A", 2)
    add(cart, "MED B", 3)
    assert quantity(cart, "Materiales", "GUANTES") == 18
    edit(cart, "GUANTES", 20, "Materiales")
    remove(cart, "MED A")
    assert quantity(cart, "Materiales", "GUANTES") == 16
    remove(cart, "MED B")
    assert quantity(cart, "Materiales", "GUANTES") == 7


def test_historical_quantity_is_not_retroactively_charged(cart):
    cart.insert_or_increment_cart_item("Medicamentos", "MED A", 10, 5)
    add(cart, "MED A", 2)
    assert quantity(cart, "Materiales", "GUANTES") == 4
    edit(cart, "MED A", 4)
    assert quantity(cart, "Materiales", "GUANTES") == 0
    remove(cart, "MED A")
    assert cart.cart_table.rowCount() == 0


def test_limit_rejects_atomic_add_and_reverts_quantity_editor(cart):
    add(cart, "MED A", 1)
    cart.insert_or_increment_cart_item("Materiales", "GUANTES", 2, 999998)
    assert quantity(cart, "Materiales", "GUANTES") == 1000000
    with pytest.raises(ValueError, match="máximo"):
        add(cart, "MED A", 1)
    assert quantity(cart, "Medicamentos", "MED A") == 1
    edit(cart, "MED A", 2)
    assert quantity(cart, "Medicamentos", "MED A") == 1
    assert cart.errors and quantity(cart, "Materiales", "JERINGA") == 1


@pytest.mark.parametrize("active,prices", [(False, {"GUANTES": 2}), (True, {})])
def test_unavailable_material_prevents_partial_medication(cart, active, prices):
    cart.material_cart.rules["MED B"][0]["active"] = active
    cart.universal["Materiales"] = prices
    with pytest.raises(ValueError, match="no disponible"):
        add(cart, "MED B", 1)
    assert cart.cart_table.rowCount() == 0


def test_unconfigured_empty_and_category_name_collision(cart):
    add(cart, "SIN CONFIGURAR", 1)
    add(cart, "VACIO", 1)
    cart.universal["Materiales"]["MED A"] = 1
    cart.insert_or_increment_cart_item("Materiales", "MED A", 1, 3)
    add(cart, "MED A", 1)
    assert quantity(cart, "Materiales", "MED A") == 3
    assert quantity(cart, "Medicamentos", "MED A") == 1
    cart.material_cart.quantity_changed(
        cart.material_cart.row("Medicamentos", "SIN CONFIGURAR"), 2
    )
    cart.material_cart.quantity_changed(
        cart.material_cart.row("Medicamentos", "VACIO"), 2
    )
    cart.material_cart.reset()
    assert cart.material_cart.allocations == cart.material_cart.manual_quantities == {}


def test_manual_delete_and_material_without_editor_callback(cart):
    add(cart, "MED B", 1)
    remove(cart, "GUANTES", "Materiales")
    edit(cart, "MED B", 2)
    assert quantity(cart, "Materiales", "GUANTES") == 3
    remove(cart, "MED B")
    assert cart.cart_table.rowCount() == 0


def test_other_category_does_not_consume_medication_allocation(cart):
    add(cart, "MED B", 1)
    cart.insert_or_increment_cart_item("Procedimientos", "MED B", 50, 1)
    row = cart.material_cart.row("Procedimientos", "MED B")
    cart.material_cart.quantity_changed(row, 2)
    remove(cart, "MED B", "Procedimientos")
    assert quantity(cart, "Materiales", "GUANTES") == 3
