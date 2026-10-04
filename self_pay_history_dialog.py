"""Separate paginated history for direct payments and exemptions."""

from pathlib import Path
import tempfile
from uuid import uuid4

from PySide6.QtCore import QDate, QThread, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from self_pay_billing import (
    FOREIGN,
    PAYMENT_LABELS,
    UNINSURED,
    coverage_label,
    list_receipts,
    load_summary,
)


class HistoryTask(QThread):
    ready = Signal(object)
    failed = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.ready.emit(self.operation())
        except Exception as exc:
            self.failed.emit(str(exc))


def report_context(summary, filters, actor, logo):
    return {
        "mode": "self_pay",
        "title": "Cobros de extranjeros y no asegurados",
        "subtitle": "Recaudación, pendientes de pago y exoneraciones",
        "generated_by": actor,
        "logo_path": logo,
        "totals": {"_date_basis_label": "Fecha de generación del recibo"},
        "data": {
            "summary": summary,
            "period": f"{filters['start']} al {filters['end']}",
        },
    }


def export_summary(path, summary, filters):
    from openpyxl import Workbook
    from report_engine.excel_exporter import _safe_excel_value

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Cobros directos"
    sheet.append(["Desde", filters["start"], "Hasta", filters["end"]])
    sheet.append(
        [
            "Cobertura",
            "Recibos",
            "Pagados",
            "Recaudado",
            "Exonerados",
            "Valor exonerado",
            "Pendientes de pago",
            "Importe pendiente",
        ]
    )
    for row in summary:
        sheet.append(
            [
                _safe_excel_value(row["label"]),
                row["count"],
                row["paid_count"],
                float(row["collected"]),
                row["exempt_count"],
                float(row["exempt_amount"]),
                row.get("pending_count", 0),
                float(row.get("pending_amount", 0)),
            ]
        )
    for column in "ABCDEFGH":
        sheet.column_dimensions[column].width = 25
    for row in sheet.iter_rows(min_row=3):
        row[3].number_format = row[5].number_format = row[7].number_format = "#,##0.00"
    sheet.freeze_panes = "A3"
    workbook.save(path)


