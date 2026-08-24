from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Mapping

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


def _base_style(is_dark=False):
    dark = bool(is_dark)
    bg = "#111827" if dark else "#f4f7fb"
    card = "#182235" if dark else "#ffffff"
    text = "#e8eef8" if dark else "#14213d"
    secondary = "#b8c6da" if dark else "#365574"
    border = "#34445e" if dark else "#cbd7e6"
    header = "#24324a" if dark else "#eef3f9"
    selected = "#25446f" if dark else "#dbeafe"
    return f"""
        QDialog {{ background: {bg}; color: {text}; }}
        QLabel {{ color: {text}; }}
        QLabel#DialogTitle {{ font-size: 22px; font-weight: 700; color: {text}; }}
        QLineEdit, QComboBox, QDateEdit, QSpinBox {{ min-height: 34px; padding: 0 9px; border: 1px solid {border}; border-radius: 7px; background: {card}; color: {text}; }}
        QPushButton {{ min-height: 34px; padding: 0 14px; border: 1px solid {border}; border-radius: 7px; background: {card}; color: {text}; }}
        QPushButton:hover {{ border-color: #2474e5; }}
        QTableWidget {{ background: {card}; color: {text}; border: 1px solid {border}; gridline-color: {border}; selection-background-color: {selected}; selection-color: {text}; alternate-background-color: {bg}; }}
        QHeaderView::section {{ background: {header}; color: {text}; font-weight: 700; padding: 7px; border: none; border-bottom: 1px solid {border}; }}
        QTabWidget::pane {{ border: 1px solid {border}; background: {card}; }}
        QTabBar::tab {{ min-height: 34px; padding: 0 18px; color: {secondary}; background: {bg}; }}
        QTabBar::tab:selected {{ color: #4f9cff; border-bottom: 2px solid #2474e5; font-weight: 700; background: {card}; }}
    """


