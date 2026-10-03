"""Offscreen Qt interaction tests with isolated fake persistence."""

import os
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QDialog
import receipt_attention_link_dialog as ui


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


def dialog(backend=None):
    return ui.LinkConfirmationDialog(
        {
            "id": 1,
            "numero": 42,
            "nombre": "PACIENTE",
            "fecha": "2026-09-01",
            "ars": "ARS",
        },
        SimpleNamespace(
            attention_id=2,
            name="PACIENTE",
            service_date="2026-09-01",
            canonical_ars="ARS",
            ars="ARS",
            turn_id=10,
        ),
        {"username": "admin"},
        backend or SimpleNamespace(write_runtime_log=Mock(), set_button_role=Mock()),
        "session",
    )


def wait_worker(qt, window):
    deadline = time.monotonic() + 5
    while window.worker.isRunning() and time.monotonic() < deadline:
        qt.processEvents()
        window.worker.wait(5)
    assert not window.worker.isRunning()
    qt.processEvents()


def test_confirm_links_asynchronously_and_accepts(qt, monkeypatch):
    call = Mock(return_value=1)
    monkeypatch.setattr(ui, "link_receipt_to_inherited_attention", call)
    window = dialog()
    window.show()
    window.confirm_button.click()
    assert not window.confirm_button.isEnabled()
    wait_worker(qt, window)
    assert window.result() == QDialog.Accepted
    call.assert_called_once()
    assert call.call_args.args[2] == "admin"
    assert call.call_args.kwargs["session_id"] == "session"


@pytest.mark.parametrize(
    "error",
    [
        ValueError("Paciente distinto"),
        PermissionError("Solo admin"),
        RuntimeError("database error"),
    ],
)
def test_error_keeps_dialog_and_prevents_blind_retry(qt, monkeypatch, error):
    monkeypatch.setattr(
        ui, "link_receipt_to_inherited_attention", Mock(side_effect=error)
    )
    window = dialog()
    window.show()
    window.confirm_button.click()
    wait_worker(qt, window)
    assert window.result() != QDialog.Accepted
    assert not window.confirm_button.isEnabled()
    assert window.cancel_button.isEnabled()
    assert window.message.text() != "Confirmando vínculo…"
    window.cancel_button.click()
    assert not window.isVisible()


def test_cancel_never_calls_persistence(qt, monkeypatch):
    call = Mock()
    monkeypatch.setattr(ui, "link_receipt_to_inherited_attention", call)
    window = dialog()
    window.show()
    window.cancel_button.click()
    call.assert_not_called()


def test_cannot_close_running_operation(qt, monkeypatch):
    window = dialog()
    monkeypatch.setattr(window.worker, "isRunning", lambda: True)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    window.done(QDialog.Accepted)
    assert window.result() == QDialog.Rejected
    monkeypatch.setattr(window.worker, "isRunning", lambda: False)
    window.closeEvent(QCloseEvent())


@pytest.mark.parametrize("receipt,admin", [(None, True), ({"id": 1}, False)])
def test_no_selection_or_nonadmin_cannot_open_picker(qt, receipt, admin):
    backend = SimpleNamespace(
        is_administrator=lambda _: admin, AdmissionValidationDialog=Mock()
    )
    history = SimpleNamespace(
        main_window=SimpleNamespace(current_user={}), _selected_receipt=lambda: receipt
    )
    ui.open_receipt_link(history, backend)
    backend.AdmissionValidationDialog.assert_not_called()


@pytest.mark.parametrize(
    "picker_result,attention,confirm_result",
    [
        (QDialog.Rejected, object(), QDialog.Accepted),
        (QDialog.Accepted, None, QDialog.Accepted),
        (QDialog.Accepted, object(), QDialog.Rejected),
        (QDialog.Accepted, object(), QDialog.Accepted),
    ],
)
def test_picker_and_confirmation_control_refresh(
    qt, monkeypatch, picker_result, attention, confirm_result
):
    picker = Mock()
    picker.exec.return_value = picker_result
    picker.selected_attention.return_value = attention
    confirmation = Mock()
    confirmation.exec.return_value = confirm_result
    monkeypatch.setattr(ui, "LinkConfirmationDialog", Mock(return_value=confirmation))
    monkeypatch.setattr(QTimer, "singleShot", lambda _, callback: callback())
    backend = SimpleNamespace(
        is_administrator=lambda _: True,
        AdmissionValidationDialog=Mock(return_value=picker),
    )
    history = SimpleNamespace(
        main_window=SimpleNamespace(current_user={"username": "admin"}),
        _selected_receipt=lambda: {"id": 1, "nombre": "PACIENTE"},
        load_rows=Mock(),
    )
    ui.open_receipt_link(history, backend)
    picker.turn_filter_combo.setCurrentIndex.assert_called_once_with(1)
    picker.cancel_inherited_button.hide.assert_called_once()
    if (
        picker_result == QDialog.Accepted
        and attention
        and confirm_result == QDialog.Accepted
    ):
        history.load_rows.assert_called_once_with(reset=False, refresh_metrics=True)
    else:
        history.load_rows.assert_not_called()


@pytest.mark.parametrize(
    "role,linked,enabled",
    [
        ("admin", False, True),
        ("admin", True, False),
        ("auditor", False, False),
        ("auxiliar", False, False),
    ],
)
def test_real_history_action_permissions(qt, monkeypatch, role, linked, enabled):
    import CALCULOS_QT as app

    monkeypatch.setattr(
        app.ReceiptHistoryDialog, "load_rows", lambda *args, **kwargs: None
    )
    main = SimpleNamespace(
        current_user={"username": "test", "role": role}, is_dark_mode=False
    )
    window = app.ReceiptHistoryDialog(main)
    monkeypatch.setattr(
        window,
        "_selected_receipt",
        lambda: {"id": 1, "admission_atencion_id": 2 if linked else None},
    )
    window._update_action_state()
    assert window.action_link_attention.isEnabled() == enabled
    assert window.action_link_attention.isVisible() == (role == "admin")
    if enabled:
        opener = Mock()
        monkeypatch.setattr(ui, "open_receipt_link", opener)
        window.action_link_attention.trigger()
        opener.assert_called_once_with(window, app)
    window.close()
