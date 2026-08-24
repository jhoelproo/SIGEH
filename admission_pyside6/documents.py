from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class AdmissionDocumentError(RuntimeError):
    pass


@dataclass(slots=True)
class DocumentResult:
    ok: bool
    attention_id: int
    path: str = ""
    action: str = ""
    message: str = ""


class AdmissionDocumentService(QObject):
    """Reconstruye hojas temporales; nunca las usa como fuente permanente."""

    operation_completed = Signal(object)
    operation_failed = Signal(str)

    def __init__(self, repository, configuration=None, parent=None):
        super().__init__(parent)
        self.repository = repository
        self.configuration = dict(configuration or {})

    def generate(self, attention_id: int) -> DocumentResult:
        path = self.repository.generate_detail_sheet(int(attention_id))
        if not path or not Path(path).is_file():
            raise AdmissionDocumentError("El generador no produjo una hoja válida.")
        return DocumentResult(True, int(attention_id), path=path, action="GENERATE")

    def open(self, attention_id: int) -> DocumentResult:
        try:
            result = self.generate(attention_id)
            if not self.repository.open_document(result.path):
                raise AdmissionDocumentError("No fue posible abrir la hoja temporal.")
            self.repository.schedule_document_cleanup(result.path, delay=900)
            result.action = "OPEN"
            self.operation_completed.emit(result)
            return result
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return DocumentResult(False, int(attention_id), action="OPEN", message=str(exc))

    def preview(self, attention_id: int) -> DocumentResult:
        result = self.open(attention_id)
        result.action = "PREVIEW"
        return result

    def print(self, attention_id: int, *, copies: int | None = None) -> DocumentResult:
        copies = max(1, int(copies or self.configuration.get("print_copies_hoja", 1) or 1))
        path = ""
        try:
            self.repository.update_print_state(attention_id, "PENDIENTE")
            generated = self.generate(attention_id)
            path = generated.path
            self.repository.update_print_state(attention_id, "PROCESANDO")
            if not self.repository.print_document(path, copies=copies):
                raise AdmissionDocumentError("No fue posible enviar la hoja a la impresora.")
            self.repository.update_print_state(
                attention_id, "ENVIADO_A_IMPRESORA", increment_attempt=True
            )
            self.repository.schedule_document_cleanup(path, delay=90)
            result = DocumentResult(True, int(attention_id), path=path, action="PRINT")
            self.operation_completed.emit(result)
            return result
        except Exception as exc:
            try:
                self.repository.update_print_state(
                    attention_id, "FALLIDO", error=str(exc), increment_attempt=True
                )
            except Exception:
                pass
            if path:
                try:
                    self.repository.schedule_document_cleanup(path, delay=90)
                except Exception:
                    pass
            self.operation_failed.emit(str(exc))
            return DocumentResult(False, int(attention_id), path=path, action="PRINT", message=str(exc))

    def register_output(self, attention_id: int) -> DocumentResult:
        if not bool(self.configuration.get("print_auto_hoja", True)):
            return self.open(attention_id) if bool(self.configuration.get("open_sheet_after_register")) else DocumentResult(True, int(attention_id), action="SKIP")
        result = self.print(attention_id)
        behavior = str(self.configuration.get("print_behavior_hoja") or "Imprimir y abrir PDF")
        if result.ok and behavior == "Imprimir y abrir PDF":
            self.repository.open_document(result.path)
        return result

    def pending(self) -> list[Any]:
        return self.repository.list_pending_prints()

    def retry(self, attention_id: int) -> DocumentResult:
        return self.print(int(attention_id))


