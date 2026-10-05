"""A focused, asynchronous authorization correction in receipt history."""

from PySide6.QtCore import QRegularExpression, QThread, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from receipt_list_consistency import validate_authorization


class AuthorizationWorker(QThread):
    def __init__(self, operation, logger, parent):
        super().__init__(parent)
        self.operation = operation
        self.logger = logger
        self.error = ""

    def run(self):
        try:
            self.operation()
        except (ValueError, PermissionError) as exc:
            self.error = str(exc)
        except Exception as exc:
            self.logger(f"Guardar autorización: {type(exc).__name__}")
            self.error = (
                "No se pudo guardar. Actualiza el historial antes de reintentar."
            )


class ReceiptAuthorizationDialog(QDialog):
    def __init__(self, receipt, user, backend, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Registrar autorización")
        self.setMinimumWidth(430)
        self.receipt = dict(receipt)
        self.user = dict(user)
        self.backend = backend
        self.worker = None
        layout = QVBoxLayout(self)
        label = QLabel(f"Recibo {receipt['numero']} · {receipt['nombre']}")
        label.setTextFormat(Qt.PlainText)
        label.setWordWrap(True)
        layout.addWidget(label)
        self.authorization = QLineEdit(str(receipt.get("numero_autorizacion") or ""))
        self.authorization.setPlaceholderText(
            "Número de autorización (mínimo 4 dígitos)"
        )
        self.authorization.setMaxLength(40)
        self.authorization.setValidator(
            QRegularExpressionValidator(QRegularExpression("[0-9]{0,40}"), self)
        )
        layout.addWidget(self.authorization)
        self.message = QLabel("Se actualizarán el recibo y sus listados pendientes.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(Qt.PlainText)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.save_button = buttons.button(QDialogButtonBox.Save)
        self.save_button.setText("Guardar autorización")
        self.cancel_button = buttons.button(QDialogButtonBox.Cancel)
        self.cancel_button.setText("Cancelar")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.authorization.setFocus()
        self.authorization.selectAll()

    def save(self):
        if self.worker is not None:
            return
        try:
            value = validate_authorization(self.authorization.text())
        except ValueError as exc:
            self.message.setText(str(exc))
            self.authorization.setFocus()
            return
        self.worker = AuthorizationWorker(
            lambda: self.backend.update_receipt_authorization(
                self.receipt["id"],
                value,
                self.user,
                expected_authorization=self.receipt.get("numero_autorizacion"),
            ),
            self.backend.write_runtime_log,
            self,
        )
        self.worker.finished.connect(self._saved)
        self.save_button.setEnabled(False)
        self.cancel_button.setEnabled(False)
        self.authorization.setEnabled(False)
        self.message.setText("Guardando autorización…")
        self.worker.start()

    def _saved(self):
        if self.worker.error:
            self.message.setText(self.worker.error)
            self.cancel_button.setEnabled(True)
            self.cancel_button.setText("Cerrar")
        else:
            self.accept()

    def done(self, result):
        if self.worker is None or not self.worker.isRunning():
            super().done(result)

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            event.ignore()
        else:
            super().closeEvent(event)


def can_edit_authorization(receipt, user, backend):
    return bool(
        receipt
        and receipt.get("estado_facturacion", backend.BILLING_UNCLASSIFIED)
        in (backend.BILLING_PENDING, backend.BILLING_UNCLASSIFIED)
        and not backend.is_self_pay(receipt.get("tipo_cobertura"))
        and backend.user_has_permission(user, backend.PERMISSION_EDIT_PENDING)
    )


def open_receipt_authorization(history, backend):
    receipt = history._selected_receipt()
    user = history.main_window.current_user
    if not can_edit_authorization(receipt, user, backend):
        return
    dialog = ReceiptAuthorizationDialog(receipt, user, backend, history)
    if dialog.exec() == QDialog.Accepted:
        history.load_rows(reset=False, refresh_metrics=True)
