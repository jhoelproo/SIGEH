"""Integrated catalog/cart presentation, retaining the existing Qt actions."""

from types import MethodType
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QHeaderView, QToolButton, QStyleOptionViewItem

import CALCULOS_QT as app
from billing_workspace_design import fit_billing_design
from tests.test_workspace_design import billing as billing, catalog as catalog, qt as qt


def test_catalog_uses_one_renderer_instead_of_drawing_legacy_rows(catalog):
    widget = catalog.current_list()
    assert not isinstance(widget.itemDelegate(), app.CatalogItemDelegate)
    assert widget.spacing() == 0
    assert "padding: 0" in widget.styleSheet()
    pixmap = QPixmap(120, 40)
    pixmap.fill(Qt.magenta)
    previous = pixmap.toImage()
    painter = QPainter(pixmap)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 120, 40)
    widget.itemDelegate().paint(painter, option, widget.model().index(0, 0))
    painter.end()
    assert pixmap.toImage() == previous


def test_catalog_has_aligned_column_headings_and_primary_row_action(catalog):
    assert catalog.header.findChild(app.QLabel, "CatalogNameHeading").text() == (
        "Nombre del ítem"
    )
    row = catalog.current_list().itemWidget(catalog.current_list().item(0))
    assert row.findChild(app.QPushButton).text() == "+  Añadir"
    assert row.findChild(app.QLabel, "CatalogPrice").styleSheet() == ""


def test_loading_inactive_catalog_does_not_change_current_page(catalog):
    catalog.show_page(1)
    other = catalog.window.source_lists["Materiales"]
    item = app.QListWidgetItem("MATERIAL FICTICIO")
    item.setData(Qt.UserRole, ("MATERIAL FICTICIO", 5.99))
    item.setData(app.CATALOG_CATEGORY_ROLE, "Materiales")
    other.addItem(item)
    catalog.filled(other)
    assert catalog.page == 1
    assert other.itemWidget(other.item(0)) is not None


def test_receipt_columns_fill_the_card_without_shrinking_quantity(billing):
    billing.resize(1680, 950)
    fit_billing_design(billing)
    table = billing.cart_table
    assert table.horizontalHeader().sectionResizeMode(1) == QHeaderView.Stretch
    assert table.columnWidth(0) >= 96
    assert table.columnWidth(2) >= 84
    assert table.columnWidth(5) >= 48


def test_summary_and_item_count_are_integrated_in_the_receipt_card(billing):
    assert billing.cart_count.parent() is billing.items_presentation.receipt_heading
    assert billing.lbl_total.parent() is billing.items_presentation.summary_card
    assert billing.lbl_sub_medicamentos.parent() is billing.lbl_total.parent()


def test_existing_quantity_editor_is_reused_with_horizontal_controls(billing):
    for name in (
        "_cart_quantity_metrics",
        "_cart_quantity_arrow_icon",
        "_cart_quantity_editor",
    ):
        setattr(billing, name, MethodType(getattr(app.MainWindow, name), billing))
    billing._cart_quantity_changed = Mock()
    billing.cart_table.setRowCount(1)
    app.MainWindow._install_cart_quantity_editor(billing, 0, 1)
    editor = billing._cart_quantity_editor(0)
    billing.items_presentation.refresh_rows()
    holder = billing.cart_table.cellWidget(0, 2)
    up = holder.findChild(QToolButton, "CartQuantityUp")
    down = holder.findChild(QToolButton, "CartQuantityDown")
    assert up.toolButtonStyle() == Qt.ToolButtonTextOnly
    assert down.text() == "−" and up.text() == "+"
    assert billing._cart_quantity_editor(0) is editor
    up.click()
    assert editor.value() == 2
    down.click()
    assert editor.value() == 1
    down.click()
    assert editor.value() == 1
    editor.setValue(300)
    up.click()
    assert editor.value() == 300
    QTest.keyClick(editor, Qt.Key_Down)
    assert editor.value() == 299
    app.MainWindow._apply_cart_quantity_metrics(billing)
    billing.items_presentation.fit()
    assert editor.width() == 32
    assert up.width() == down.width() == 24
    app.MainWindow._refresh_cart_action_icons(billing)
    assert up.toolButtonStyle() == Qt.ToolButtonTextOnly
    billing.items_presentation.refresh_rows()
    assert holder.layout().count() == 5


