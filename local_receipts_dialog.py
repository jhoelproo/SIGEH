"""Inspect this user's durable local receipts and reopen their local PDF."""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from receipt_continuity import local_receipt_store
from sigeh_visual_theme import visual_theme_tokens

STATE_LABELS = {
    "PENDING": "Pendiente de sincronización",
    "SYNCED": "Confirmado en central",
    "REVIEW": "Requiere revisión",
}


def _style_action(button, tokens, variant):
    button.setStyleSheet(
        f"QPushButton {{ background: {tokens[f'button_{variant}_bg']}; "
        f"color: {tokens[f'button_{variant}_text']}; "
        f"border: 1px solid {tokens['border']}; border-radius: 6px; "
        "padding: 8px 12px; min-height: 22px; font-weight: 600; }"
        f"QPushButton:hover {{ background: {tokens[f'button_{variant}_hover']}; }}"
        f"QPushButton:disabled {{ background: {tokens['input_disabled_bg']}; "
        f"color: {tokens['text_disabled']}; }}"
    )


class LocalReceiptsDialog(QDialog):
    def __init__(self, main_window):
        super().__init__(main_window)
        self.main_window = main_window
        self.username = str(main_window.current_user.get("username") or "")
        self.setWindowTitle("Recibos locales")
        self.resize(960, 480)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Los recibos pendientes se validan al recuperar la conexión. "
                "La copia local no representa una confirmación central."
            )
        )
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Identificador local", "Paciente", "Fecha", "Estado", "Número central"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        refresh = QPushButton("Actualizar")
        refresh.setObjectName("ModernSecondaryButton")
        self.open_button = QPushButton("Abrir copia local")
        self.open_button.setObjectName("ModernPrimaryButton")
        close = QPushButton("Cerrar")
        close.setObjectName("ModernSecondaryButton")
        tokens = visual_theme_tokens(bool(getattr(main_window, "is_dark_mode", False)))
        _style_action(refresh, tokens, "secondary")
        _style_action(self.open_button, tokens, "primary")
        _style_action(close, tokens, "secondary")
        refresh.clicked.connect(self.refresh)
        self.open_button.clicked.connect(self.open_selected)
        self.table.itemSelectionChanged.connect(self.update_actions)
        close.clicked.connect(self.accept)
        for button in (refresh, self.open_button, close):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        rows = local_receipt_store().rows(self.username)
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            job = json.loads(row["payload_json"])
            values = (
                row["request_id"][:13],
                job["patient"],
                job["date_str"],
                STATE_LABELS[row["state"]],
                str(row["central_number"] or "—"),
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(row["request_id"] if column == 0 else value)
                item.setData(Qt.ItemDataRole.UserRole, row["request_id"])
                self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        self.update_actions()

    def update_actions(self):
        self.open_button.setEnabled(bool(self.table.selectedItems()))

    def open_selected(self):
        selected = self.table.selectedItems()
        if not selected:
            return
        try:
            row = local_receipt_store().row_for_user(
                selected[0].data(Qt.ItemDataRole.UserRole), self.username
            )
            job = json.loads(row["payload_json"])
            job["reopen_local_request"] = row["request_id"]
            self.main_window.pdf_worker.submit(job)
        except Exception:
            QMessageBox.warning(
                self,
                "Recibo local",
                "No se pudo abrir la copia local. Los datos guardados se conservan.",
            )
