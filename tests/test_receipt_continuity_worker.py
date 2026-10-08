"""Check the actual worker's offline boundary and retained failure states."""

from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from receipt_continuity import local_receipt_store
from tests.test_local_receipts import receipt_job


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    instance = app.PDFDatabaseWorker("demo", "session-demo")
    instance.renderer = Mock()
    instance.signals = Mock()
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    monkeypatch.setattr(
        "receipt_continuity.render_local_receipt", Mock(return_value="local.pdf")
    )
    return instance


def job():
    value = receipt_job()
    value.update(editing_num=None, is_backdated=0)
    return value


@pytest.mark.parametrize("offline,forced", [(True, False), (False, True)])
def test_offline_save_never_requests_central_number(
    worker, monkeypatch, offline, forced
):
    payload = job()
    payload["current_user"]["_offline_login"] = offline
    payload["force_local"] = forced
    central_number = Mock(side_effect=AssertionError("No remote calls permitted"))
    monkeypatch.setattr(app, "get_next_recibo_number", central_number)
    worker.process(payload)
    central_number.assert_not_called()
    assert local_receipt_store().rows("demo")[0]["state"] == "PENDING"
    signal = worker.signals.finished_signal.emit.call_args.args
    assert signal[0] and signal[1].startswith("__LOCAL_RECEIPT_SAVED__:")
    assert signal[2:] == ("local.pdf", 0)


def test_network_failure_keeps_local_command(worker, monkeypatch):
    monkeypatch.setattr(
        app,
        "get_next_recibo_number",
        Mock(side_effect=ConnectionError("server closed the connection")),
    )
    worker.process(job())
    assert local_receipt_store().rows("demo")[0]["state"] == "PENDING"
    assert worker.signals.finished_signal.emit.call_args.args[0]


def test_business_failure_requires_review(worker, monkeypatch):
    monkeypatch.setattr(
        app, "get_next_recibo_number", Mock(side_effect=PermissionError())
    )
    worker.process(job())
    assert local_receipt_store().rows("demo")[0]["state"] == "REVIEW"
    assert not worker.signals.finished_signal.emit.call_args.args[0]


def test_render_failure_does_not_remove_saved_local_receipt(worker, monkeypatch):
    payload = job()
    payload["force_local"] = True
    monkeypatch.setattr(
        "receipt_continuity.render_local_receipt", Mock(side_effect=RuntimeError())
    )
    monkeypatch.setattr(
        "admission_source.emergency_core.backup.BackupManager.create",
        Mock(side_effect=OSError()),
    )
    worker.process(payload)
    assert local_receipt_store().rows("demo")[0]["state"] == "PENDING"
    signal = worker.signals.finished_signal.emit.call_args.args
    assert signal[0] and signal[2] == ""


def test_reopen_does_not_enqueue_another_command(worker):
    identity = local_receipt_store().enqueue(job())
    payload = job()
    payload["reopen_local_request"] = identity
    worker.process(payload)
    assert len(local_receipt_store().rows("demo")) == 1
    assert worker.signals.finished_signal.emit.call_args.args[1].startswith(
        "__LOCAL_RECEIPT_REOPENED__:"
    )


def test_idle_sync_runs_daily_backup_and_restriction_gate(worker, monkeypatch):
    from network_retry import NetworkRetryGate
    from local_receipts import LocalReceiptSynchronizer

    now = [0.0]
    publish = Mock(side_effect=RuntimeError("402 Payment Required"))
    local_receipt_store().enqueue(job())
    worker._local_sync = LocalReceiptSynchronizer(
        local_receipt_store(),
        publish,
        app.is_temporary_connection_error,
        retry_gate=NetworkRetryGate(lambda: now[0]),
    )
    worker._synchronize_local_receipts()
    worker._synchronize_local_receipts()
    assert publish.call_count == 1
    assert list((local_receipt_store().path.parent / "backups").glob("*/manifest.json"))
    now[0] = 900
    worker._synchronize_local_receipts()
    assert publish.call_count == 2