@pytest.mark.parametrize("missing", ["editor", "up", "down", "layout", "arrows"])
def test_quantity_presentation_tolerates_incomplete_legacy_widgets(qt, missing):
    from billing_items_design import present_quantity_holder

    holder = app.QWidget()
    if missing != "layout":
        app.QHBoxLayout(holder)
    names = {
        "editor": "CartQuantitySpin",
        "up": "CartQuantityUp",
        "down": "CartQuantityDown",
        "arrows": "CartQuantityArrowColumn",
    }
    for key, name in names.items():
        if key != missing:
            control = (
                app.QToolButton(holder)
                if key in ("up", "down")
                else app.QWidget(holder)
            )
            control.setObjectName(name)
    present_quantity_holder(holder)
    assert bool(holder.property("horizontalQuantity")) == (missing == "arrows")
    holder.close()


@pytest.mark.parametrize("dark", [True, False])
def test_receipt_painting_keeps_category_price_and_model_values(billing, qt, dark):
    controller = billing.items_presentation
    controller.apply_theme(dark)
    table = billing.cart_table
    categories = list(app.ALL_CATEGORIES) + ["Desconocida", ""]
    table.setRowCount(len(categories))
    for row, category in enumerate(categories):
        for column, value in enumerate(
            (category, "<NOMBRE FICTICIO>", "300", "$7,020.99", "$2,106,297.00", "")
        ):
            table.setItem(row, column, app.QTableWidgetItem(value))
    model = table.model()
    pixmap = QPixmap(160, 60)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    for row in range(len(categories)):
        for column in range(6):
            option = QStyleOptionViewItem()
            option.rect = QRect(0, 0, 160, 60)
            option.widget = table if row % 2 else None
            controller.delegate.paint(painter, option, model.index(row, column))
    painter.end()
    assert not pixmap.isNull()
    assert table.item(0, 3).text() == "$7,020.99"
    assert table.item(0, 4).text() == "$2,106,297.00"
    assert table.item(0, 1).text() == "<NOMBRE FICTICIO>"
    assert controller.delegate.dark is dark


def test_resize_events_are_coalesced_and_headers_restore_full_category(billing, qt):
    controller = billing.items_presentation
    billing.tabs.setTabToolTip(0, "Medicamentos")
    billing.tabs.setTabText(0, "Med.")
    controller.fit_catalog()
    assert billing.tabs.tabText(0) == "Medicamentos"
    assert (
        controller.eventFilter(billing.cart_table.viewport(), QEvent(QEvent.Enter))
        is False
    )
    controller.eventFilter(billing.cart_table.viewport(), QEvent(QEvent.Resize))
    assert controller.pending_resize
    controller.eventFilter(billing.cart_table.viewport(), QEvent(QEvent.Show))
    qt.processEvents()
    assert not controller.pending_resize
    billing.cart_table.setRowCount(1)
    controller.refresh_rows()
    assert billing.cart_count.text() == "1 ítems"


def test_receipt_tooltips_preserve_long_names_without_mutating_data(billing):
    from billing_workspace_design import update_billing_summary

    billing.cart_table.setRowCount(2)
    name = "SUTURA DE HERIDA ÚNICA DE CARA CON DESCRIPCIÓN EXTENSA"
    billing.cart_table.setItem(0, 0, app.QTableWidgetItem("Procedimientos"))
    billing.cart_table.setItem(0, 1, app.QTableWidgetItem(name))
    update_billing_summary(billing)
    assert billing.cart_table.item(0, 1).toolTip() == name
    assert billing.cart_table.item(0, 1).text() == name
    assert billing.cart_count.text() == "2 ítems"


@pytest.mark.parametrize("width", [540, 850])
def test_receipt_headers_and_summary_fit_the_available_width(billing, qt, width):
    billing.receipt_panel.resize(width, 720)
    billing.cart_table.resize(width - 24, 300)
    billing.receipt_panel.show()
    billing.cart_table.show()
    from billing_workspace_design import _fit_cart_summary

    _fit_cart_summary(billing, width < 650)
    billing.items_presentation.fit()
    qt.processEvents()
    table = billing.cart_table
    assert table.horizontalScrollBar().maximum() == 0, (
        table.viewport().width(),
        table.horizontalHeader().length(),
        [table.columnWidth(column) for column in range(6)],
    )
    assert table.horizontalHeader().visualIndex(1) == 0
    assert not billing.lbl_total.isHidden()
    assert not billing.lbl_sub_medicamentos.isHidden()
    assert billing.cart_summary_layout.columnStretch(0) == 1
    assert billing.cart_summary_layout.columnStretch(1) == 1
