"""Monthly list overview, local status filters and selected-patient details."""

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTableWidgetItem,
    QToolButton,
    QHeaderView,
)

from workspace_design import (
    DetailPanel,
    DetailResponsive,
    metric_row,
    pill,
    workspace_styles,
)
from workspace_selection import RowSelectionChecks
from workspace_accents import (
    StatusBadgeDelegate,
    apply_metric_theme,
    style_action,
    style_status_label,
    table_styles,
)


def monthly_row_state(receipt, problems):
    try:
        date.fromisoformat(str(receipt.get("service_date_snapshot") or ""))
    except ValueError:
        return "errors"
    if float(receipt.get("total_snapshot") or 0) <= 0:
        return "errors"
    return "review" if problems else "ready"


def row_matches(table, row, needle, requested_state):
    cells = [table.item(row, column) for column in range(10)]
    searchable = " ".join(item.text() for item in cells if item).casefold()
    state = str(cells[0].data(Qt.UserRole) or "review") if cells[0] else "review"
    states = {"ready": {"ready"}, "errors": {"errors"}, "review": {"review", "errors"}}
    matches = requested_state == "all" or state in states.get(requested_state, set())
    return needle in searchable and matches


class MonthlyWorkspace:
    def __init__(self, page):
        self.page = page
        page.setFont(QFont("Segoe UI", 9))
        self.state = "all"
        self.counts = {key: QLabel() for key in ("all", "ready", "review", "total")}
        self.metrics = metric_row(
            self.counts.values(), ("blue", "green", "amber", "purple")
        )
        page.layout().insertWidget(1, self.metrics)
        content = page.steps.widget(1)
        layout = content.layout()
        filters = QHBoxLayout()
        self.buttons = {}
        for key, caption in (
            ("all", "Todos"),
            ("review", "Por revisar"),
            ("ready", "Listos"),
            ("errors", "Con errores"),
        ):
            button = pill(
                caption, lambda _checked=False, value=key: self.select_state(value)
            )
            self.buttons[key] = button
            filters.addWidget(button)
        filters.addStretch(1)
        layout.insertLayout(1, filters)
        self.details = DetailPanel(
            "Detalles del paciente",
            (
                ("document", "NSS / cédula"),
                ("date", "Fecha de servicio"),
                ("authorization", "Autorización"),
                ("specialty", "Especialidad"),
                ("amount", "Valor"),
                ("state", "Estado en el envío"),
            ),
            (("edit", "Corregir datos", page.edit_patient_button.click),),
        )
        index = layout.indexOf(page.patients)
        layout.removeWidget(page.patients)
        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)
        split.addWidget(page.patients)
        split.addWidget(self.details)
        split.setStretchFactor(0, 1)
        layout.insertWidget(index, split, 1)
        self.responsive = DetailResponsive(content, self.details, minimum=900)
        page.patients.itemSelectionChanged.connect(self.update_details)
        page.patients.setColumnCount(11)
        page.patients.setHorizontalHeaderItem(10, QTableWidgetItem("···"))
        page.patients.setColumnWidth(10, 40)
        for column, width in {
            1: 78,
            2: 68,
            3: 78,
            4: 105,
            6: 105,
            7: 105,
            8: 130,
            9: 105,
        }.items():
            page.patients.setColumnWidth(column, width)
        page.patients.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.selection = RowSelectionChecks(page.patients, 11)
        self.status_delegate = StatusBadgeDelegate(page.patients)
        page.patients.setItemDelegateForColumn(1, self.status_delegate)
        self.batch_status_delegate = StatusBadgeDelegate(page.batches)
        page.batches.setItemDelegateForColumn(4, self.batch_status_delegate)
        page.patients.setColumnWidth(1, 100)
        page.patients.verticalHeader().setDefaultSectionSize(36)
        page.batches.verticalHeader().setDefaultSectionSize(36)
        self.select_state("all")

    def select_state(self, state):
        self.state = state
        for key, button in self.buttons.items():
            button.setChecked(key == state)
        self.filter_rows(self.page.patient_search.text())

    def filter_rows(self, text):
        needle = str(text or "").casefold()
        for row in range(self.page.patients.rowCount()):
            visible = row_matches(self.page.patients, row, needle, self.state)
            self.page.patients.setRowHidden(row, not visible)
        self.page._update_patient_actions()
        self.update_details()

    def filled(self, receipts):
        counts = {"ready": 0, "review": 0, "errors": 0}
        for row, receipt in enumerate(receipts):
            self.selection.add_row(row)
            state = monthly_row_state(receipt, self.page._receipt_problems(receipt))
            counts[state] += 1
            self.page.patients.item(row, 0).setData(Qt.UserRole, state)
            more = QToolButton()
            more.setText("···")
            more.clicked.connect(lambda _checked=False, index=row: self.row_menu(index))
            self.page.patients.setCellWidget(row, 10, more)
        total = sum(float(item.get("total_snapshot") or 0) for item in receipts)
        self.counts["all"].setText(f"{len(receipts)}\nTotal pacientes")
        self.counts["ready"].setText(f"{counts['ready']}\nListos")
        self.counts["review"].setText(
            f"{counts['review'] + counts['errors']}\nPor revisar"
        )
        self.counts["total"].setText(f"RD$ {total:,.2f}\nTotal del envío")
        for key, button in self.buttons.items():
            count = (
                len(receipts)
                if key == "all"
                else (
                    counts["review"] + counts["errors"]
                    if key == "review"
                    else counts[key]
                )
            )
            captions = {
                "all": "Todos",
                "ready": "Listos",
                "review": "Por revisar",
                "errors": "Con errores",
            }
            button.setText(f"{captions[key]} ({count})")
        self.filter_rows(self.page.patient_search.text())

    def row_menu(self, row):
        self.page.patients.selectRow(row)
        rect = self.page.patients.visualRect(self.page.patients.model().index(row, 10))
        self.page._show_patients_context_menu(rect.center())

    def update_details(self):
        entry = self.page._selected_receipt() or {}
        self.details.show_values(
            entry.get("patient_snapshot") or entry.get("nombre"),
            {
                "document": entry.get("document_number_snapshot"),
                "date": entry.get("service_date_snapshot"),
                "authorization": entry.get("authorization_snapshot"),
                "specialty": entry.get("specialty_snapshot"),
                "amount": f"RD$ {float(entry.get('total_snapshot') or 0):,.2f}"
                if entry
                else "",
                "state": "LISTO"
                if entry
                and monthly_row_state(entry, self.page._receipt_problems(entry))
                == "ready"
                else "POR REVISAR",
            },
        )
        self.details.buttons["edit"].setEnabled(
            self.page.edit_patient_button.isEnabled()
        )
        style_status_label(
            self.details.fields["state"],
            getattr(self.page, "_monthly_design_dark", True),
        )


