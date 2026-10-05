import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog

import CALCULOS_QT as app
import receipt_authorization_dialog as ui


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def receipt(**changes):
    return {
        "id": 1,
        "numero": 42,
        "nombre": "PACIENTE SINTÉTICO",
        "numero_autorizacion": "",
        "estado_facturacion": app.BILLING_PENDING,
        "tipo_cobertura": "ASEGURADO",
        **changes,
    }


def dialog(operation=None):
    backend = SimpleNamespace(
        update_receipt_authorization=operation or Mock(), write_runtime_log=Mock()
    )
    return ui.ReceiptAuthorizationDialog(receipt(), {"role": app.ROLE_ADMIN}, backend)


def finish(qt, window):
    deadline = time.monotonic() + 5
    while window.worker.isRunning() and time.monotonic() < deadline:
        qt.processEvents()
        window.worker.wait(5)
    assert not window.worker.isRunning()
    qt.processEvents()


def test_quick_dialog_saves_asynchronously_and_preserves_zeroes(qt):
    window = dialog()
    window.show()
    window.authorization.setText("001234")
    window.save_button.click()
    assert not window.save_button.isEnabled()
    window.save()
    finish(qt, window)
    assert window.result() == QDialog.Accepted
    window.backend.update_receipt_authorization.assert_called_once_with(
        1, "001234", {"role": app.ROLE_ADMIN}, expected_authorization=""
    )


@pytest.mark.parametrize("value", ["", "123", "ABCD"])
def test_invalid_input_keeps_dialog_and_never_writes(qt, value):
    window = dialog()
    window.show()
    window.authorization.setText(value)
    window.save_button.click()
    assert window.worker is None
    assert "4 y 40" in window.message.text()
    window.backend.update_receipt_authorization.assert_not_called()
    window.cancel_button.click()


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Cambió la autorización"),
        PermissionError("Sin permiso"),
        RuntimeError("private error"),
    ],
)
def test_error_keeps_dialog_and_requires_refresh_before_retry(qt, error):
    window = dialog(Mock(side_effect=error))
    window.show()
    window.authorization.setText("1234")
    window.save()
    finish(qt, window)
    assert window.result() == QDialog.Rejected
    assert not window.save_button.isEnabled()
    assert window.cancel_button.isEnabled()
    assert "private error" not in window.message.text()
    window.cancel_button.click()


def test_cancel_and_close_do_not_write_and_busy_dialog_cannot_close(qt, monkeypatch):
    window = dialog()
    window.show()
    window.cancel_button.click()
    window.backend.update_receipt_authorization.assert_not_called()
    window.closeEvent(QCloseEvent())
    window.worker = ui.AuthorizationWorker(Mock(), Mock(), window)
    monkeypatch.setattr(window.worker, "isRunning", lambda: True)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    window.done(QDialog.Accepted)
    assert window.result() == QDialog.Rejected
    monkeypatch.setattr(window.worker, "isRunning", lambda: False)
    window.close()


@pytest.mark.parametrize(
    "record,role,enabled",
    [
        (None, app.ROLE_ADMIN, False),
        (receipt(), app.ROLE_ADMIN, True),
        (receipt(), app.ROLE_AUX, True),
        (receipt(), app.ROLE_AUDIT, True),
        (receipt(), "unknown", False),
        (receipt(estado_facturacion=app.BILLING_INVOICED), app.ROLE_ADMIN, False),
        (receipt(tipo_cobertura="EXTRANJERO"), app.ROLE_ADMIN, False),
    ],
)
def test_history_button_permissions_and_signal(qt, monkeypatch, record, role, enabled):
    monkeypatch.setattr(
        app.ReceiptHistoryDialog, "load_rows", lambda *args, **kwargs: None
    )
    window = app.ReceiptHistoryDialog(
        SimpleNamespace(current_user={"role": role}, is_dark_mode=True)
    )
    monkeypatch.setattr(window, "_selected_receipt", lambda: record)
    window._update_action_state()
    assert window.btn_authorization.isEnabled() == enabled
    opener = Mock()
    monkeypatch.setattr(ui, "open_receipt_authorization", opener)
    if enabled:
        window.btn_authorization.click()
        opener.assert_called_once_with(window, app)
    window.close()


@pytest.mark.parametrize("result", [QDialog.Accepted, QDialog.Rejected])
def test_open_helper_refreshes_only_after_success(qt, monkeypatch, result):
    history = SimpleNamespace(
        _selected_receipt=lambda: receipt(),
        main_window=SimpleNamespace(current_user={"role": app.ROLE_ADMIN}),
        load_rows=Mock(),
    )
    constructor = Mock()
    constructor.return_value.exec.return_value = result
    monkeypatch.setattr(ui, "ReceiptAuthorizationDialog", constructor)
    ui.open_receipt_authorization(history, app)
    assert history.load_rows.call_count == (result == QDialog.Accepted)


def test_open_helper_denies_without_selection(qt, monkeypatch):
    history = SimpleNamespace(
        _selected_receipt=lambda: None,
        main_window=SimpleNamespace(current_user={"role": app.ROLE_ADMIN}),
    )
    constructor = Mock()
    monkeypatch.setattr(ui, "ReceiptAuthorizationDialog", constructor)
    ui.open_receipt_authorization(history, app)
    constructor.assert_not_called()
