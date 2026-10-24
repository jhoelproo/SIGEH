"""Boundary and failure paths for the configured billing controls."""

from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtGui import QCloseEvent

import CALCULOS_QT as app
from tests.test_billing_material_cart import cart as cart, add, remove, edit, quantity
from tests.test_ars_material_dialogs import material_dialog as material_dialog


def test_untracked_rows_and_deleted_materials_preserve_manual_choice(cart):
    cart.material_cart.finish_add("ABSENT")
    cart.insert_or_increment_cart_item("Materiales", "GUANTES", 2, 1)
    edit(cart, "GUANTES", 3, "Materiales")
    assert cart.material_cart.manual_quantities == {}
    add(cart, "UNCONFIGURED", 1)
    remove(cart, "UNCONFIGURED")
    add(cart, "MED B", 1)
    remove(cart, "GUANTES", "Materiales")
    remove(cart, "MED B")
    assert quantity(cart, "Materiales", "GUANTES") == 0


def test_busy_dialog_ignores_second_operation_and_non_checkbox_changes(material_dialog):
    dialog, save = material_dialog
    dialog._save()
    worker = dialog.worker
    extra = Mock()
    dialog._run(extra, extra)
    assert dialog.worker is worker
    extra.assert_not_called()
    dialog._selection_changed(dialog.table.item(0, 1))
    worker.wait(3000)


def selection_context():
    context = SimpleNamespace(
        material_cart=SimpleNamespace(
            prepare_add=Mock(return_value=False), finish_add=Mock()
        ),
        qty=Mock(),
        insert_or_increment_cart_item=Mock(return_value="added"),
        update_totals=Mock(),
        search=Mock(),
        maybe_auto_add_guantes=Mock(),
        maybe_auto_add_bajante_cateter=Mock(),
        maybe_prompt_honorario=Mock(),
        maybe_prompt_mascarilla=Mock(),
    )
    context.qty.value.return_value = 2
    item = Mock()
    item.data.return_value = ("MED QA", 10)
    return context, item


def test_unconfigured_selection_preserves_legacy_helpers(monkeypatch):
    monkeypatch.setattr(app, "FloatingToast", Mock())
    context, item = selection_context()
    app.MainWindow._add_catalog_selection(context, "Medicamentos", item)
    context.maybe_auto_add_guantes.assert_called_once_with("Medicamentos")
    context.maybe_auto_add_bajante_cateter.assert_called_once_with("MED QA")
    context.maybe_prompt_mascarilla.assert_called_once_with("Medicamentos", "MED QA")
    context.material_cart.finish_add.assert_not_called()


def test_unavailable_selection_is_atomic_and_blocked_fee_stops_processing(monkeypatch):
    monkeypatch.setattr(app, "FloatingToast", Mock())
    context, item = selection_context()
    context.material_cart.prepare_add.side_effect = ValueError("Material unavailable")
    app.MainWindow._add_catalog_selection(context, "Medicamentos", item)
    context.insert_or_increment_cart_item.assert_not_called()
    context.update_totals.assert_not_called()
    context.insert_or_increment_cart_item.return_value = "blocked"
    app.MainWindow._add_catalog_selection(context, "Honorarios", item)
    context.update_totals.assert_not_called()


def test_offline_bootstrap_restores_policy_and_self_pay_is_not_restricted():
    context = SimpleNamespace()
    data = {
        "material_rules": {"links": {"MED QA": []}, "version": 4},
        "honorarium_settings": [
            {
                "ars_id": 5,
                "ars_name": "ARS QA",
                "honorarium_specialties": {"GENERAL": False},
            }
        ],
    }
    app.MainWindow._initialize_material_rules(context, data)
    assert context.material_cart.rules == {"MED QA": []}
    assert context._material_rules_version == 4
    context.coverage_combo = SimpleNamespace(currentText=lambda: "No asegurado")
    context._current_ars_id = 5
    assert app.receipt_honorarium_allowed(context)
    app.HONORARIUM_PROMPT_SETTINGS.update([])


def test_close_with_completed_or_absent_worker_does_not_defer():
    event = QCloseEvent()
    assert not app._defer_material_query_close(SimpleNamespace(), event)
    worker = SimpleNamespace(isRunning=lambda: False)
    assert not app._defer_material_query_close(
        SimpleNamespace(_material_rules_worker=worker), event
    )
    assert app._closing_user_data(SimpleNamespace(current_user=None)) == {}
