from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from local_receipts import receipt_command
from tests.test_local_receipts import receipt_job
from tests.test_mainwindow_viewport_oct08 import main_window as main_window
from tests.test_receipt_continuity_worker import worker as worker, job as worker_job


def test_individual_deny_blocks_local_preliminary_before_enqueue():
    job = receipt_job()
    job["current_user"]["permission_overrides"] = {
        "receipt.save_without_authorization": False
    }
    with pytest.raises(PermissionError, match="autorización"):
        receipt_command(job)


def test_live_individual_deny_blocks_central_save(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_user",
        lambda _name: {
            "username": "operator",
            "role": app.ROLE_ADMIN,
            "permission_overrides": {"receipt.save_without_authorization": False},
        },
    )
    database = Mock(side_effect=AssertionError("No writes allowed"))
    monkeypatch.setattr(app, "db_connect", database)
    with pytest.raises(PermissionError, match="autorización"):
        app.save_receipt_with_items(
            None,
            1,
            "QA",
            "2026-10-27",
            "CEFALEA",
            "FUTURO",
            0,
            10,
            "",
            "operator",
            0,
            "",
            [],
            verification_bypass={"reason": "Motivo de prueba"},
        )
    database.assert_not_called()


@pytest.mark.parametrize(
    "coverage,authorization",
    [("EXTRANJERO", ""), ("NO_ASEGURADO", ""), ("ASEGURADO", "000123")],
)
def test_self_pay_and_authorized_receipts_are_exempt(coverage, authorization):
    job = receipt_job()
    job.update(coverage=coverage, authorization_number=authorization)
    job["current_user"]["permission_overrides"] = {
        "receipt.save_without_authorization": False
    }
    assert receipt_command(job)


def test_ui_denial_prevents_worker_submission(main_window, monkeypatch):
    window = main_window
    window.name_edit.setText("PACIENTE QA")
    window.current_user["permission_overrides"] = {
        app.PERMISSION_SAVE_PRELIMINARY_WITHOUT_AUTHORIZATION: False
    }
    window.authorization_edit.clear()
    window._last_generate_time = 0
    submit = Mock()
    worker = Mock(submit=submit, can_contact_central=False)
    worker.isRunning.return_value = False
    monkeypatch.setattr(window, "pdf_worker", worker, raising=False)
    toast = Mock()
    monkeypatch.setattr(app, "FloatingToast", toast)
    window.generate_pdf()
    submit.assert_not_called()
    assert "autorización" in toast.call_args.args[0]


def test_worker_rejects_missing_authorization_before_local_or_central_save(worker):
    value = worker_job()
    value["authorization_number"] = ""
    value["current_user"]["permission_overrides"] = {
        app.PERMISSION_SAVE_PRELIMINARY_WITHOUT_AUTHORIZATION: False
    }
    local = Mock()
    worker._prepare_local_job = local
    worker.process(value)
    local.assert_not_called()
    assert worker.signals.finished_signal.emit.call_args.args[0] is False