def test_startup_cache_failure_is_safe(monkeypatch):
    monkeypatch.setattr(
        "receipt_continuity.local_catalog_startup", Mock(side_effect=OSError())
    )
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    assert app.local_billing_startup("demo") == {}


@pytest.mark.parametrize(
    "actor",
    [
        None,
        {"username": "demo", "is_active": 0},
        {"username": "demo", "role": "unknown"},
    ],
)
def test_central_replay_rejects_inactive_or_unauthorized_actor(monkeypatch, actor):
    monkeypatch.setattr(app, "get_user", Mock(return_value=actor))
    with pytest.raises(PermissionError):
        app._local_receipt_actor(job())


def test_central_replay_revalidates_linked_attention(monkeypatch):
    payload = job()
    actor = {"username": "demo", "role": app.ROLE_ADMIN}
    assert app._local_receipt_attention(payload, actor, "session") is None
    payload["admission_attention"] = {
        "attention_id": 9,
        "source_instance_id": "SOURCE",
        "global_attention_id": "global",
    }
    claim = Mock(return_value=None)
    monkeypatch.setattr(app, "claim_projected_billable_attention", claim)
    with pytest.raises(ValueError):
        app._local_receipt_attention(payload, actor, "session")
    claim.return_value = Mock()
    claim.return_value.snapshot.return_value = {"attention_id": 9, "verified": True}
    assert app._local_receipt_attention(payload, actor, "session") == {
        "attention_id": 9,
        "verified": True,
    }
    assert claim.call_args.kwargs["session_id"] == "session"


def test_idle_sync_empty_user_and_backup_error_are_safe(worker, monkeypatch):
    worker.username = ""
    worker._synchronize_local_receipts()
    assert worker._local_sync is None

    worker.username = "demo"
    monkeypatch.setattr(
        "admission_source.emergency_core.backup.DailyBackupSchedule.run_due",
        Mock(side_effect=OSError()),
    )
    worker._synchronize_local_receipts()
    assert worker._local_sync is None


def test_cached_catalog_restores_without_central_reads(monkeypatch):
    cache = Mock()
    markup = Mock()
    monkeypatch.setattr(app, "ARS_RUNTIME_CACHE", cache)
    monkeypatch.setattr(app, "update_medication_markup_cache", markup)
    data = {
        "local_tariffs": {"ARS DEMO": {"room": 500}},
        "local_medication_markup": {"percent": 10, "version": 2},
    }
    assert app.prepare_local_billing_catalog(data, {"_offline_login": True}) == [
        "ARS DEMO"
    ]
    cache.put.assert_called_once_with("ARS DEMO", {"room": 500})
    markup.assert_called_once_with(10, 2)
    assert app.prepare_local_billing_catalog({}, {"_offline_login": True}) == []
    remember = Mock(side_effect=OSError())
    monkeypatch.setattr("receipt_continuity.remember_startup_catalog", remember)
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    assert app.prepare_local_billing_catalog({}, {"username": "demo"}) == []


@pytest.mark.parametrize("central_available", [True, False])
def test_cut_during_final_admission_check_submits_pending_local_copy(
    monkeypatch, central_available
):
    from PySide6.QtCore import QDate

    snapshot = {
        "attention_id": 9,
        "source_instance_id": "SOURCE",
        "global_attention_id": "global",
    }
    window = Mock(
        _pending_ars_correction=None,
        receipt_read_only=False,
        editing_recibo_id=None,
        editing_recibo_numero=None,
        current_admission_attention=snapshot,
        current_user={"role": app.ROLE_ADMIN},
        session_id="session",
        _last_generate_time=0,
        current_ars="ARS DEMO",
    )
    window.coverage_combo.currentText.return_value = "Asegurado"
    window.name_edit.text.return_value = "PACIENTE FICTICIO"
    window.date_edit.date.return_value = QDate.currentDate()
    window.dx_edit.text.return_value = "Diagnóstico ficticio"
    window.authorization_edit.text.return_value = "000123"
    window.cart_table.rowCount.return_value = 0
    window.sala_spin.value.return_value = 500
    window.pdf_worker.can_contact_central = central_available
    central = Mock(side_effect=ConnectionError("server closed the connection"))
    monkeypatch.setattr(
        app,
        "get_projected_billable_attention",
        central,
    )
    app.MainWindow.generate_pdf(window)
    submitted = window.pdf_worker.submit.call_args.args[0]
    assert submitted["force_local"] is True
    assert submitted["admission_attention"] == snapshot
    assert submitted["total_general"] == 500
    assert central.call_count == int(central_available)


