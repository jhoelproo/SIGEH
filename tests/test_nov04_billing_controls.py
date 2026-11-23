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
        return 1

    monkeypatch.setattr(app, "delete_universal_catalog_selection", delete)
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


@pytest.fixture
def selection_catalog(monkeypatch):
    items = [
        {
            "id": 10,
            "categoria": "Medicamentos",
            "nombre": "MED ACTIVO",
            "precio": 5,
            "is_active": 1,
        },
        {
            "id": 11,
            "categoria": "Medicamentos",
            "nombre": "MED UNO",
            "precio": 8,
            "is_active": 0,
        },
        {
            "id": 12,
            "categoria": "Medicamentos",
            "nombre": "MED DOS",
            "precio": 9,
            "is_active": 0,
        },
    ]
    monkeypatch.setattr(
        app, "load_universal_catalog_admin_data", lambda: {"markup": 35, "items": items}
    )
    dialog = app.UniversalCatalogAdminDialog({"role": app.ROLE_ADMIN, "username": "QA"})
    dialog.show()
    QApplication.processEvents()
    settle(dialog, "_worker")
    yield dialog
    dialog.close()


def select_catalog_rows(table, *rows):
    from PySide6.QtCore import QItemSelectionModel

    for row in rows:
        table.selectionModel().select(
            table.model().index(row, 0),
            QItemSelectionModel.SelectionFlag.Select
            | QItemSelectionModel.SelectionFlag.Rows,
        )


@pytest.mark.parametrize(
    "choice,deleted,confirmed",
    [
        ("Cancelar", [], []),
        ("Eliminar solo inactivos", [11, 12], []),
        ("Eliminar incluidos los activos", [10, 11, 12], [10]),
    ],
)
def test_mixed_medication_selection_warns_and_only_sends_confirmed_ids(
    selection_catalog,
    monkeypatch,
    choice,
    deleted,
    confirmed,
):
    from PySide6.QtCore import QTimer

    dialog = selection_catalog
    category = "Medicamentos"
    table = dialog.tables[category]
    select_catalog_rows(table, 0, 1, 2)
    assert table.selectionMode() == app.QAbstractItemView.ExtendedSelection
    assert "Activos en la selección: 1" in dialog.selection_labels[category].text()
    assert dialog.delete_buttons[category].isEnabled()
    assert all(
        not button.isEnabled() for button in dialog.single_item_buttons[category]
    )
    launch = Mock()
    monkeypatch.setattr(dialog, "_start_worker", launch)
    observed = {}

    def choose():
        warning = QApplication.activeModalWidget()
        observed["default"] = warning.defaultButton().text()
        observed["details"] = warning.informativeText()
        next(button for button in warning.buttons() if button.text() == choice).click()

    QTimer.singleShot(0, choose)
    dialog._delete_item(category)
    assert observed["default"] == "Cancelar"
    assert "MED ACTIVO" in observed["details"]
    if deleted:
        operation, payload, _status = launch.call_args.args
        assert operation == "DELETE_SELECTION"
        assert sorted(payload["ids"]) == deleted
        assert payload["confirmed_active_ids"] == confirmed
    else:
        launch.assert_not_called()


def test_right_click_preserves_the_selected_batch_and_search_clears_it(
    selection_catalog,
):
    dialog = selection_catalog
    category = "Medicamentos"
    table = dialog.tables[category]
    select_catalog_rows(table, 1, 2)
    point = table.visualItemRect(table.item(1, 0)).center()
    dialog._show_catalog_menu(category, point)
    assert {row["id"] for row in dialog._selected_catalog_rows(category)} == {11, 12}
    assert next(
        action
        for action in dialog._catalog_menu.actions()
        if action.text() == "Eliminar selección"
    ).isEnabled()
    assert not next(
        action for action in dialog._catalog_menu.actions() if action.text() == "Editar"
    ).isEnabled()
    dialog._catalog_menu.hide()
    point = table.visualItemRect(table.item(0, 0)).center()
    dialog._show_catalog_menu(category, point)
    assert [row["id"] for row in dialog._selected_catalog_rows(category)] == [10]
    dialog._catalog_menu.hide()

    dialog.searches[category].setText("MED UNO")
    assert dialog._selected_catalog_rows(category) == []
    assert table.isRowHidden(0) and table.isRowHidden(2)
    assert not dialog.delete_buttons[category].isEnabled()


def test_inactive_only_batch_requires_default_negative_confirmation(
    selection_catalog, monkeypatch
):
    dialog = selection_catalog
    category = "Medicamentos"
    select_catalog_rows(dialog.tables[category], 1, 2)
    question = Mock(return_value=app.QMessageBox.No)
    launch = Mock()
    monkeypatch.setattr(app.QMessageBox, "question", question)
    monkeypatch.setattr(dialog, "_start_worker", launch)
    dialog._delete_item(category)
    launch.assert_not_called()
    assert question.call_args.args[-1] == app.QMessageBox.No
    question.return_value = app.QMessageBox.Yes
    dialog._delete_item(category)
    payload = launch.call_args.args[1]
    assert sorted(payload["ids"]) == [11, 12]
    assert payload["confirmed_active_ids"] == []


def test_active_only_warning_offers_cancel_by_default(selection_catalog, monkeypatch):
    from PySide6.QtCore import QTimer

    dialog = selection_catalog
    category = "Medicamentos"
    select_catalog_rows(dialog.tables[category], 0)
    launch = Mock()
    monkeypatch.setattr(dialog, "_start_worker", launch)
    observed = {}

    def choose():
        warning = QApplication.activeModalWidget()
        observed["buttons"] = {button.text() for button in warning.buttons()}
        warning.defaultButton().click()

    QTimer.singleShot(0, choose)
    dialog._delete_item(category)
    assert observed["buttons"] == {"Cancelar", "Eliminar incluidos los activos"}
    launch.assert_not_called()


def test_catalog_can_select_a_range_with_shift(selection_catalog):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog = selection_catalog
    table = dialog.tables["Medicamentos"]
    first = table.visualItemRect(table.item(0, 0)).center()
    last = table.visualItemRect(table.item(2, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, Qt.NoModifier, first)
    QTest.mouseClick(
        table.viewport(), Qt.MouseButton.LeftButton, Qt.ShiftModifier, last
    )
    assert {row["id"] for row in dialog._selected_catalog_rows("Medicamentos")} == {
        10,
        11,
        12,
    }


def test_long_active_selection_dialog_lists_examples_and_remaining_count(
    selection_catalog,
):
    from PySide6.QtCore import QTimer

    dialog = selection_catalog
    active = [{"name": f"MED {i}", "active": True} for i in range(7)]
    observed = {}

    def cancel():
        warning = QApplication.activeModalWidget()
        observed["details"] = warning.informativeText()
        warning.defaultButton().click()

    QTimer.singleShot(0, cancel)
    assert dialog._confirm_active_catalog_deletion(active, active) == []
    assert "y 2 más" in observed["details"]
    assert "MED 6" not in observed["details"]
