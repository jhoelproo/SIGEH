"""Patient follow-up and like-for-like shift comparisons in Admission reports."""

from dataclasses import replace

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from admin_workspace_design import admin_label, admin_table
from admission_contract import normalize_key
from admission_report_favorites import ReportFavoriteStore
from admission_report_tools import comparison_rows, followup_rows, followup_summary
from admission_statistical_reports import build_admission_report_dataset
from admission_turn_history import historical_turn_period, turn_cursor
from admission_turn_history_dialog import AdmissionTurnHistoryDialog


class ReportViewsMenu(QMenu):
    def __init__(self, controller, capture, apply, parent=None):
        super().__init__(parent)
        self.controller, self.capture, self.apply = controller, capture, apply
        self.store = ReportFavoriteStore(controller.session_context.username)
        self.aboutToShow.connect(self.populate)

    def populate(self):
        self.clear()
        self.addAction("Guardar vista actual…", self.save_view)
        self.addSeparator()
        try:
            views = self.store.list()
        except (ValueError, OSError) as exc:
            self.addAction(str(exc)).setEnabled(False)
            return
        remove = self.addMenu("Eliminar vista")
        remove.setEnabled(bool(views))
        for view in views:
            self.addAction(view["name"], lambda view=view: self.apply(view))
            remove.addAction(
                view["name"], lambda name=view["name"]: self.remove_view(name)
            )
        self.addSeparator()
        self.addAction("Vistas de este usuario en este equipo").setEnabled(False)

    def save_view(self):
        name, accepted = QInputDialog.getText(
            self.parentWidget(), "Guardar vista", "Nombre de la vista"
        )
        if not accepted:
            return
        try:
            if name.strip() in {view["name"] for view in self.store.list()}:
                answer = QMessageBox.question(
                    self.parentWidget(),
                    "Reemplazar vista",
                    "Ya existe una vista con ese nombre. ¿Quieres reemplazarla?",
                )
                if answer != QMessageBox.Yes:
                    return
            self.store.save(dict(self.capture(), name=name))
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self.parentWidget(), "Guardar vista", str(exc))

    def remove_view(self, name):
        if (
            QMessageBox.question(
                self.parentWidget(), "Eliminar vista", f"¿Eliminar la vista «{name}»?"
            )
            == QMessageBox.Yes
        ):
            try:
                self.store.remove(name)
            except (ValueError, OSError) as exc:
                QMessageBox.warning(self.parentWidget(), "Eliminar vista", str(exc))