def test_idle_sync_creates_service_but_empty_queue_never_contacts_central(
    worker, monkeypatch
):
    publish = Mock(side_effect=AssertionError("Empty queue must stay local"))
    monkeypatch.setattr(app, "publish_local_receipt_command", publish)
    worker._synchronize_local_receipts()
    assert worker._local_sync is not None
    publish.assert_not_called()


def test_offline_renderer_is_created_lazily(worker, monkeypatch):
    worker.renderer = None
    renderer = Mock()
    monkeypatch.setattr("pdf_engine.ReceiptPDFRenderer", Mock(return_value=renderer))
    payload = job()
    payload["force_local"] = True
    worker.process(payload)
    assert worker.renderer is renderer
    assert worker.signals.finished_signal.emit.call_args.args[0]


def test_local_preparation_never_enqueues_central_edits(worker):
    payload = job()
    payload["editing_id"] = 5
    assert worker._prepare_local_job(payload) == ("", "", False)
    assert local_receipt_store().rows("demo") == []
    assert not worker._handle_local_failure(payload, "", PermissionError())


@pytest.mark.parametrize("path,expected", [("local.pdf", True), ("", False)])
def test_reopening_local_copy_only_opens_pdf_or_reports_failure(
    monkeypatch, path, expected
):
    window = Mock()
    warning = Mock()
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    assert app.MainWindow._handle_local_receipt_reopen(
        window, "__LOCAL_RECEIPT_REOPENED__:id", path
    )
    assert window._open_pdf_after_generation.called == expected
    assert warning.called != expected
    assert not app.MainWindow._handle_local_receipt_reopen(window, "Éxito", path)


def test_saved_status_never_assigns_a_fake_central_number():
    assert "pendiente" in app.local_receipt_saved_message(
        "__LOCAL_RECEIPT_SAVED__:id", 0
    )
    assert "N° 10" in app.local_receipt_saved_message("Éxito", 10)


def test_restriction_also_stops_repeated_user_save_requests(worker, monkeypatch):
    class Restricted(RuntimeError):
        status_code = 402

    number = Mock(side_effect=Restricted("Payment Required"))
    monkeypatch.setattr(app, "get_next_recibo_number", number)
    worker.process(job())
    second = job()
    second["patient"] = "SEGUNDO PACIENTE FICTICIO"
    worker.process(second)
    assert number.call_count == 1
    assert len(local_receipt_store().rows("demo", pending_only=True)) == 2


def test_final_attention_query_respects_closed_retry_gate(monkeypatch):
    central = Mock(side_effect=AssertionError("No remote calls permitted"))
    monkeypatch.setattr(app, "get_projected_billable_attention", central)
    with pytest.raises(ConnectionError, match="central retry deferred"):
        app.query_final_receipt_attention(
            {"attention_id": 1},
            {"username": "demo"},
            "session",
            None,
            global_attention_id="",
            central_available=False,
        )
    central.assert_not_called()


def test_worker_retries_only_after_restriction_pause_expires(worker):
    from network_retry import NetworkRetryGate, RESTRICTION_RETRY_SECONDS

    now = [0.0]
    worker._local_retry_gate = NetworkRetryGate(clock=lambda: now[0])
    worker._local_retry_gate.failed(RuntimeError("Payment Required"))
    worker._synchronize_local_receipts()
    assert worker._local_sync.retry_gate is worker._local_retry_gate
    assert not worker.can_contact_central
    now[0] = RESTRICTION_RETRY_SECONDS - 0.001
    assert not worker.can_contact_central
    now[0] = RESTRICTION_RETRY_SECONDS
    assert worker.can_contact_central
