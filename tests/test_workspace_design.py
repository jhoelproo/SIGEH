"""Native UI behavior: paging, prices, selection, filters, permissions and identity."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidgetItem,
    QPushButton,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QFormLayout,
    QLineEdit,
    QDoubleSpinBox,
    QDateEdit,
    QGroupBox,
    QGridLayout,
    QSplitter,
)

import CALCULOS_QT as app
from billing_workspace_design import (
    billing_identity_nss,
    update_billing_summary,
    install_billing_design,
    fit_billing_design,
)
from catalog_workspace_design import CatalogWorkspace, CatalogNameLabel
from monthly_workspace_design import monthly_row_state
from tests import test_monthly_ars_form_state as monthly_tests
from workspace_design import DetailPanel, DetailResponsive, workspace_styles
from workspace_selection import RowSelectionChecks


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def catalog(qt):
    surface = QWidget()
    layout = QVBoxLayout(surface)
    search = QHBoxLayout()
    search.addWidget(QLabel("Buscar"))
    layout.addLayout(search)
    tabs = QTabBar()
    tabs.addTab("Medicamentos")
    tabs.addTab("Materiales")
    lists = {name: app.CatalogListWidget() for name in ("Medicamentos", "Materiales")}
    for widget in lists.values():
        layout.addWidget(widget)
    owner = SimpleNamespace(
        catalog_layout=layout,
        tabs=tabs,
        source_lists=lists,
        get_current_category=lambda: list(lists)[tabs.currentIndex()],
        add_selected_item=Mock(),
    )
    controller = CatalogWorkspace(owner, app)
    for index in range(30):
        item = QListWidgetItem(f"Medicamento {index:02}")
        item.setData(Qt.UserRole, (item.text(), 100 - index))
        item.setData(app.CATALOG_CATEGORY_ROLE, "Medicamentos")
        item.setData(app.CATALOG_FAVORITE_ROLE, index == 29)
        lists["Medicamentos"].addItem(item)
    controller.filled(lists["Medicamentos"])
    yield controller
    surface.close()


def test_catalog_paging_first_last_empty_and_keyboard_selection(catalog):
    widget = catalog.current_list()
    assert widget.item(0).data(Qt.UserRole)[0] == "Medicamento 29"
    assert sum(not widget.item(i).isHidden() for i in range(30)) == 13
    assert not catalog.previous.isEnabled()
    catalog.next.click()
    assert catalog.page == 1
    assert not widget.currentItem().isHidden()
    catalog.show_page(999)
    assert catalog.page == 2
    assert not catalog.next.isEnabled()
    catalog.previous.click()
    catalog.size.setCurrentText("8")
    assert catalog.page == 0 and catalog.page_size == 8
    catalog.window.tabs.setCurrentIndex(1)
    assert "0–0 de 0" in catalog.counter.text()
    assert not catalog.next.isEnabled()
    catalog.show_page(-1)
    assert catalog.page == 0


@pytest.mark.parametrize(
    "order,expected", [(1, "Medicamento 28"), (2, "Medicamento 00")]
)
def test_catalog_price_sort_and_row_add_preserve_category_price(
    catalog, order, expected
):
    catalog.sort.setCurrentIndex(order)
    widget = catalog.current_list()
    assert widget.item(1).data(Qt.UserRole)[0] == expected
    item = widget.item(1)
    row = widget.itemWidget(item)
    row.findChild(QPushButton).click()
    catalog.window.add_selected_item.assert_called_once_with(
        category_override="Medicamentos"
    )
    assert widget.currentItem() is item
    assert item.data(Qt.UserRole)[1] == (72 if order == 1 else 100)


def test_catalog_favorite_button_emits_existing_persistence_signal(catalog):
    widget = catalog.current_list()
    calls = []
    widget.favoriteToggled.connect(lambda *args: calls.append(args))
    widget.itemWidget(widget.item(0)).findChild(QToolButton).click()
    assert calls == [("Medicamento 29", 71.0, False)]
    widget.itemWidget(widget.item(1)).findChild(QToolButton).click()
    assert calls[-1][2] is True


def test_long_catalog_names_fit_without_overlapping_price_or_actions(catalog, qt):
    row = catalog.row_widget(catalog.current_list(), catalog.current_list().item(0))
    row.resize(290, 44)
    row.show()
    label = row.findChild(CatalogNameLabel)
    label.full_name = "ACETAMINOFÉN (PARACETAMOL) GOTAS PEDIÁTRICAS 100 MG"
    label.setToolTip(label.full_name)
    row.resize(291, 44)
    qt.processEvents()
    price = next(item for item in row.findChildren(QLabel) if item is not label)
    assert label.geometry().bottom() < price.geometry().top()
    assert label.fontMetrics().horizontalAdvance(label.text()) <= label.width()
    assert label.toolTip() == label.full_name
    row.resize(1000, 44)
    qt.processEvents()
    assert label.text() == label.full_name
    assert label.geometry().right() < price.geometry().left()
    assert row.item.sizeHint().height() == 44
    row.close()


def test_checkboxes_native_selection_keyboard_and_visible_all(qt):
    table = QTableWidget(3, 1)
    for row in range(3):
        table.setItem(row, 0, QTableWidgetItem(str(row)))
    checks = RowSelectionChecks(table, 1)
    for row in range(3):
        checks.add_row(row)
    checks.toggle_visible(0)
    assert not table.selectionModel().selectedRows()
    table.item(0, 1).setCheckState(Qt.Checked)
    assert {i.row() for i in table.selectionModel().selectedRows()} == {0}
    table.item(0, 1).setCheckState(Qt.Unchecked)
    table.setRowHidden(2, True)
    checks.toggle_visible(1)
    assert {i.row() for i in table.selectionModel().selectedRows()} == {0, 1}
    checks.toggle_visible(1)
    assert not table.selectionModel().selectedRows()
    table.selectRow(2)
    assert table.item(2, 1).checkState() == Qt.Checked
    table.clearSelection()
    assert table.item(2, 1).checkState() == Qt.Unchecked
    table.close()


@pytest.mark.parametrize(
    "entry,problems,state",
    [
        ({"service_date_snapshot": "2026-10-05", "total_snapshot": 100}, [], "ready"),
        (
            {"service_date_snapshot": "2026-10-05", "total_snapshot": 100},
            ["NSS"],
            "review",
        ),
        ({"service_date_snapshot": "invalid", "total_snapshot": 100}, [], "errors"),
        ({"service_date_snapshot": "2026-10-05", "total_snapshot": 0}, [], "errors"),
        ({"service_date_snapshot": "2026-10-05", "total_snapshot": -1}, [], "errors"),
    ],
)
def test_monthly_status_separates_missing_identity_from_invalid_services(
    entry, problems, state
):
    assert monthly_row_state(entry, problems) == state


@pytest.fixture
def monthly(qt):
    monthly_tests.MonthlyArsFormStateTests.qt_app = qt
    fixture = monthly_tests.MonthlyArsFormStateTests()
    fixture.setUp()
    fixture._load_test_patients()
    yield fixture.page
    fixture.tearDown()


def test_monthly_filter_detail_and_single_edit_permissions(monthly):
    controller = monthly.monthly_workspace
    controller.buttons["ready"].click()
    assert all(monthly.patients.isRowHidden(row) for row in range(3))
    controller.buttons["review"].click()
    monthly.patients.selectRow(0)
    assert controller.details.patient.text() == "PACIENTE 1"
    assert controller.details.buttons["edit"].isEnabled()
    monthly.patient_search.setText("PACIENTE 2")
    assert monthly._selected_receipts() == []
    assert not controller.details.buttons["edit"].isEnabled()
    controller.buttons["errors"].click()
    assert all(monthly.patients.isRowHidden(row) for row in range(3))
    controller.buttons["all"].click()
    monthly.patient_search.clear()
    controller.selection.toggle_visible(11)
    assert len(monthly._selected_receipts()) == 3
    assert not controller.details.buttons["edit"].isEnabled()


def test_monthly_row_menu_uses_existing_actions(monthly, monkeypatch):
    callback = Mock()
    monkeypatch.setattr(monthly, "_show_patients_context_menu", callback)
    monthly.monthly_workspace.row_menu(1)
    callback.assert_called_once()
    assert monthly._selected_receipt()["recibo_id"] == 2


@pytest.fixture
def history(qt, monkeypatch):
    monkeypatch.setattr(
        app.ReceiptHistoryDialog, "load_rows", lambda *args, **kwargs: None
    )
    history = app.ReceiptHistoryDialog(
        SimpleNamespace(
            current_user={"role": app.ROLE_ADMIN, "username": "qa"}, is_dark_mode=True
        )
    )
    yield history
    history.close()


def test_history_detail_actions_filters_sorting_and_error_metrics(history, monkeypatch):
    controller = history.history_workspace
    entry = {
        "id": 1,
        "numero": 42,
        "nombre": "<PACIENTE>",
        "ars": "FUTURO",
        "numero_autorizacion": "001234",
        "admission_nss_snapshot": "001234567",
        "total": 100,
        "estado_facturacion": app.BILLING_PENDING,
        "estado_documento": app.DOCUMENT_READY,
    }
    monkeypatch.setattr(history, "_selected_receipt", lambda: entry)
    history._update_action_state()
    assert controller.details.fields["authorization"].text() == "001234"
    assert controller.details.fields["nss"].text() == "001234567"
    assert controller.details.patient.textFormat() == Qt.PlainText
    opener = Mock()
    history.btn_open_receipt.clicked.disconnect()
    history.btn_open_receipt.clicked.connect(opener)
    controller.details.buttons["open"].click()
    # Avoid the original handler's filesystem work in this synthetic test.
    assert opener.call_count == 1
    controller.filter_metric("ready", app.BILLING_ALL)
    assert history._history_filter_values()["flow_filter"] == "ready"
    history.status_combo.setCurrentIndex(
        history.status_combo.findData(app.BILLING_PENDING)
    )
    assert controller.flow == "all"
    controller.sort_changed("total")
    assert history._history_filter_values()["sort_order"] == "total"
    controller.metrics_loaded({"queue": {"ready": 7}})
    assert "7" in history.ready_summary.text()
    controller.metrics_loaded({"_errors": {"metrics": "failure"}})
    assert "No disponible" in history.ready_summary.text()
    controller.filter_metric("ready", "missing-status")
    history.clear_filters()
    assert controller.flow == "all"
    monkeypatch.setattr(history, "_selected_receipt", lambda: None)
    history._update_action_state()
    assert not controller.details.buttons["authorization"].isEnabled()


def test_history_row_menu_and_advanced_filters(history, monkeypatch):
    callback = Mock()
    monkeypatch.setattr(history, "show_context_menu", callback)
    history.table.setRowCount(1)
    history.history_workspace.row_added(0)
    history.table.cellWidget(0, 19).click()
    callback.assert_called_once()
    advanced = next(
        button
        for button in history.findChildren(QPushButton)
        if button.text() == "Filtros avanzados"
    )
    history.show()
    advanced.click()
    assert not history.history_workspace.advanced.isHidden()
    advanced.click()
    assert history.history_workspace.advanced.isHidden()


def test_ready_history_query_composes_parameterized_status_filter():
    where, parameters = app._receipt_history_filter_sql(
        flow_filter="ready", billing_status=app.BILLING_PENDING
    )
    assert "r.estado_documento=%s" in where
    assert "COALESCE(r.estado_facturacion, %s)=%s" in where
    assert parameters[-3:] == (
        app.DOCUMENT_READY,
        app.BILLING_UNCLASSIFIED,
        app.BILLING_PENDING,
    )


def test_history_preserves_loaded_filter_and_legacy_controls(history):
    controller = history.history_workspace
    history.ars_combo.addItem("FUTURO")
    history.ars_combo.setCurrentIndex(1)
    del history.history_workspace
    history._on_history_metrics_loaded(
        history._query_generation,
        {"filters": {"ars": ["FUTURO", "OTRA"], "users": ["qa"]}},
    )
    assert history.ars_combo.currentText() == "FUTURO"
    history._append_row({"id": 1, "numero": 42, "nombre": "FICTICIO"})
    assert history.table.item(0, 1).text() == "42"
    history._update_action_state()
    history.clear_filters(reload=False)
    assert history.ars_combo.currentIndex() == 0
    assert history.search_edit.text() == ""
    history.history_workspace = controller


@pytest.mark.parametrize(
    "role,available,assignee",
    [
        (app.ROLE_ADMIN, True, ""),
        (app.ROLE_AUDIT, True, "otro_auditor"),
        ("sin_permisos", False, ""),
    ],
)
def test_history_context_authorization_action_obeys_role_and_routes_to_dialog(
    history, monkeypatch, role, available, assignee
):
    history.main_window.current_user = {"role": role, "username": "qa"}
    entry = {
        "id": 1,
        "numero": 42,
        "nombre": "PACIENTE FICTICIO",
        "total": 100,
        "estado_facturacion": app.BILLING_PENDING,
        "estado_documento": app.DOCUMENT_READY,
        "auditoria_asignada_a": assignee,
    }
    history._page_rows_by_id[1] = entry
    history._append_row(entry)
    history.table.selectRow(0)
    history._update_action_state()
    calls = []
    import receipt_authorization_dialog

    monkeypatch.setattr(
        receipt_authorization_dialog,
        "open_receipt_authorization",
        lambda *args: calls.append(args),
    )

    class InspectedMenu(app.QMenu):
        def exec(self, _position):
            actions = [
                action
                for action in self.actions()
                if action.text() == "Registrar autorización…"
            ]
            assert bool(actions) is available
            if actions:
                actions[0].trigger()

    monkeypatch.setattr(app, "QMenu", InspectedMenu)
    point = history.table.visualRect(history.table.model().index(0, 1)).center()
    history.show_context_menu(point)
    assert len(calls) == int(available)
    if calls:
        assert calls[0] == (history, app)


@pytest.mark.parametrize(
    "attention,saved,expected",
    [
        (None, "", ""),
        ({"nss_snapshot": "1234"}, "", "1234"),
        ({"nss_clean": "000123"}, "", "000123"),
        ({"nss": "old"}, "latest", "latest"),
        ({"admission_nss_snapshot": "4567"}, "", "4567"),
    ],
)
def test_billing_nss_sources_prefer_saved_correction(attention, saved, expected):
    assert (
        billing_identity_nss(
            SimpleNamespace(
                current_admission_attention=attention, receipt_identity_nss=saved
            )
        )
        == expected
    )
    update_billing_summary(SimpleNamespace())


def test_detail_panel_resize_and_theme_preserve_plain_values(qt):
    surface = QWidget()
    panel = DetailPanel("Paciente", (("nss", "NSS"),), parent=surface)
    surface.resize(1300, 700)
    surface.show()
    controller = DetailResponsive(surface, panel)
    qt.processEvents()
    assert panel.isVisible()
    surface.resize(1000, 700)
    qt.processEvents()
    assert not panel.isVisible()
    surface.resize(1300, 700)
    qt.processEvents()
    assert panel.isVisible()
    panel.show_values("<Paciente>", {"nss": "000123"})
    assert panel.fields["nss"].text() == "000123"
    assert controller.eventFilter(surface, app.QEvent(app.QEvent.Show)) is False
    assert workspace_styles(True) != workspace_styles(False)
    surface.close()


class BillingSurface(QWidget):
    theme_toggled = Signal(bool)


@pytest.fixture
def billing(qt):
    window = BillingSurface()
    window.current_user = {"full_name": "COORDINADOR FICTICIO DE PRUEBA"}
    window.current_admission_attention = {"nss_clean": "001234567"}
    window.receipt_identity_nss = ""
    window.is_dark_mode = True
    window.billing_workspace = QWidget(window)
    window.header_widget = QWidget(window)
    window.header_widget.setObjectName("HeaderWidget")
    window.header_title = QLabel(window.header_widget)
    window.header_subtitle = QLabel(window.header_widget)
    window.lbl_user_top = QLabel(window.header_widget)
    window.nav_widget = QWidget(window)
    window.nav_widget.setObjectName("NavWidget")
    window.btn_add_catalog_item = QPushButton(window.nav_widget)
    window.btn_import_meds = QPushButton(window.nav_widget)
    QPushButton("Historial", window.nav_widget)
    window.billing_group = QGroupBox(window)
    patient_layout = QVBoxLayout(window.billing_group)
    window.billing_layout = QFormLayout()
    patient_layout.addLayout(window.billing_layout)
    patient_layout.addStretch()
    window.name_edit, window.authorization_edit = QLineEdit(), QLineEdit()
    window.ars_combo, window.coverage_combo = QComboBox(), QComboBox()
    window.ars_combo.addItem("FUTURO")
    window.coverage_combo.addItem("Asegurado")
    window.sala_spin = QDoubleSpinBox()
    window.date_edit = QDateEdit()
    window.billing_layout.addRow("Paciente", window.name_edit)
    window.receipt_catalog_group = QGroupBox(window)
    window.catalog_panel = QWidget()
    window.receipt_panel = QWidget()
    window.catalog_layout = QVBoxLayout(window.catalog_panel)
    window.receipt_layout = QVBoxLayout(window.receipt_panel)
    search = QHBoxLayout()
    search.addWidget(QLineEdit())
    window.catalog_layout.addLayout(search)
    window.tabs = QTabBar()
    window.tabs.addTab("Medicamentos")
    window.source_lists = {"Medicamentos": app.CatalogListWidget()}
    window.catalog_layout.addWidget(window.source_lists["Medicamentos"])
    window.get_current_category = lambda: "Medicamentos"
    window.cart_group = QGroupBox(window.receipt_panel)
    QVBoxLayout(window.cart_group)
    window.cart_table = QTableWidget(0, 6, window.cart_group)
    window.lbl_total = QLabel("Total: RD$ 500.00")
    window.lbl_sub_medicamentos, window.lbl_sub_materiales = QLabel(), QLabel()
    window.cart_summary_layout = QGridLayout()
    window.cart_group.layout().addLayout(window.cart_summary_layout)
    window.patient_scroll = QWidget()
    window.main_split = QSplitter()
    window.main_split.addWidget(window.catalog_panel)
    window.main_split.addWidget(window.receipt_panel)
    window.bottom_widget = QWidget()
    window.bottom_layout = QGridLayout(window.bottom_widget)
    window.lbl_edit_mode = QLabel()
    window.btn_cancel_edit, window.btn_reset, window.btn_generate = (
        QPushButton(),
        QPushButton(),
        QPushButton(),
    )
    window.btn_add = QPushButton()
    install_billing_design(window, app)
    yield window
    window.close()


def test_billing_visible_nss_updates_summary_reset_and_theme(billing):
    assert billing.nss_display.text() == "001234567"
    assert billing.nss_display.isReadOnly()
    billing.authorization_edit.setText("00001234")
    billing.sala_spin.setValue(99)
    assert billing.patient_summary_labels["Autorización"].text() == "00001234"
    assert billing.patient_summary_labels["Sala"].text() == "RD$ 99.00"
    billing.current_admission_attention = None
    update_billing_summary(billing)
    assert billing.nss_display.text() == ""
    assert billing.patient_summary_labels["NSS"].text() == "No registrado"
    dark = billing.billing_workspace.styleSheet()
    billing.theme_toggled.emit(False)
    assert billing.billing_workspace.styleSheet() != dark
    billing.date_edit.setDate(app.QDate(2026, 10, 5))
    assert billing.patient_summary_labels["Fecha"].text() == "05-10-2026"


def test_billing_compact_and_wide_geometry_keep_actions_available(billing):
    fit_billing_design(SimpleNamespace())
    billing.resize(1366, 768)
    fit_billing_design(billing)
    assert billing.patient_scroll.minimumWidth() >= 270
    assert billing.bottom_layout.indexOf(billing.btn_generate) >= 0
    assert billing.catalog_workspace.sort.maximumWidth() == 140
    assert (
        billing.cart_table.horizontalHeader().sectionResizeMode(1)
        == app.QHeaderView.Stretch
    )
    assert billing.lbl_total.minimumHeight() == 54
    billing.resize(1680, 950)
    fit_billing_design(billing)
    assert billing.catalog_workspace.sort.maximumWidth() == 190
    assert billing.lbl_total.minimumHeight() == 78
    assert billing.tabs.usesScrollButtons()
