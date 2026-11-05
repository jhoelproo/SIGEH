"""Shared catalog updates and deletion through real billing widgets."""

from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
from tests.test_mainwindow_viewport_oct08 import main_window as main_window
from tests.test_billing_material_cart import cart as cart, quantity
from tests.test_nov04_workspace import settle


def test_shared_ars_names_wait_for_a_new_receipt_before_changing_tariffs(
    main_window, monkeypatch
):
    window = main_window
    changed = Mock()
    monkeypatch.setattr(window, "on_ars_changed", changed)
    with QSignalBlocker(window.ars_combo):
        window.ars_combo.clear()
        window.ars_combo.addItems(["MAPFRE"])
    window.cart_table.setRowCount(1)
    room_price = window.sala_spin.value()
    window._apply_shared_ars_names(["MAPFRE/PALIC", "HUMANO"])
    assert window.ars_combo.currentText() == "MAPFRE"
    assert window.sala_spin.value() == room_price
    changed.assert_not_called()
    window.reset_all()
    assert window.ars_combo.currentText() == "MAPFRE/PALIC"
    changed.assert_called_once_with("MAPFRE/PALIC")
    window._apply_shared_ars_names(["MAPFRE/PALIC"])
    assert window.ars_combo.currentText() == "MAPFRE/PALIC"
    window.editing_recibo_id = 8
    window._apply_shared_ars_names(["HUMANO"])
    assert window.ars_combo.currentText() == "MAPFRE/PALIC"
    window.editing_recibo_id = None
    window._apply_shared_ars_names([])
    assert window.ars_combo.count() == 0
    applied = Mock()
    monkeypatch.setattr(window, "_apply_shared_ars_names", applied)
    window._material_rules_loaded(
        {
            "links": {},
            "version": 3,
            "universal": window.universal,
            "ars_names": ["PRIMERA"],
        }
    )
    applied.assert_called_once_with(["PRIMERA"])


def test_gloves_presence_replaces_the_old_once_flag(cart, monkeypatch):
    monkeypatch.setattr(app, "FloatingToast", Mock())
    cart.preferences = {"auto_add_guantes": True}
    app.MainWindow.maybe_auto_add_guantes(cart, "Honorarios")
    cart.preferences["auto_add_guantes"] = False
    app.MainWindow.maybe_auto_add_guantes(cart, "Medicamentos")
    assert cart.cart_table.rowCount() == 0
    cart.preferences["auto_add_guantes"] = True
    app.MainWindow.maybe_auto_add_guantes(cart, "Medicamentos")
    app.MainWindow.maybe_auto_add_guantes(cart, "Medicamentos")
    assert quantity(cart, "Materiales", "GUANTES") == 1
    cart.cart_table.setRowCount(0)
    cart.universal["Materiales"] = {}
    app.MainWindow.maybe_auto_add_guantes(cart, "Medicamentos")
    assert cart.cart_table.rowCount() == 0


@pytest.mark.parametrize("fails", [False, True])
def test_catalog_delete_reloads_and_exposes_failure_without_freezing(
    monkeypatch, fails
):
    records = [
        {
            "id": 3,
            "categoria": "Materiales",
            "nombre": "GUANTES",
            "precio": 2,
            "is_active": 0,
        }
    ]
    monkeypatch.setattr(
        app,
        "load_universal_catalog_admin_data",
        lambda: {"markup": 0, "items": list(records)},
    )
    monkeypatch.setattr(app, "FloatingToast", Mock())
    warning = Mock()
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    monkeypatch.setattr(app.QMessageBox, "question", lambda *_args: app.QMessageBox.Yes)

    def delete(*_args, **_kwargs):
        if fails:
            raise ValueError("No se guardó")
        records.clear()

    monkeypatch.setattr(app, "mutate_universal_catalog", delete)
    dialog = app.UniversalCatalogAdminDialog({"role": app.ROLE_ADMIN, "username": "QA"})
    dialog.show()
    QApplication.processEvents()
    settle(dialog, "_worker")
    table = dialog.tables["Materiales"]
    table.setCurrentCell(0, 0)
    dialog._delete_item("Materiales")
    assert dialog._worker is not None
    dialog.done(0)
    assert dialog.isVisible()
    dialog.reload_async()
    settle(dialog, "_worker")
    assert table.rowCount() == int(fails)
    assert warning.call_count == int(fails)
    assert dialog.catalog_changed is not fails
    dialog.done(0)
    assert not dialog.isVisible()


def test_receipt_candidates_and_sent_reports_accept_equivalent_ars_names(monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [
        {"recibo_id": 1, "ars_snapshot": "MAPFRE"},
        {"recibo_id": 2, "ars_snapshot": "HUMANO"},
    ]
    result = app._query_available_receipts_for_batch(
        connection, 1, {"ars": "MAPFRE/PALIC"}
    )
    assert [row["recibo_id"] for row in result] == [1]
    assert "mapfre" in connection.execute.call_args.args[1][0]
    from contextlib import nullcontext

    monkeypatch.setattr(app, "db_connect", lambda: nullcontext(connection))
    app.list_sent_billing_batches_report(ars="MAPFRE/PALIC")
    assert "mapfre" in connection.execute.call_args.args[1][-1]


def test_opening_ars_manager_applies_its_refreshed_names(main_window, monkeypatch):
    window = main_window
    dialog = Mock(_ars_names=["MAPFRE/PALIC"], universal_catalog_changed=False)
    monkeypatch.setattr(app, "ARSManagerDialog", Mock(return_value=dialog))
    applied = Mock()
    monkeypatch.setattr(window, "_apply_shared_ars_names", applied)
    window.open_ars_manager()
    applied.assert_called_once_with(["MAPFRE/PALIC"])


def test_insurer_list_preserves_billing_names_when_admission_is_unavailable(
    monkeypatch,
):
    from contextlib import nullcontext

    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [
        {"nombre": "PRIMERA", "is_active": 1, "billing_enabled": True}
    ]
    monkeypatch.setattr(app, "db_connect", lambda: nullcontext(connection))
    monkeypatch.setattr(
        app.AdmissionReadOnlyRepository,
        "list_canonical_ars",
        Mock(side_effect=app.AdmissionBridgeError("synthetic")),
    )
    assert app.ars_list() == ["PRIMERA"]


def test_removed_automations_cannot_be_reenabled_in_preferences():
    dialog = app.PreferencesDialog(
        {"auto_add_guantes": True, "auto_add_bajante_cateter": True}
    )
    try:
        values = dialog.values()
        assert values["auto_add_guantes"] is True
        assert values["auto_add_bajante_cateter"] is False
        assert not hasattr(app.MainWindow, "maybe_prompt_mascarilla")
        assert not hasattr(app.MainWindow, "maybe_auto_add_bajante_cateter")
    finally:
        dialog.close()
