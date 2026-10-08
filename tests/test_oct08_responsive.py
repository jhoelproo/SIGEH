"""Small screen geometry and existing control routing regressions."""

import pytest
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

from billing_responsive import fit_billing_navigation, fit_receipt_item_column
from billing_workspace_design import fit_billing_design
from tests.test_workspace_design import billing as billing, history as history, qt as qt


@pytest.mark.parametrize(
    "width,single", [(1024, True), (1179, True), (1180, False), (1280, False)]
)
def test_billing_panels_switch_without_recreating_controls(billing, qt, width, single):
    billing.resize(width, 680)
    fit_billing_navigation(billing)
    tabs = billing.billing_view_tabs
    assert tabs.property("singlePanel") == single
    assert not billing.catalog_panel.isHidden()
    assert billing.receipt_panel.isHidden() == single
    tabs.setCurrentIndex(1)
    assert not billing.receipt_panel.isHidden()
    assert billing.catalog_panel.isHidden() == single
    billing.resize(1920, 1000)
    fit_billing_navigation(billing)
    assert not billing.catalog_panel.isHidden()
    assert not billing.receipt_panel.isHidden()
    assert billing.billing_view_tabs is tabs


def test_small_billing_restores_wide_sizes_and_scrolls_item_names(billing, qt):
    billing.resize(1280, 680)
    fit_billing_design(billing)
    assert billing.nav_widget.width() == 110
    assert billing.patient_scroll.minimumWidth() == 240
    assert billing.catalog_panel.minimumWidth() == 360
    assert billing.bottom_widget.minimumHeight() == 48
    billing.lbl_edit_mode.setText("EDICIÓN DE RECIBO")
    fit_billing_design(billing)
    assert billing.bottom_widget.minimumHeight() == 76
    billing.cart_table.resize(400, 300)
    fit_receipt_item_column(billing.cart_table)
    assert billing.cart_table.columnWidth(1) == 180
    billing.cart_table.resize(1000, 300)
    fit_receipt_item_column(billing.cart_table)
    billing.resize(1920, 1000)
    fit_billing_design(billing)
    assert billing.nav_widget.width() == 188
    assert billing.patient_scroll.minimumWidth() == 285


@pytest.mark.parametrize("width,height", [(1200, 660), (1280, 680), (1366, 680)])
def test_history_rows_filters_typing_and_summary_remain_accessible(
    history, qt, width, height
):
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    qt.setFont(QFont("Segoe UI", 9))
    history.resize(width, height)
    history.show()
    qt.processEvents()
    workspace = history.history_workspace
    workspace.small_screen.fit()
    qt.processEvents()
    assert history.table.viewport().height() >= 220
    assert history.metrics_widget.isHidden()
    workspace.advanced_button.click()
    qt.processEvents()
    assert not workspace.advanced.isHidden()
    assert history.period_filter.isVisible()
    assert history.assignment_combo.isVisible()
    assert history.table.viewport().height() >= 130
    history.search_edit.setFocus()
    QTest.keyClicks(history.search_edit, "990430")
    assert history.search_edit.text() == "990430"
    workspace.advanced_button.click()
    workspace.small_screen.summary_button.click()
    assert not history.metrics_widget.isHidden()
    workspace.small_screen.summary_button.click()
    history.resize(1920, 1000)
    qt.processEvents()
    workspace.small_screen.fit()
    qt.processEvents()
    assert not history.metrics_widget.isHidden()
    assert history.period_filter.parentWidget() is history.search_edit.parentWidget()
    assert workspace.details.isVisible()
    assert history.search_edit.text() == "990430"
    history.clear_filters(reload=False)
    qt.processEvents()
    assert history.search_edit.hasFocus()
    QTest.keyClicks(history.search_edit, "nuevo")
    assert history.search_edit.text() == "nuevo"


def test_controlled_shared_identifiers_and_metadata_helpers():
    import CALCULOS_QT as app

    values = [None] * 16
    app._apply_shared_admission_identifiers(values, {})
    assert values[2:4] == [None, None]
    app._apply_shared_admission_identifiers(
        values, {"shared_identifiers": ["0001", "001"]}
    )
    assert values[2:4] == ["0001", "001"]
    app._apply_corrected_admission_metadata(values, None)
    app._apply_corrected_admission_metadata(
        values, {"snapshot_hash": "digest", "source_updated_at": "stamp"}
    )
    assert values[7] == "stamp" and values[9] == "digest"
