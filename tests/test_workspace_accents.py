"""Reference colors preserve actions, model values and permissions."""

import pytest
from PySide6.QtCore import QEvent, Qt, QRect
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
)

from tests.test_workspace_design import billing as billing
from tests.test_workspace_design import catalog as catalog
from tests.test_workspace_design import history as history
from tests.test_workspace_design import monthly as monthly
from tests.test_workspace_design import qt as qt
from tests.test_workspace_design import app as app


def test_sidebar_and_catalog_add_have_vivid_accents(billing):
    assert billing.btn_add_catalog_item.property("accentTone") == "green"
    assert billing.btn_import_meds.property("accentTone") == "amber"
    assert billing.btn_add.property("accentTone") == "green"
    assert "qlineargradient" in billing.btn_add.styleSheet()


def test_catalog_pagination_color_preserves_first_last_behavior(billing):
    controller = billing.catalog_workspace
    controller.show_page(0)
    assert controller.previous.property("accentTone") == "blue"
    assert controller.next.property("accentTone") == "blue"
    assert not controller.previous.isEnabled()
    assert not controller.next.isEnabled()


def test_monthly_metrics_and_filters_use_reference_colors(monthly):
    controller = monthly.monthly_workspace
    assert controller.buttons["review"].property("accentTone") == "amber"
    assert controller.buttons["ready"].property("accentTone") == "green"
    assert controller.buttons["errors"].property("accentTone") == "red"
    assert controller.counts["total"].parentWidget().property("accentTone") == "purple"


def test_monthly_status_badges_keep_original_state_and_text(monthly):
    from workspace_accents import StatusBadgeDelegate

    assert isinstance(monthly.patients.itemDelegateForColumn(1), StatusBadgeDelegate)
    item = monthly.patients.item(0, 1)
    assert item.text() == "REVISAR"
    assert monthly.patients.item(0, 0).data(Qt.UserRole) == "review"


def test_history_metrics_and_actions_have_reference_accents(history):
    from workspace_accents import StatusBadgeDelegate

    cards = history.metrics_widget.layout()
    assert [cards.itemAt(i).widget().property("accentTone") for i in range(4)] == [
        "amber",
        "green",
        "blue",
        "purple",
    ]
    assert isinstance(history.table.itemDelegateForColumn(9), StatusBadgeDelegate)
    assert history.btn_open_receipt.property("accentTone") == "blue"
    assert history.btn_bulk_invoice.property("accentTone") == "green"
    assert history.btn_delete_receipt.property("accentTone") == "red"


def test_theme_switch_keeps_disabled_actions_and_filter_callbacks(monthly):
    controller = monthly.monthly_workspace
    monthly.edit_patient_button.setEnabled(False)
    monthly.apply_monthly_theme(False)
    assert not monthly.edit_patient_button.isEnabled()
    assert "QPushButton:disabled" in monthly.edit_patient_button.styleSheet()
    controller.buttons["ready"].click()
    assert controller.state == "ready"
    assert controller.buttons["ready"].isChecked()
    assert not controller.buttons["review"].isChecked()
    assert controller.details.buttons["edit"].findChildren(QPushButton) == []


@pytest.mark.parametrize("dark", [True, False])
@pytest.mark.parametrize("tone", ["blue", "green", "amber", "purple", "red", "unknown"])
def test_action_palette_keeps_callback_state_and_disabled_feedback(qt, dark, tone):
    from workspace_accents import accent_colors, style_action

    button = QPushButton("Acción")
    button.setCheckable(True)
    calls = []
    button.clicked.connect(lambda checked=False: calls.append(checked))
    style_action(button, tone, dark)
    button.click()
    assert calls == [True]
    assert button.isChecked()
    assert "qlineargradient" in button.styleSheet()
    button.setEnabled(False)
    style_action(button, tone, dark, filled=False, compact=True)
    button.click()
    assert calls == [True]
    assert not button.isEnabled()
    assert accent_colors(tone, dark)["foreground"] in button.styleSheet()
    assert "QPushButton:disabled" in button.styleSheet()
    button.close()


@pytest.mark.parametrize(
    "text,tone",
    [
        (None, "blue"),
        ("", "blue"),
        ("desconocido", "blue"),
        ("No facturado", "red"),
        ("FACTURADO", "blue"),
        ("LISTO", "green"),
        ("COMPLETO", "green"),
        ("PAGADO", "green"),
        ("EXONERADO", "purple"),
        ("HISTÓRICO", "purple"),
        ("REVISAR", "amber"),
        ("Pendiente de validación", "amber"),
        ("PRELIMINAR", "amber"),
        ("Con errores", "red"),
        ("RECHAZADO", "red"),
    ],
)
def test_state_colors_distinguish_negative_and_unknown_states(text, tone):
    from workspace_accents import status_tone

    assert status_tone(text) == tone


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0\nTotal pacientes", ("0", "Total pacientes", "")),
        ("RD$ 1,512.00\nTotal del envío", ("RD$ 1,512.00", "Total del envío", "")),
        (
            "VALIDACIÓN\n1,830 recibos · RD$ 2,189,889.24",
            ("1,830", "VALIDACIÓN", "RD$ 2,189,889.24"),
        ),
        (
            "FACTURACIÓN\nFacturados: 180 · No facturados: 0",
            ("180", "FACTURACIÓN", "No facturados: 0"),
        ),
        ("Listos para auditoría\n448", ("448", "Listos para auditoría", "")),
        ("VALIDACIÓN\nNo disponible", ("—", "VALIDACIÓN", "No disponible")),
        ("Cargando", ("—", "Cargando", "")),
        (None, ("—", "", "")),
    ],
)
def test_metric_presentation_handles_zero_totals_and_unavailable_data(text, expected):
    from workspace_accents import metric_parts

    assert metric_parts(text) == expected


