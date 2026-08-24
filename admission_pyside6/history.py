from __future__ import annotations

from typing import Any, Mapping

from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


class HistoryWorker(QThread):
    completed = Signal(int, object)
    failed = Signal(int, str)

    def __init__(self, request_id: int, loader, filters: Mapping[str, Any], parent=None):
        super().__init__(parent)
        self.request_id = int(request_id)
        self.loader = loader
        self.filters = dict(filters or {})

    def run(self):
        try:
            self.completed.emit(self.request_id, list(self.loader(self.filters) or []))
        except Exception as exc:
            self.failed.emit(self.request_id, str(exc))


class AdmissionHistoryDialog(QDialog):
    edit_requested = Signal(int)
    open_sheet_requested = Signal(int)

    COLUMNS = (
        ("id", "ID"),
        ("fecha", "Fecha"),
        ("hora", "Hora"),
        ("nombre", "Paciente"),
        ("hoja", "Hoja"),
        ("ars", "ARS"),
        ("nss", "NSS"),
        ("cedula", "Cédula"),
        ("edad", "Edad"),
        ("tipo", "Tipo"),
    )

    def __init__(self, controller, *, uninsured=False, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.uninsured = bool(uninsured)
        self.page_size = 100
        self.offset = 0
        self._request_id = 0
        self._worker = None
        self._rows: list[Any] = []
        self.setWindowTitle("Historial sin seguros" if uninsured else "Historial de atenciones")
        self.setObjectName("AdmissionHistoryDialog")
        self.resize(1280, 760)
        self._build_ui()
        self.apply_theme(False)
        self.load_page(reset=True)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)
        title = QLabel(self.windowTitle())
        title.setObjectName("HistoryTitle")
        root.addWidget(title)

        filters = QWidget()
        filters.setObjectName("HistoryFilters")
        row = QHBoxLayout(filters)
        row.setContentsMargins(12, 10, 12, 10)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Nombre, cédula o NSS")
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(("Turno actual", "Turno anterior", "Todos", "Hoy", "Sin seguro"))
        if self.uninsured:
            self.mode_combo.setCurrentText("Sin seguro")
            self.mode_combo.setEnabled(False)
        self.search_button = QPushButton("Buscar")
        self.show_all_button = QPushButton("Mostrar todo")
        row.addWidget(self.search_edit, 3)
        row.addWidget(self.mode_combo, 1)
        row.addWidget(self.search_button)
        row.addWidget(self.show_all_button)
        root.addWidget(filters)

        self.status_label = QLabel("Preparando historial…")
        self.status_label.setObjectName("HistoryStatus")
        root.addWidget(self.status_label)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([label for _, label in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        self.open_button = QPushButton("Abrir hoja")
        self.edit_button = QPushButton("Editar")
        self.cancel_button = QPushButton("Anular")
        self.previous_button = QPushButton("Anterior")
        self.next_button = QPushButton("Siguiente")
        self.retry_button = QPushButton("Reintentar")
        self.retry_button.hide()
        close_button = QPushButton("Cerrar")
        footer.addWidget(self.open_button)
        footer.addWidget(self.edit_button)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.retry_button)
        footer.addStretch(1)
        footer.addWidget(self.previous_button)
        footer.addWidget(self.next_button)
        footer.addWidget(close_button)
        root.addLayout(footer)

        self.search_button.clicked.connect(lambda: self.load_page(reset=True))
        self.search_edit.returnPressed.connect(lambda: self.load_page(reset=True))
        self.show_all_button.clicked.connect(self._show_all)
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.retry_button.clicked.connect(lambda: self.load_page(reset=False))
        self.open_button.clicked.connect(self._open_selected)
        self.edit_button.clicked.connect(self._edit_selected)
        self.cancel_button.clicked.connect(self._cancel_selected)
        self.table.itemDoubleClicked.connect(lambda _item: self._open_selected())
        close_button.clicked.connect(self.close)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self._update_actions()

    def _filters(self):
        mode = "Sin seguro" if self.uninsured else self.mode_combo.currentText()
        result = {
            "text": self.search_edit.text().strip(),
            "mode": mode,
            "limit": self.page_size,
            "offset": self.offset,
        }
        current = dict(self.controller.service.context.current_shift or {})
        if mode == "Turno actual":
            result["shift_id"] = current.get("id") or current.get("turno_id")
        return result

    def load_page(self, *, reset=False):
        if reset:
            self.offset = 0
        self._request_id += 1
        request_id = self._request_id
        self._set_loading(True, "Cargando atenciones…")
        worker = HistoryWorker(request_id, self.controller.load_history, self._filters(), self)
        self._worker = worker
        worker.completed.connect(self._load_completed)
        worker.failed.connect(self._load_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _set_loading(self, loading: bool, message: str):
        self.status_label.setText(message)
        self.search_button.setEnabled(not loading)
        self.show_all_button.setEnabled(not loading)
        self.previous_button.setEnabled(not loading and self.offset > 0)
        self.next_button.setEnabled(not loading and len(self._rows) >= self.page_size)
        if loading:
            self.retry_button.hide()

    def _load_completed(self, request_id: int, rows):
        if request_id != self._request_id:
            return
        self._rows = list(rows or [])
        self.table.setRowCount(0)
        for row_data in self._rows:
            row = self._as_mapping(row_data)
            table_row = self.table.rowCount()
            self.table.insertRow(table_row)
            values = {
                "id": row.get("id", ""), "fecha": row.get("fecha", ""),
                "hora": row.get("hora", ""), "nombre": row.get("nombre", row.get("Nombre", "")),
                "hoja": row.get("hoja", row.get("Hoja", "")), "ars": row.get("ars", row.get("Aseguradora (ARS)", "")),
                "nss": row.get("nss", row.get("NSS", "")), "cedula": row.get("cedula", row.get("Cédula", "")),
                "edad": row.get("edad_num", row.get("Edad_num", row.get("edad", ""))),
                "tipo": row.get("tipo_atencion", row.get("TipoAtencion", "")),
            }
            for column, (key, _label) in enumerate(self.COLUMNS):
                item = QTableWidgetItem(str(values.get(key, "") or ""))
                if column == 0:
                    item.setData(Qt.UserRole, values.get("id"))
                self.table.setItem(table_row, column, item)
        message = f"{len(self._rows)} resultado(s) — desde {self.offset + 1}" if self._rows else "No se encontraron registros."
        self._set_loading(False, message)
        self._update_actions()

    def _load_failed(self, request_id: int, message: str):
        if request_id != self._request_id:
            return
        self._rows = []
        self.table.setRowCount(0)
        self._set_loading(False, "No fue posible cargar el historial.")
        self.retry_button.setToolTip(str(message))
        self.retry_button.show()
        self._update_actions()

    @staticmethod
    def _as_mapping(row):
        if isinstance(row, Mapping):
            return dict(row)
        try:
            return dict(row)
        except Exception:
            return {}

    def _selected_id(self):
        selected = self.table.selectionModel().selectedRows()
        if not selected:
            return None
        value = self.table.item(selected[0].row(), 0).data(Qt.UserRole)
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _update_actions(self):
        selected = self._selected_id() is not None
        self.open_button.setEnabled(selected)
        self.edit_button.setEnabled(selected and self.controller.service.context.has_permission("admission.edit"))
        self.cancel_button.setEnabled(selected and self.controller.service.context.has_permission("admission.cancel"))

    def _open_selected(self):
        attention_id = self._selected_id()
        if attention_id is not None:
            self.open_sheet_requested.emit(attention_id)

    def _edit_selected(self):
        attention_id = self._selected_id()
        if attention_id is not None:
            self.edit_requested.emit(attention_id)

    def _cancel_selected(self):
        attention_id = self._selected_id()
        if attention_id is None:
            return
        reason, ok = self._ask_reason()
        if ok:
            result = self.controller.cancel(attention_id, reason)
            if result.ok:
                self.load_page(reset=False)
            else:
                QMessageBox.warning(self, "Anular atención", result.message or "No fue posible anular.")

    def _ask_reason(self):
        from PySide6.QtWidgets import QInputDialog
        return QInputDialog.getText(self, "Anular atención", "Motivo (mínimo 5 caracteres):")

    def _show_all(self):
        self.search_edit.clear()
        if not self.uninsured:
            self.mode_combo.setCurrentText("Todos")
        self.load_page(reset=True)

    def previous_page(self):
        self.offset = max(0, self.offset - self.page_size)
        self.load_page(reset=False)

    def next_page(self):
        if len(self._rows) >= self.page_size:
            self.offset += self.page_size
            self.load_page(reset=False)

    def apply_theme(self, is_dark: bool):
        dark = bool(is_dark)
        bg = "#111827" if dark else "#f4f7fb"
        card = "#182235" if dark else "white"
        text = "#e8eef8" if dark else "#14213d"
        border = "#34445e" if dark else "#d9e2ef"
        header = "#24324a" if dark else "#eef3f9"
        selected = "#25446f" if dark else "#dbeafe"
        disabled = "#253044" if dark else "#edf1f6"
        self.setStyleSheet(f"""
            QDialog#AdmissionHistoryDialog {{ background: {bg}; color: {text}; }}
            QLabel#HistoryTitle {{ font-size: 22px; font-weight: 700; color: {text}; }}
            QWidget#HistoryFilters {{ background: {card}; border: 1px solid {border}; border-radius: 10px; }}
            QLineEdit, QComboBox {{ min-height: 34px; border: 1px solid {border}; border-radius: 7px; padding: 0 10px; background: {card}; color: {text}; }}
            QPushButton {{ min-height: 34px; padding: 0 14px; border: 1px solid {border}; border-radius: 7px; background: {card}; color: {text}; }}
            QPushButton:hover {{ border-color: #2474e5; }}
            QPushButton:disabled {{ color: #93a0b3; background: {disabled}; }}
            QTableWidget {{ background: {card}; color: {text}; border: 1px solid {border}; border-radius: 9px; gridline-color: {border}; alternate-background-color: {bg}; selection-background-color: {selected}; selection-color: {text}; }}
            QHeaderView::section {{ background: {header}; color: {text}; font-weight: 700; border: none; border-bottom: 1px solid {border}; padding: 8px; }}
            QLabel#HistoryStatus {{ color: {text}; }}
        """)