def apply_monthly_design_theme(page, dark):
    page._monthly_design_dark = bool(dark)
    page.setStyleSheet(page.styleSheet() + workspace_styles(dark))
    controller = page.monthly_workspace
    apply_metric_theme(controller.metrics, dark)
    for key, button in controller.buttons.items():
        tone = {"all": "blue", "ready": "green", "review": "amber", "errors": "red"}[
            key
        ]
        style_action(button, tone, dark, filled=False, compact=True)
    for button, tone in (
        (page.create_button, "blue"),
        (page.edit_batch_button, "blue"),
        (page.delete_batch_button, "red"),
        (page.refresh_button, "blue"),
        (page.search_candidates_button, "blue"),
        (page.edit_patient_button, "blue"),
        (page.add_receipt_button, "green"),
        (page.add_all_receipts_button, "green"),
        (page.remove_receipt_button, "red"),
        (page.export_button, "blue"),
        (controller.details.buttons["edit"], "blue"),
    ):
        style_action(button, tone, dark, compact=True)
    for table, delegate in (
        (page.patients, controller.status_delegate),
        (page.batches, controller.batch_status_delegate),
    ):
        table.setStyleSheet(table_styles(dark))
        delegate.dark = bool(dark)
        table.viewport().update()
    style_status_label(controller.details.fields["state"], dark)
