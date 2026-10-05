"""Receipt history layout and actions over its existing paginated backend."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from workspace_design import (
    DetailPanel,
    DetailResponsive,
    metric_row,
    title,
    workspace_styles,
)
from workspace_selection import RowSelectionChecks
from workspace_accents import (
    StatusBadgeDelegate,
    WorkspaceRowDelegate,
    apply_metric_theme,
    style_action,
    style_status_label,
    table_styles,
)


class ReceiptHistoryWorkspace:
    def __init__(self, history, backend):
        self.history = history
        history.setFont(QFont("Segoe UI", 9))
        self.backend = backend
        self.flow = "all"
        self.sort = "recent"
        root = history.layout()
        heading = QWidget()
        contents = QVBoxLayout(heading)
        contents.setContentsMargins(0, 0, 0, 0)
        contents.addWidget(title("Historial de recibos"))
        caption = QLabel("Busca, revisa y gestiona los recibos guardados.")
        caption.setObjectName("DesignCaption")
        contents.addWidget(caption)
        root.insertWidget(0, heading)
        self.rebuild_filters()
        history.ready_summary = QLabel("Listos para auditoría\nCargando…")
        index = root.indexOf(history.metrics_widget)
        root.removeWidget(history.metrics_widget)
        old_metrics = history.metrics_widget
        history.metrics_widget = metric_row(
            (
                history.billing_summary,
                history.ready_summary,
                history.billing_totals_summary,
                history.historical_summary,
            ),
            ("amber", "green", "blue", "purple"),
        )
        old_metrics.hide()
        root.insertWidget(index, history.metrics_widget)
        self.install_metric_actions()
        self.install_table_details()
        self.install_sorting()
        self.compact_action_bar()
        root.setSpacing(5)
        for label in (
            history.billing_summary,
            history.ready_summary,
            history.billing_totals_summary,
            history.historical_summary,
        ):
            label.setStyleSheet("padding:0;font-size:10pt;font-weight:700;")
        history.status_combo.currentIndexChanged.connect(self.manual_status_changed)

    def manual_status_changed(self):
        self.flow = "all"

    def compact_action_bar(self):
        h = self.history
        h.btn_select_visible.hide()
        h.btn_delete_receipt.setText("Mover a papelera")
        select = h.more_actions_menu.addAction("Seleccionar visibles")
        select.triggered.connect(h.table.selectAll)
        remove = h.more_actions_menu.addAction("Mover seleccionados a papelera")
        remove.triggered.connect(h.btn_delete_selected.click)
        h.btn_bulk_invoice.setText("Marcar seleccionados")
        h.btn_authorization.setText("Autorización")

    def rebuild_filters(self):
        h = self.history
        grid = h.search_edit.parentWidget().layout()
        while grid.count():
            widget = grid.takeAt(0).widget()
            if widget:
                widget.hide()
        for column, (caption, control) in enumerate(
            (
                ("Paciente, recibo o identificación", h.search_edit),
                ("ARS", h.ars_combo),
                ("Estado", h.status_combo),
                ("Usuarios", h.user_filter),
            )
        ):
            grid.addWidget(QLabel(caption), 0, column)
            grid.addWidget(control, 1, column)
            control.show()
        grid.setColumnStretch(0, 3)
        for column in (1, 2, 3):
            grid.setColumnStretch(column, 2)
        for column in (4, 5):
            grid.setColumnStretch(column, 0)
        grid.setVerticalSpacing(4)
        grid.setContentsMargins(12, 14, 12, 6)
        grid.addWidget(h.period_filter, 2, 0, 1, 2)
        grid.addWidget(h.assignment_combo, 2, 2)
        h.period_filter.show()
        h.assignment_combo.show()
        actions = QHBoxLayout()
        advanced = QPushButton("Filtros avanzados")
        advanced.setAutoDefault(False)
        actions.addWidget(h.btn_search)
        actions.addWidget(advanced)
        actions.addWidget(h.btn_clear_filters)
        h.btn_search.show()
        h.btn_clear_filters.show()
        grid.addLayout(actions, 2, 3)
        self.advanced = QWidget()
        advanced_layout = QHBoxLayout(self.advanced)
        advanced_layout.addWidget(QLabel("Identificación:"))
        advanced_layout.addWidget(h.document_type_combo)
        advanced_layout.addWidget(h.document_edit, 1)
        h.document_type_combo.show()
        h.document_edit.show()
        grid.addWidget(self.advanced, 3, 0, 1, 4)
        self.advanced.hide()
        advanced.clicked.connect(
            lambda: self.advanced.setVisible(not self.advanced.isVisible())
        )
        grid.addWidget(h.filter_summary, 4, 0, 1, 4)
        h.filter_summary.show()
        h.search_edit.setPlaceholderText("Buscar paciente, recibo, cédula o NSS…")

    def install_metric_actions(self):
        for index, (flow, status) in enumerate(
            (
                ("all", self.backend.BILLING_PENDING),
                ("ready", self.backend.BILLING_ALL),
                ("all", self.backend.BILLING_INVOICED),
                ("all", self.backend.BILLING_UNCLASSIFIED),
            )
        ):
            card = self.history.metrics_widget.layout().itemAt(index).widget()
            button = QPushButton("›")
            button.setToolTip("Ver recibos")
            button.setAutoDefault(False)
            button.clicked.connect(
                lambda _checked=False, f=flow, s=status: self.filter_metric(f, s)
            )
            card.add_action(button)

    def filter_metric(self, flow, status):
        self.flow = flow
        combo = self.history.status_combo
        index = combo.findData(status)
        if index >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)
        self.history.load_rows(reset=True)

    def install_table_details(self):
        h = self.history
        self.details = DetailPanel(
            "Detalle del recibo",
            (
                ("number", "Recibo"),
                ("nss", "NSS"),
                ("ars", "ARS"),
                ("date", "Fecha de servicio"),
                ("authorization", "Autorización"),
                ("total", "Total"),
                ("status", "Estado"),
                ("assigned", "Asignado a"),
            ),
            (
                ("open", "Abrir PDF", h.btn_open_receipt.click),
                ("edit", "Editar recibo", h.btn_edit_receipt.click),
                ("authorization", "Registrar autorización", h.btn_authorization.click),
                ("validate", "Confirmar facturado", h.btn_validate_receipt.click),
            ),
        )
        root = h.layout()
        index = root.indexOf(h.table)
        root.removeWidget(h.table)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(h.table)
        split.addWidget(self.details)
        split.setStretchFactor(0, 1)
        root.insertWidget(index, split, 1)
        self.responsive = DetailResponsive(h, self.details)
        for column in (0, 4, 5, 8, 12, 13, 14, 15, 16, 17, 18):
            h.table.setColumnHidden(column, True)
        h.table.setColumnCount(20)
        h.table.horizontalHeader().setStretchLastSection(False)
        h.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        h.table.setHorizontalHeaderItem(19, QTableWidgetItem("···"))
        h.table.setColumnWidth(19, 42)
        self.selection = RowSelectionChecks(h.table, 20)
        self.status_delegate = StatusBadgeDelegate(h.table)
        self.row_delegate = WorkspaceRowDelegate(h.table)
        h.table.setItemDelegate(self.row_delegate)
        h.table.setItemDelegateForColumn(9, self.status_delegate)
        h.table.setColumnWidth(9, 155)
        h.table.verticalHeader().setDefaultSectionSize(42)
        h.table.itemSelectionChanged.connect(self.update_details)

    def install_sorting(self):
        h = self.history
        controls = QWidget()
        layout = QHBoxLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Recibos guardados"), 1)
        layout.addWidget(QLabel("Ordenar por:"))
        combo = QComboBox()
        combo.addItem("Más recientes", "recent")
        combo.addItem("Fecha de servicio · Más recientes", "service_recent")
        combo.addItem("Fecha de servicio · Más antiguos", "service_oldest")
        combo.addItem("Importe · Mayor primero", "total")
        combo.currentIndexChanged.connect(
            lambda: self.sort_changed(combo.currentData())
        )
        layout.addWidget(combo)
        index = h.layout().indexOf(h.table.parentWidget())
        h.layout().insertWidget(index, controls)

    def sort_changed(self, order):
        self.sort = order
        self.history.load_rows(reset=True)

    def row_added(self, row):
        for column in (0, 4, 5, 8, 12, 13, 14, 15, 16, 17, 18):
            self.history.table.setColumnHidden(column, True)
        self.history.btn_select_visible.hide()
        self.selection.add_row(row)
        more = QToolButton()
        more.setText("···")
        more.clicked.connect(lambda: self.row_menu(row))
        self.history.table.setCellWidget(row, 19, more)

    def row_menu(self, row):
        rect = self.history.table.visualRect(self.history.table.model().index(row, 19))
        self.history.show_context_menu(rect.center())

    def update_details(self):
        h = self.history
        entry = h._selected_receipt() or {}
        self.details.show_values(
            entry.get("nombre"),
            {
                "number": entry.get("numero"),
                "nss": entry.get("admission_nss_snapshot"),
                "ars": entry.get("ars"),
                "date": entry.get("fecha"),
                "authorization": entry.get("numero_autorizacion") or "Pendiente",
                "total": f"RD$ {float(entry.get('total') or 0):,.2f}" if entry else "",
                "status": self.backend.billing_status_label(
                    entry.get("estado_facturacion")
                ),
                "assigned": entry.get("auditoria_asignada_a") or "Sin asignar",
            },
        )
        for key, original in (
            ("open", h.btn_open_receipt),
            ("edit", h.btn_edit_receipt),
            ("authorization", h.btn_authorization),
            ("validate", h.btn_validate_receipt),
        ):
            self.details.buttons[key].setEnabled(bool(entry) and original.isEnabled())
        style_status_label(
            self.details.fields["status"], getattr(h, "_history_design_dark", True)
        )

    def metrics_loaded(self, payload):
        if (payload.get("_errors") or {}).get("metrics"):
            self.history.ready_summary.setText("Listos para auditoría\nNo disponible")
            return
        queue = payload.get("queue") or {}
        self.history.ready_summary.setText(
            f"{int(queue.get('ready') or 0):,}\nListos para auditoría"
        )


def apply_history_design_theme(history, dark):
    history._history_design_dark = bool(dark)
    history.setStyleSheet(history.styleSheet() + workspace_styles(dark))
    controller = history.history_workspace
    apply_metric_theme(history.metrics_widget, dark)
    history.table.setStyleSheet(table_styles(dark))
    controller.status_delegate.dark = bool(dark)
    controller.row_delegate.dark = bool(dark)
    history.table.viewport().update()
    for button, tone in (
        (history.btn_open_receipt, "blue"),
        (history.btn_edit_receipt, "blue"),
        (history.btn_authorization, "blue"),
        (history.btn_validate_receipt, "green"),
        (history.btn_bulk_invoice, "green"),
        (history.btn_delete_receipt, "red"),
        (history.btn_search, "blue"),
        (history.btn_clear_filters, "blue"),
        (history.btn_trash, "amber"),
    ):
        style_action(button, tone, dark, compact=True)
    for key, button in controller.details.buttons.items():
        style_action(
            button, "green" if key == "validate" else "blue", dark, compact=True
        )
    for card in history.metrics_widget.findChildren(QWidget, "DesignMetricCard"):
        for button in card.findChildren(QPushButton):
            style_action(
                button, card.property("accentTone"), dark, filled=False, compact=True
            )
    style_status_label(controller.details.fields["status"], dark)