@pytest.mark.parametrize("dark", [True, False])
@pytest.mark.parametrize(
    "text", ["LISTO", "REVISAR", "NO FACTURADO", "FACTURADO", "EXONERADO", ""]
)
def test_status_delegate_renders_badge_without_changing_model(qt, dark, text):
    from workspace_accents import StatusBadgeDelegate, accent_colors, status_tone

    table = QTableWidget(1, 1)
    item = QTableWidgetItem(text)
    item.setData(Qt.UserRole, "original-state")
    item.setBackground(QBrush(QColor("#875a22")))
    table.setItem(0, 0, item)
    delegate = StatusBadgeDelegate(table)
    delegate.dark = dark
    pixmap = QPixmap(180, 44)
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 180, 44)
    option.widget = table if dark else None
    delegate.paint(painter, option, table.model().index(0, 0))
    painter.end()
    assert item.text() == text
    assert item.data(Qt.UserRole) == "original-state"
    assert item.background().color().name() == "#875a22"
    if text:
        assert (
            pixmap.toImage().pixelColor(15, 15).name()
            == accent_colors(status_tone(text), dark)["background"]
        )
    table.close()


@pytest.mark.parametrize("dark", [True, False])
def test_neutral_row_renderer_preserves_legacy_data_colors(qt, dark):
    from workspace_accents import WorkspaceRowDelegate

    table = QTableWidget(1, 1)
    item = QTableWidgetItem("PACIENTE FICTICIO")
    item.setBackground(QBrush(QColor("#875a22")))
    table.setItem(0, 0, item)
    delegate = WorkspaceRowDelegate(table)
    delegate.dark = dark
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 250, 42)
    option.widget = table if dark else None
    styled = delegate.styled_option(option, table.model().index(0, 0))
    assert styled.backgroundBrush.style() == Qt.NoBrush
    pixmap = QPixmap(250, 42)
    pixmap.fill(QColor("white"))
    painter = QPainter(pixmap)
    delegate.paint(painter, option, table.model().index(0, 0))
    painter.end()
    assert item.background().color().name() == "#875a22"
    assert item.text() == "PACIENTE FICTICIO"
    table.close()


@pytest.mark.parametrize("dark", [True, False])
def test_metric_card_paints_long_numbers_without_changing_original_label(qt, dark):
    from workspace_accents import MetricCard, apply_metric_theme
    from workspace_design import metric_row

    label = QLabel("RD$ 123,456,789,012.34\nTotal del envío")
    original = label.text()
    metrics = metric_row([label])
    metrics.resize(280, 90)
    apply_metric_theme(metrics, dark)
    metrics.show()
    qt.processEvents()
    card = metrics.findChild(MetricCard)
    assert card.testAttribute(Qt.WA_StyledBackground)
    assert not card.painter.eventFilter(label, QEvent(QEvent.User))
    assert not metrics.grab().isNull()
    assert not label.grab().isNull()
    assert label.text() == original
    assert card.painter.dark is dark
    metrics.close()


def test_original_main_window_color_pass_keeps_new_styles(billing):
    from billing_workspace_design import apply_billing_action_accents

    for name in ("btn_ars_mgmt", "btn_remove_from_cart", "btn_validate_admission"):
        setattr(billing, name, QPushButton())
    billing.btn_admin_catalog = None
    arrow = QToolButton(billing.catalog_panel)
    arrow.setObjectName("SpinArrowBtn")
    app.MainWindow.apply_button_colors(billing)
    assert "qlineargradient" in billing.btn_add.styleSheet()
    assert "qlineargradient" in billing.btn_add_catalog_item.styleSheet()
    assert arrow.property("accentTone") == "blue"
    controller = billing.catalog_workspace
    del billing.catalog_workspace
    apply_billing_action_accents(billing, False)
    billing.catalog_workspace = controller
    assert billing.btn_add.property("accentTone") == "green"


def test_history_and_monthly_theme_badges_keep_permissions(history, monthly):
    history.btn_authorization.setEnabled(False)
    history.apply_history_theme(False)
    assert not history.btn_authorization.isEnabled()
    assert history.history_workspace.status_delegate.dark is False
    assert history.history_workspace.row_delegate.dark is False
    monthly.apply_monthly_theme(False)
    assert monthly.monthly_workspace.status_delegate.dark is False
    assert monthly.monthly_workspace.batch_status_delegate.dark is False


def test_history_filter_rebuild_retains_search_and_advanced_controls(history):
    history.search_edit.setText("FICTICIO")
    history.history_workspace.rebuild_filters()
    assert history.search_edit.text() == "FICTICIO"
    assert history.document_edit.parentWidget() is history.history_workspace.advanced
