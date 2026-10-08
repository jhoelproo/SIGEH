"""Keep the existing billing controls reachable in small logical viewports."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTabBar

SINGLE_PANEL_WIDTH = 1180
NARROW_RECEIPT_WIDTH = 560
MINIMUM_ITEM_WIDTH = 180


def fit_billing_navigation(window):
    if not hasattr(window, "billing_view_tabs"):
        _install_view_tabs(window)
    narrow = window.width() < SINGLE_PANEL_WIDTH
    window.billing_view_tabs.setVisible(narrow)
    window.billing_view_tabs.setProperty("singlePanel", narrow)
    _show_billing_panel(window)
    fit_category_tabs(window.tabs)


def fit_category_tabs(tabs):
    tabs.setElideMode(Qt.TextElideMode.ElideNone)
    tabs.setUsesScrollButtons(True)
    tabs.setExpanding(False)
    minimum_width = "QTabBar::tab {min-width:74px;}"
    if minimum_width not in tabs.styleSheet():
        tabs.setStyleSheet(tabs.styleSheet() + minimum_width)


def _install_view_tabs(window):
    tabs = QTabBar(window.receipt_catalog_group)
    tabs.addTab("Catálogo de ítems")
    tabs.addTab("Recibo de facturación")
    tabs.setExpanding(True)
    tabs.setAccessibleName("Panel de facturación")
    window.receipt_catalog_group.layout().insertWidget(0, tabs)
    window.billing_view_tabs = tabs
    tabs.currentChanged.connect(lambda _index: _show_billing_panel(window))


def _show_billing_panel(window):
    single = window.billing_view_tabs.property("singlePanel")
    active = window.billing_view_tabs.currentIndex()
    window.catalog_panel.setVisible(not single or active == 0)
    window.receipt_panel.setVisible(not single or active == 1)


def fit_receipt_item_column(table):
    """Scroll narrow receipts instead of squeezing long service names."""
    from PySide6.QtWidgets import QHeaderView

    narrow = table.viewport().width() < NARROW_RECEIPT_WIDTH
    header = table.horizontalHeader()
    order = (1, 2, 4, 5, 0, 3) if narrow else (1, 0, 2, 3, 4, 5)
    for position, column in enumerate(order):
        header.moveSection(header.visualIndex(column), position)
    header.setSectionResizeMode(
        1, QHeaderView.Interactive if narrow else QHeaderView.Stretch
    )
    if narrow:
        table.setColumnWidth(1, MINIMUM_ITEM_WIDTH)