class SelfPayHistoryDialog(QDialog):
    PAGE_SIZE = 100

    def __init__(
        self,
        connection_factory,
        *,
        actor,
        logo,
        open_receipt,
        edit_receipt,
        preview,
        new_receipt=None,
        parent=None,
    ):
        super().__init__(parent)
        self.connection_factory = connection_factory
        self.actor, self.logo = actor, logo
        self.open_receipt, self.edit_receipt, self.preview = (
            open_receipt,
            edit_receipt,
            preview,
        )
        self.offset = 0
        self.rows = []
        self.worker = None
        self.setWindowTitle("Historial de extranjeros y no asegurados")
        self.resize(1100, 650)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Cobros directos · Pagados, pendientes y exonerados"))
        filters = QHBoxLayout()
        self.start, self.end = QDateEdit(), QDateEdit()
        for field, date in (
            (self.start, QDate.currentDate().addDays(-30)),
            (self.end, QDate.currentDate()),
        ):
            field.setDate(date)
            field.setCalendarPopup(True)
            field.setDisplayFormat("dd/MM/yyyy")
        self.coverage = QComboBox()
        self.coverage.addItem("Ambas coberturas", "")
        for code in (FOREIGN, UNINSURED):
            self.coverage.addItem(coverage_label(code), code)
        self.status = QComboBox()
        for text, code in (
            ("Todos los estados", ""),
            ("Pagados", "PAGADO"),
            ("Exonerados", "EXONERADO"),
            ("Pendientes de pago", "PENDIENTE_PAGO"),
        ):
            self.status.addItem(text, code)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Paciente o número de recibo")
        self.query_controls = [
            self.start,
            self.end,
            self.coverage,
            self.status,
            self.search,
        ]
        self.search_button = QPushButton("Buscar")
        for widget in (
            QLabel("Desde"),
            self.start,
            QLabel("Hasta"),
            self.end,
            self.coverage,
            self.status,
            self.search,
            self.search_button,
        ):
            filters.addWidget(widget)
        layout.addLayout(filters)
        self.summary = QLabel("Seleccione filtros y busque los recibos.")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            [
                "Recibo",
                "Paciente",
                "Fecha servicio",
                "Cobertura",
                "Estado",
                "Valor tarifado",
                "Usuario",
            ]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Stretch
        )
        layout.addWidget(self.table)
        self.message = QLabel()
        layout.addWidget(self.message)
        actions = QHBoxLayout()
        self.previous = QPushButton("Anterior")
        self.next = QPushButton("Siguiente")
        self.buttons = [self.search_button, self.previous, self.next]
        for button in (self.previous, self.next):
            actions.addWidget(button)
        if new_receipt is not None:
            for code in (FOREIGN, UNINSURED):
                button = QPushButton(f"Nuevo {coverage_label(code).lower()}")
                button.clicked.connect(
                    lambda checked=False, selected=code: self.start_receipt(
                        new_receipt, selected
                    )
                )
                self.buttons.append(button)
                actions.addWidget(button)
        for text, handler in (
            ("Abrir recibo", self.open_selected),
            ("Editar recibo", self.edit_selected),
            ("Reporte PDF", self.create_report),
            ("Exportar Excel", self.export_report),
            ("Cerrar", self.close),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            self.buttons.append(button)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.search_button.clicked.connect(self.search_receipts)
        self.search.returnPressed.connect(self.search_receipts)
        self.previous.clicked.connect(lambda: self.change_page(-1))
        self.next.clicked.connect(lambda: self.change_page(1))
        self.search.setFocus()

    def filters(self):
        if self.start.date() > self.end.date():
            raise ValueError("La fecha inicial no puede ser posterior a la final.")
        return {
            "start": self.start.date().toString("yyyy-MM-dd"),
            "end": self.end.date().toString("yyyy-MM-dd"),
            "code": self.coverage.currentData(),
            "status": self.status.currentData(),
            "search": self.search.text().strip(),
        }

    def run_task(self, operation, handler):
        if self.worker is not None:
            return
        self.message.setText("Consultando…")
        for button in self.buttons + self.query_controls:
            button.setEnabled(False)
        self.worker = HistoryTask(operation, self)
        self.worker.ready.connect(handler)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.task_finished)
        self.worker.start()

    def task_finished(self):
        if self.worker is None:
            return
        self.worker.deleteLater()
        self.worker = None
        for button in self.buttons + self.query_controls:
            button.setEnabled(True)
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(
            bool(self.rows)
            and self.offset + len(self.rows) < self.rows[0]["matched_count"]
        )

    def show_error(self, message):
        self.message.setText("No se completó la operación. Puede volver a intentarlo.")
        QMessageBox.warning(self, "Cobros directos", message)

    def search_receipts(self):
        self.offset = 0
        self.load_page()

    def change_page(self, direction):
        self.offset = max(0, self.offset + direction * self.PAGE_SIZE)
        self.load_page()

    def load_page(self):
        try:
            filters = self.filters()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        offset = self.offset

        def load():
            with self.connection_factory() as connection:
                connection.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                return list_receipts(
                    connection, offset=offset, **filters
                ), load_summary(connection, **filters)

        self.run_task(load, self.show_page)

    def show_page(self, result):
        self.rows, summary = result
        self.table.setRowCount(len(self.rows))
        for index, row in enumerate(self.rows):
            values = [
                row["numero"],
                row["nombre"],
                row["fecha"],
                coverage_label(row["coverage"]),
                PAYMENT_LABELS.get(row["payment_status"], row["payment_status"]),
                f"RD$ {float(row['total']):,.2f}",
                row["username"],
            ]
            for column, value in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()
        self.summary.setText(
            " · ".join(
                f"{row['label']}: {row['paid_count']} pagados, RD$ {row['collected']} recaudados; {row['exempt_count']} exonerados, RD$ {row['exempt_amount']} exonerados; {row.get('pending_count', 0)} pendientes de pago, RD$ {row.get('pending_amount', '0.00')} adeudados"
                for row in summary
            )
        )
        self.message.setText(
            f"{self.rows[0]['matched_count'] if self.rows else 0} recibos encontrados · Página {self.offset // self.PAGE_SIZE + 1}"
        )

    def selected_id(self):
        index = self.table.currentRow()
        return self.rows[index]["id"] if 0 <= index < len(self.rows) else None

    def open_selected(self):
        identity = self.selected_id()
        if identity is not None:
            self.run_task(lambda: self.open_receipt(identity), self.preview)

    def edit_selected(self):
        identity = self.selected_id()
        if identity is not None:
            self.accept()
            self.edit_receipt(identity)

    def start_receipt(self, callback, code):
        self.accept()
        callback(code)

    def summary_operation(self, export, filters):
        with self.connection_factory() as connection:
            summary = load_summary(connection, **filters)
        return export(summary, filters)

    def create_report(self):
        try:
            filters = self.filters()
        except ValueError as exc:
            self.show_error(str(exc))
            return

        def create(summary, _filters):
            from report_engine import ReportHTMLRenderer

            path = Path(tempfile.gettempdir()) / f"sigeh-cobros-{uuid4().hex}.pdf"
            return ReportHTMLRenderer().render_pdf(
                report_context(summary, filters, self.actor, self.logo), str(path)
            )

        self.run_task(lambda: self.summary_operation(create, filters), self.preview)

    def export_report(self):
        try:
            filters = self.filters()
        except ValueError as exc:
            self.show_error(str(exc))
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar cobros", "Cobros.xlsx", "Excel (*.xlsx)"
        )
        if path:
            self.run_task(
                lambda: self.summary_operation(
                    lambda summary, current_filters: export_summary(
                        path, summary, current_filters
                    ),
                    filters,
                ),
                lambda _: self.message.setText("Excel exportado correctamente."),
            )

    def closeEvent(self, event):
        if self.worker is not None:
            event.ignore()
        else:
            super().closeEvent(event)