class PendingPrintsDialog(QDialog):
    def __init__(self, document_service: AdmissionDocumentService, parent=None):
        super().__init__(parent)
        self.document_service = document_service
        self.setWindowTitle("Impresiones pendientes")
        self.resize(940, 560)
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        title = QLabel("Impresiones pendientes")
        title.setObjectName("PendingPrintsTitle")
        root.addWidget(title)
        self.status = QLabel()
        root.addWidget(self.status)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Atención", "Paciente", "Hoja", "Fecha", "Estado", "Intentos"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        root.addWidget(self.table, 1)
        footer = QHBoxLayout()
        refresh = QPushButton("Actualizar")
        self.open_button = QPushButton("Abrir hoja")
        self.retry_button = QPushButton("Reintentar impresión")
        close = QPushButton("Cerrar")
        footer.addWidget(refresh)
        footer.addWidget(self.open_button)
        footer.addWidget(self.retry_button)
        footer.addStretch(1)
        footer.addWidget(close)
        root.addLayout(footer)
        refresh.clicked.connect(self.reload)
        self.open_button.clicked.connect(self._open)
        self.retry_button.clicked.connect(self._retry)
        close.clicked.connect(self.close)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.apply_theme(False)
        self.reload()

    def apply_theme(self, is_dark: bool):
        dark = bool(is_dark)
        bg = "#111827" if dark else "#f4f7fb"
        card = "#182235" if dark else "#ffffff"
        text = "#e8eef8" if dark else "#14213d"
        border = "#34445e" if dark else "#d9e2ef"
        header = "#24324a" if dark else "#eef3f9"
        selected = "#25446f" if dark else "#dbeafe"
        disabled = "#253044" if dark else "#edf1f6"
        self.setStyleSheet(f"""
            QDialog {{ background: {bg}; color: {text}; }}
            QLabel#PendingPrintsTitle {{ font-size: 21px; font-weight: 700; color: {text}; }}
            QTableWidget {{ background: {card}; color: {text}; border: 1px solid {border}; gridline-color: {border}; selection-background-color: {selected}; selection-color: {text}; }}
            QHeaderView::section {{ background: {header}; color: {text}; font-weight: 700; padding: 7px; border: none; border-bottom: 1px solid {border}; }}
            QPushButton {{ min-height: 34px; padding: 0 14px; border: 1px solid {border}; border-radius: 7px; background: {card}; color: {text}; }}
            QPushButton:hover {{ border-color: #2474e5; }}
            QPushButton:disabled {{ color: #93a0b3; background: {disabled}; }}
        """)

    @staticmethod
    def _mapping(row):
        if isinstance(row, Mapping):
            return dict(row)
        try:
            return dict(row)
        except Exception:
            return {}

    def reload(self):
        try:
            rows = list(self.document_service.pending() or [])
        except Exception as exc:
            self.status.setText("No fue posible consultar las impresiones pendientes.")
            self.status.setToolTip(str(exc))
            rows = []
        self.table.setRowCount(0)
        for value in rows:
            row = self._mapping(value)
            index = self.table.rowCount()
            self.table.insertRow(index)
            values = (
                row.get("atencion_id", ""), row.get("nombre", ""),
                row.get("hoja", ""), row.get("fecha", ""),
                row.get("impresion_estado", ""), row.get("intentos", 0),
            )
            for column, item_value in enumerate(values):
                item = QTableWidgetItem(str(item_value or ""))
                if column == 0:
                    item.setData(Qt.UserRole, row.get("atencion_id"))
                self.table.setItem(index, column, item)
        self.status.setText(
            f"{len(rows)} trabajo(s) pendiente(s)." if rows else "No hay impresiones pendientes."
        )
        self._selection_changed()

    def _selected_id(self):
        indexes = self.table.selectionModel().selectedRows()
        if not indexes:
            return None
        value = self.table.item(indexes[0].row(), 0).data(Qt.UserRole)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _selection_changed(self):
        selected = self._selected_id() is not None
        self.open_button.setEnabled(selected)
        self.retry_button.setEnabled(selected)

    def _open(self):
        attention_id = self._selected_id()
        if attention_id is not None:
            result = self.document_service.open(attention_id)
            if not result.ok:
                QMessageBox.warning(self, "Abrir hoja", result.message)

    def _retry(self):
        attention_id = self._selected_id()
        if attention_id is not None:
            result = self.document_service.retry(attention_id)
            if not result.ok:
                QMessageBox.warning(self, "Reintentar impresión", result.message)
            self.reload()