class AdmissionReportPanels(QTabWidget):
    def __init__(self, controller, summary, updated, parent):
        super().__init__(parent)
        self.controller, self.updated = controller, updated
        self.dataset = None
        self.source_records = None
        self.evidence = None
        self.reference_source = None
        self.rows, self.visible_rows = [], []
        self.generation = 0
        self.billing_loading = False
        self.addTab(summary, "Resumen")
        self._build_patients()
        self._build_comparison()
        self.currentChanged.connect(self._tab_changed)
        parent.closing.connect(self.invalidate)
        for button in self.findChildren(QPushButton):
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(30)
            button.setAutoDefault(False)
        self.tabBar().setCursor(Qt.PointingHandCursor)

    def _build_patients(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("AdminSearch")
        self.search.setPlaceholderText("Buscar paciente, NSS, cédula o ARS…")
        self.search.setAccessibleName("Buscar en los pacientes del reporte")
        self.search.setClearButtonEnabled(True)
        self.state = QComboBox()
        self.state.addItems(
            [
                "Todos",
                "Facturado",
                "Recibo pendiente",
                "Sin recibo",
                "No facturado",
                "Sin confirmar",
                "Por corregir",
            ]
        )
        self.state.setAccessibleName("Estado del paciente")
        self.refresh = QPushButton("Actualizar estados")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.state)
        filters.addWidget(self.refresh)
        layout.addLayout(filters)
        self.patient_status = admin_label(
            "Genera un reporte para consultar sus pacientes."
        )
        layout.addWidget(self.patient_status)
        self.patients = self._table(
            [
                "Paciente",
                "Facturación",
                "ARS",
                "Datos por corregir",
                "Especialidad",
                "Fecha",
                "Identificación",
                "Recibos",
            ]
        )
        admin_table(
            self.patients,
            {1: 140, 2: 145, 3: 220, 4: 120, 5: 100, 6: 130, 7: 95},
            stretch=0,
        )
        self.patients.setAccessibleName("Pacientes y facturación del reporte")
        layout.addWidget(self.patients, 1)
        actions = QHBoxLayout()
        self.edit = QPushButton("Corregir atención")
        self.receipt = QPushButton("Abrir recibo")
        self.selection_note = admin_label("Selecciona una atención.")
        actions.addWidget(self.edit)
        actions.addWidget(self.receipt)
        actions.addWidget(self.selection_note, 1)
        layout.addLayout(actions)
        self.addTab(page, "Pacientes y facturación")
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(150)
        self.search.textChanged.connect(lambda: self.search_timer.start())
        self.search_timer.timeout.connect(self.filter_patients)
        self.state.currentTextChanged.connect(self.filter_patients)
        self.refresh.clicked.connect(self.refresh_billing)
        self.patients.itemSelectionChanged.connect(self._selection_changed)
        self.edit.clicked.connect(self.edit_attention)
        self.receipt.clicked.connect(self.open_receipt)
        self._selection_changed()

    def _build_comparison(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        self.choose_comparison = QPushButton("Elegir turno para comparar")
        self.comparison_status = admin_label(
            "Compara dos turnos usando las mismas ARS, especialidad y cobertura."
        )
        row.addWidget(self.choose_comparison)
        row.addWidget(self.comparison_status, 1)
        layout.addLayout(row)
        self.comparison = self._table(
            [
                "Concepto",
                "Este reporte",
                "Turno de referencia",
                "Diferencia",
                "Variación",
            ]
        )
        admin_table(self.comparison, {1: 120, 2: 160, 3: 100, 4: 110}, stretch=0)
        self.comparison.setAccessibleName("Comparación de los turnos")
        layout.addWidget(self.comparison, 1)
        self.addTab(page, "Comparar turnos")
        self.choose_comparison.clicked.connect(self.open_comparison_history)

    @staticmethod
    def _table(headers):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setMinimumHeight(68)
        return table

    def invalidate(self):
        self.generation += 1
        self.dataset = None
        self.billing_loading = False
        self.rows = []
        self.filter_patients()
        self.patient_status.setText(
            "Genera el reporte para aplicar los filtros actuales."
        )
        self.refresh.setEnabled(False)
        self.choose_comparison.setEnabled(False)
        self.comparison_status.setText(
            "Genera el reporte para aplicar los filtros actuales."
        )
        self.comparison.setRowCount(0)

    def set_dataset(self, dataset, source_records):
        self.generation += 1
        self.dataset = dataset
        if self.source_records is not source_records:
            self.evidence = None
        self.source_records = source_records
        self.choose_comparison.setEnabled(True)
        self.refresh.setEnabled(True)
        self._render_comparison()
        self._tab_changed(self.currentIndex())

    def _tab_changed(self, index):
        if index == 1 and self.dataset is not None:
            self.load_followup()

    def refresh_billing(self):
        self.evidence = None
        self.load_followup()

    def load_followup(self):
        if self.dataset is None or self.billing_loading:
            return
        if not self.dataset.records:
            self.rows = []
            self.filter_patients()
            return
        if self.evidence is not None:
            self.rows = followup_rows(self.dataset.records, self.evidence)
            self.filter_patients()
            return
        token = self.generation
        self.billing_loading = True
        self.refresh.setEnabled(False)
        self.rows = []
        self.filter_patients()
        self.patient_status.setText(
            "Consultando los recibos vinculados de este reporte…"
        )
        records = tuple(self.source_records)

        def loaded(evidence):
            if not isValid(self) or token != self.generation:
                return
            self.billing_loading = False
            self.refresh.setEnabled(True)
            self.evidence = evidence
            self.load_followup()

        def failed(error):
            if not isValid(self) or token != self.generation:
                return
            self.billing_loading = False
            self.refresh.setEnabled(True)
            self.patient_status.setText(
                f"No se pudieron consultar los estados. Pulsa Actualizar estados. {error}"
            )

        self.controller._ejecutar_en_segundo_plano(
            "Consultando facturación del reporte…",
            lambda: self.controller.db.report_billing_followup(records),
            loaded,
            failed,
        )

    def filter_patients(self, *_):
        query, state = normalize_key(self.search.text()), self.state.currentText()
        self.visible_rows = [
            row for row in self.rows if self._patient_matches(row, query, state)
        ]
        self.patients.setRowCount(len(self.visible_rows))
        for index, row in enumerate(self.visible_rows):
            values = (
                row["patient_name"],
                row["billing_state"],
                row["canonical_ars"],
                " ".join(row["issues"]),
                row["specialty"],
                row["service_date"],
                row.get("nss") or row.get("cedula") or "",
                ", ".join(str(receipt["numero"]) for receipt in row["receipts"]),
            )
            self._fill_row(self.patients, index, values)
        self.patients.clearSelection()
        self._selection_changed()
        if self.dataset is not None:
            summary = followup_summary(self.visible_rows)
            self.patient_status.setText(
                f"{summary['patients']} paciente(s) · {summary['invoiced']} facturados · "
                f"{summary['missing_receipt']} sin recibo · {summary['needs_correction']} por corregir"
            )

    @staticmethod
    def _patient_matches(row, query, state):
        text = " ".join(
            str(row.get(key) or "")
            for key in ("patient_name", "canonical_ars", "nss", "cedula")
        )
        state_matches = (
            state == "Todos"
            or state == row["billing_state"]
            or (state == "Por corregir" and bool(row["issues"]))
        )
        return state_matches and query in normalize_key(text)

    @staticmethod
    def _fill_row(table, index, values):
        for column, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setToolTip(str(value))
            table.setItem(index, column, item)

    def selected_patient(self):
        index = self.patients.currentRow()
        if (
            self.dataset is not None
            and self.patients.selectedItems()
            and 0 <= index < len(self.visible_rows)
        ):
            return self.visible_rows[index]
        return None

    def _selection_changed(self):
        row = self.selected_patient()
        self.edit.setEnabled(
            bool(
                row
                and row.get("global_attention_id")
                and self.controller._puede("records.edit")
            )
        )
        self.receipt.setEnabled(bool(row and row["receipts"]))
        self.selection_note.setText(
            " ".join(row["issues"]) if row else "Selecciona una atención."
        )

    def open_receipt(self):
        from admission_receipt_dialog import choose_linked_receipt

        row = self.selected_patient()
        if not row or not row["receipts"]:
            return
        if len(row["receipts"]) == 1:
            self.controller.db.open_report_receipt(row["receipts"][0]["id"])
        else:
            choose_linked_receipt(
                row["receipts"], self.controller.db.open_report_receipt
            )

    def edit_attention(self):
        row = self.selected_patient()
        if not row or not self.controller._exigir_permiso(
            "records.edit", "corregir esta atención"
        ):
            return
        token, global_id = self.generation, row.get("global_attention_id")
        if not global_id:
            return
        self.edit.setEnabled(False)

        def loaded(attention):
            if not isValid(self) or token != self.generation:
                return
            self._selection_changed()
            if not self.controller._exigir_permiso(
                "records.edit", "corregir esta atención"
            ):
                return
            if not attention or attention.get("is_deleted"):
                QMessageBox.information(
                    self,
                    "Corregir atención",
                    "La atención ya no está disponible. Actualiza el reporte.",
                )
                return
            self.controller._abrir_editor_atencion(
                int(attention["id"]), on_saved=self.updated
            )

        def failed(error):
            if isValid(self) and token == self.generation:
                self._selection_changed()
                QMessageBox.warning(self, "Corregir atención", str(error))

        self.controller._ejecutar_en_segundo_plano(
            "Preparando la atención seleccionada…",
            lambda: self.controller.db.get_attention_by_global_id(
                global_id, force_central=True, include_deleted=False
            ),
            loaded,
            failed,
        )

    def open_comparison_history(self):
        if self.dataset is None:
            return
        dialog = AdmissionTurnHistoryDialog(
            self.controller, self.select_reference, self
        )
        dialog.setWindowTitle("Elegir turno de referencia")
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.open()
        dialog.user.setFocus()

    def select_reference(self, turn):
        if self.dataset is None:
            return
        current = self.dataset.turns
        if (
            current
            and all(
                key in current[0]
                for key in ("started_at", "username", "operational_source_id")
            )
            and turn_cursor(current[0]) == turn_cursor(turn)
        ):
            self.comparison_status.setText("Elige un turno diferente para comparar.")
            return
        token = self.generation
        self.choose_comparison.setEnabled(False)
        self.comparison_status.setText("Cargando el turno de referencia…")

        def loaded(source):
            if not isValid(self) or token != self.generation:
                return
            self.choose_comparison.setEnabled(True)
            self.reference_source = source
            self._render_comparison()

        def failed(error):
            if isValid(self) and token == self.generation:
                self.choose_comparison.setEnabled(True)
                self.comparison_status.setText(
                    f"No se pudo cargar el turno de referencia. {error}"
                )

        self.controller._ejecutar_en_segundo_plano(
            "Cargando turno de referencia…",
            lambda: self._read_reference(turn),
            loaded,
            failed,
        )

    def _read_reference(self, turn):
        source = self.controller.db.load_historical_turn_report(turn)
        historical_turn_period(source["selected_turn"])
        return source

    def _render_comparison(self):
        if self.dataset is None or self.reference_source is None:
            return
        source = self.reference_source
        turn = source["selected_turn"]
        period = historical_turn_period(turn)
        filters = replace(
            self.dataset.filters,
            start_at=period.start_at,
            end_at=period.end_at,
            period_label=period.label,
            turn_label=period.label,
            operational_source_id=turn["operational_source_id"],
            turn_id=turn["turn_id"],
        )
        reference = build_admission_report_dataset(
            source["records"], filters, turns=(turn,)
        )
        rows = comparison_rows(self.dataset, reference)
        self.comparison.setRowCount(len(rows))
        for index, row in enumerate(rows):
            self._fill_row(self.comparison, index, row)
        self.comparison_status.setText(
            f"Referencia · {period.label} · {period.start_at:%d/%m/%Y %H:%M} · Mismos filtros en ambos turnos"
        )
