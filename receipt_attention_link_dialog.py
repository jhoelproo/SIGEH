"""Explicit confirmation for linking an existing receipt to a pending admission."""

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton, QHBoxLayout

from receipt_attention_link import link_receipt_to_inherited_attention


class LinkWorker(QThread):
    def __init__(self, receipt_id, attention, username, backend, session_id, parent):
        super().__init__(parent)
        self.arguments = (receipt_id, attention, username)
        self.backend = backend
        self.session_id = session_id
        self.error = ""

    def run(self):
        try:
            link_receipt_to_inherited_attention(
                *self.arguments, backend=self.backend, session_id=self.session_id
            )
        except (ValueError, PermissionError) as exc:
            self.error = str(exc)
        except Exception as exc:
            self.backend.write_runtime_log(
                f"Vincular recibo: {type(exc).__name__}: {exc}"
            )
            self.error = "No se pudo confirmar el vínculo. Actualiza el historial antes de reintentar."


class LinkConfirmationDialog(QDialog):
    def __init__(self, receipt, attention, user, backend, session_id, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Vincular recibo a una atención de Admisión")
        self.setMinimumWidth(540)
        self.worker = LinkWorker(
            receipt["id"], attention, user["username"], backend, session_id, self
        )
        layout = QVBoxLayout(self)
        details = QLabel(
            f"Recibo N.º {receipt['numero']} · {receipt['nombre']}\n"
            f"Fecha: {receipt['fecha']} · ARS: {receipt['ars']}\n\n"
            f"Atención N.º {attention.attention_id} · {attention.name}\n"
            f"Fecha: {attention.service_date} · ARS: {attention.canonical_ars or attention.ars}\n"
            f"Turno de origen: {attention.turn_id}\n\n"
            "La atención quedará resuelta con este recibo. Se conservarán su fecha, "
            "autor e importes originales. La vinculación quedará registrada internamente."
        )
        details.setTextFormat(Qt.TextFormat.PlainText)
        details.setWordWrap(True)
        layout.addWidget(details)
        self.message = QLabel(
            "Revisa que ambos registros correspondan al mismo paciente."
        )
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Cancelar")
        self.confirm_button = QPushButton("Confirmar vínculo")
        backend.set_button_role(self.cancel_button, "neutral")
        backend.set_button_role(self.confirm_button, "success")
        self.confirm_button.setAutoDefault(False)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.start_link)
        self.worker.finished.connect(self.link_finished)

    def start_link(self):
        self.confirm_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.message.setText("Confirmando vínculo…")
        self.worker.start()

    def link_finished(self):
        if self.worker.error:
            self.message.setText(self.worker.error)
            self.cancel_button.setEnabled(True)
            self.cancel_button.setText("Cerrar")
        else:
            self.accept()

    def done(self, result):
        if not self.worker.isRunning():
            super().done(result)

    def closeEvent(self, event):
        if self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


def open_receipt_link(history, backend):
    user = history.main_window.current_user
    receipt = history._selected_receipt()
    if not receipt or not backend.is_administrator(user):
        return
    session_id = str(getattr(history.main_window, "session_id", "") or "")
    picker = backend.AdmissionHistoryDialog(user, history, initial_search=False)
    picker.session_id = session_id
    picker.setWindowTitle("Buscar atención sin recibo en el historial de Admisión")
    _restrict_link_picker(picker, receipt)
    for button in (
        picker.open_receipt_button,
        picker.cancel_inherited_button,
    ):
        button.hide()
    QTimer.singleShot(0, picker.search)
    if picker.exec() != QDialog.DialogCode.Accepted:
        return
    attention = picker.selected_for_billing()
    if attention is None:
        return
    confirmation = LinkConfirmationDialog(
        receipt, attention, user, backend, session_id, history
    )
    if confirmation.exec() == QDialog.DialogCode.Accepted:
        history.load_rows(reset=False, refresh_metrics=True)


def _restrict_link_picker(picker, receipt):
    for combo, label, value in (
        (picker.receipt_combo, "Sin recibo", "SIN_RECIBO"),
        (picker.status_combo, "Sin facturación", "SIN_RECIBO"),
    ):
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(label, value)
        combo.setEnabled(False)
        combo.blockSignals(False)
    picker.search_edit.setText(
        str(
            receipt.get("admission_nss_snapshot")
            or receipt.get("admission_cedula_snapshot")
            or receipt["nombre"]
        )
    )