class AdmissionReportDialog(QDialog):
    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.summary = None
        self.setWindowTitle("Reporte estadístico de Admisión")
        self.resize(980, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        title = QLabel("Reporte estadístico")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        filters = QHBoxLayout()
        self.period_combo = QComboBox()
        self.period_combo.addItems(("Diario", "Semanal", "Mensual", "Anual", "Personalizado"))
        today = QDate.currentDate()
        self.start_date = QDateEdit(today)
        self.end_date = QDateEdit(today)
        for editor in (self.start_date, self.end_date):
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("dd-MM-yyyy")
        self.generate_button = QPushButton("Generar reporte")
        filters.addWidget(QLabel("Período:"))
        filters.addWidget(self.period_combo)
        filters.addWidget(QLabel("Desde:"))
        filters.addWidget(self.start_date)
        filters.addWidget(QLabel("Hasta:"))
        filters.addWidget(self.end_date)
        filters.addWidget(self.generate_button)
        root.addLayout(filters)
        self.status = QLabel("Seleccione el período y genere el reporte.")
        root.addWidget(self.status)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(("Sección", "Concepto", "Cantidad"))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        root.addWidget(self.table, 1)
        actions = QHBoxLayout()
        self.pdf_button = QPushButton("Crear y abrir PDF")
        self.excel_button = QPushButton("Exportar Excel")
        close = QPushButton("Cerrar")
        self.pdf_button.setEnabled(False)
        self.excel_button.setEnabled(False)
        actions.addWidget(self.pdf_button)
        actions.addWidget(self.excel_button)
        actions.addStretch(1)
        actions.addWidget(close)
        root.addLayout(actions)
        self.generate_button.clicked.connect(self.generate)
        self.pdf_button.clicked.connect(lambda: self._export("pdf"))
        self.excel_button.clicked.connect(lambda: self._export("excel"))
        self.period_combo.currentTextChanged.connect(self._period_changed)
        close.clicked.connect(self.close)
        self.apply_theme(False)
        self._period_changed(self.period_combo.currentText())

    def apply_theme(self, is_dark: bool):
        self.setStyleSheet(_base_style(is_dark))

    def _period_changed(self, period):
        base = self.start_date.date().toPython()
        if period == "Diario":
            end = base
        elif period == "Semanal":
            base = base - timedelta(days=base.weekday())
            end = base + timedelta(days=6)
        elif period == "Mensual":
            base = base.replace(day=1)
            next_month = date(base.year + (base.month == 12), 1 if base.month == 12 else base.month + 1, 1)
            end = next_month - timedelta(days=1)
        elif period == "Anual":
            base, end = date(base.year, 1, 1), date(base.year, 12, 31)
        else:
            return
        self.start_date.setDate(QDate(base.year, base.month, base.day))
        self.end_date.setDate(QDate(end.year, end.month, end.day))

    def _range(self):
        start_date = self.start_date.date().toPython()
        end_date = self.end_date.date().toPython()
        if end_date < start_date:
            start_date, end_date = end_date, start_date
        start = datetime.combine(start_date, time(0, 0))
        end = datetime.combine(end_date + timedelta(days=1), time(0, 0))
        label = f"{start_date:%d/%m/%Y} a {end_date:%d/%m/%Y}"
        return start, end, label

    def generate(self):
        self.generate_button.setEnabled(False)
        self.status.setText("Cargando datos…")
        try:
            start, end, label = self._range()
            self.summary = self.repository.report_summary(start, end, label)
            self._show_summary(self.summary)
            self.status.setText(
                f"Reporte listo · {int(self.summary.get('total_general', 0) or 0)} atención(es)."
            )
            self.pdf_button.setEnabled(True)
            self.excel_button.setEnabled(True)
        except Exception as exc:
            self.summary = None
            self.status.setText("No fue posible cargar el reporte.")
            self.status.setToolTip(str(exc))
            self.pdf_button.setEnabled(False)
            self.excel_button.setEnabled(False)
        finally:
            self.generate_button.setEnabled(True)

    def _show_summary(self, summary: Mapping):
        values = [
            ("Resumen general", "Emergencias", summary.get("total_general", 0)),
            ("Resumen general", "Sin seguro", summary.get("cantidad_sin_seguro", 0)),
            ("Resumen general", "Urgencias", summary.get("cantidad_urgencias", 0)),
            ("Resumen general", "Consultas", summary.get("cantidad_consultas", 0)),
        ]
        values.extend(("Por ARS", name, count) for name, count in summary.get("por_seguro", []))
        values.extend(("Por especialidad", name, count) for name, count in summary.get("por_especialidad", []))
        self.table.setRowCount(0)
        for section, concept, count in values:
            row = self.table.rowCount()
            self.table.insertRow(row)
            for column, value in enumerate((section, concept, count)):
                self.table.setItem(row, column, QTableWidgetItem(str(value)))

    def _export(self, kind):
        if not self.summary:
            return
        try:
            path = (
                self.repository.generate_report_pdf(self.summary)
                if kind == "pdf"
                else self.repository.generate_report_excel(self.summary)
            )
            if not path or not Path(path).is_file():
                raise RuntimeError("El documento no fue generado.")
            self.repository.open_document(path)
        except Exception as exc:
            QMessageBox.warning(self, "Reporte", str(exc))


class AdmissionPreferencesDialog(QDialog):
    def __init__(self, repository, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.values = dict(repository.load_preferences() or {})
        self.setWindowTitle("Preferencias de Admisión")
        self.resize(700, 590)
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        title = QLabel("Preferencias de Admisión")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        form = QFormLayout()
        self.auto_print = QCheckBox("Permitir impresión operativa de hoja")
        self.auto_print.setChecked(bool(self.values.get("print_auto_hoja", True)))
        self.print_behavior = QComboBox()
        self.print_behavior.addItems(("Solo imprimir", "Imprimir y abrir PDF"))
        self.print_behavior.setCurrentText(str(self.values.get("print_behavior_hoja", "Imprimir y abrir PDF")))
        self.copies = QSpinBox()
        self.copies.setRange(1, 9)
        self.copies.setValue(int(self.values.get("print_copies_hoja", 1) or 1))
        self.dark_theme = QCheckBox("Usar tema oscuro")
        self.dark_theme.setChecked(str(self.values.get("theme", "dark")).lower() == "dark")
        self.compact = QCheckBox("Diseño compacto")
        self.compact.setChecked(bool(self.values.get("compact_mode", False)))
        self.history_rows = QSpinBox()
        self.history_rows.setRange(25, 250)
        self.history_rows.setValue(int(self.values.get("history_page_size", 100) or 100))
        form.addRow(self.auto_print)
        form.addRow("Comportamiento:", self.print_behavior)
        form.addRow("Copias de la hoja:", self.copies)
        form.addRow(self.dark_theme)
        form.addRow(self.compact)
        form.addRow("Filas del historial:", self.history_rows)
        root.addLayout(form)
        root.addStretch(1)
        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Save)
        buttons.button(QDialogButtonBox.Save).setText("Guardar cambios")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.apply_theme(False)

    def apply_theme(self, is_dark: bool):
        self.setStyleSheet(_base_style(is_dark))

    def save(self):
        self.values.update(
            {
                "print_auto_hoja": self.auto_print.isChecked(),
                "print_behavior_hoja": self.print_behavior.currentText(),
                "print_copies_hoja": self.copies.value(),
                "theme": "dark" if self.dark_theme.isChecked() else "light",
                "compact_mode": self.compact.isChecked(),
                "history_page_size": self.history_rows.value(),
            }
        )
        try:
            self.repository.save_preferences(self.values)
            self.accept()
        except Exception as exc:
            QMessageBox.warning(self, "Preferencias", str(exc))


class AdmissionInternalConfigDialog(QDialog):
    """Vista administrativa nativa; las mutaciones requieren el autorizador principal."""

    def __init__(self, repository, context, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.context = context
        self.setWindowTitle("Configuración interna de Admisión")
        self.resize(1120, 720)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        title = QLabel("Configuración interna")
        title.setObjectName("DialogTitle")
        root.addWidget(title)
        note = QLabel(
            "ARS, representantes, revisiones NSS y respaldos. Las acciones destructivas "
            "permanecen bloqueadas si el contexto principal no aporta autorización administrativa."
        )
        note.setWordWrap(True)
        root.addWidget(note)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.close)
        root.addWidget(buttons)
        self.apply_theme(False)
        self.reload()

    def apply_theme(self, is_dark: bool):
        self.setStyleSheet(_base_style(is_dark))

    def reload(self):
        snapshot = self.repository.configuration_snapshot()
        self.tabs.clear()
        self.tabs.addTab(self._table(("ARS", "Cantidad"), snapshot.get("ars", [])), "Administrar ARS")
        self.tabs.addTab(self._table(("Representante",), snapshot.get("representatives", [])), "Representantes")
        reviews = [
            (row.get("id", ""), row.get("nss_normalizado", ""), row.get("estado", ""))
            for row in snapshot.get("nss_reviews", [])
        ]
        self.tabs.addTab(self._table(("ID", "NSS", "Estado"), reviews), "Revisión NSS")
        backups = [
            (row.get("created_at", ""), row.get("reason", ""), row.get("status", ""))
            for row in snapshot.get("backups", [])
        ]
        self.tabs.addTab(self._table(("Fecha", "Motivo", "Estado"), backups), "Respaldos")

    @staticmethod
    def _table(headers, rows):
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        for raw in rows:
            if isinstance(raw, Mapping):
                values = list(raw.values())[: len(headers)]
            elif isinstance(raw, (tuple, list)):
                values = list(raw)
            else:
                values = [raw]
            index = table.rowCount()
            table.insertRow(index)
            for column, value in enumerate(values[: len(headers)]):
                table.setItem(index, column, QTableWidgetItem(str(value or "")))
        return table
