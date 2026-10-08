"""Exercise persistence acknowledgements and UI recovery without remote services."""

import queue
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

import CALCULOS_QT as app
from receipt_continuity import local_receipt_store
from tests.test_receipt_continuity_worker import job, worker as worker


@pytest.mark.parametrize("confirmed", [None, (7, 11), (8, 11)])
def test_publish_checks_central_acknowledgement(monkeypatch, confirmed):
    actor = {"username": "demo", "role": app.ROLE_ADMIN, "is_active": 1}
    monkeypatch.setattr(app, "get_user", Mock(return_value=actor))
    monkeypatch.setattr(app, "db_connect", MagicMock())
    monkeypatch.setattr(app, "get_next_recibo_number", Mock(return_value=11))
    save = Mock(return_value=7)
    monkeypatch.setattr(app, "save_receipt_with_items", save)
    monkeypatch.setattr(
        "receipt_command_sync.find_confirmed_request",
        Mock(side_effect=[None, confirmed]),
    )
    if confirmed == (7, 11):
        assert (
            app.publish_local_receipt_command(job(), "identity", "digest") == confirmed
        )
    else:
        with pytest.raises(RuntimeError, match="confirmación"):
            app.publish_local_receipt_command(job(), "identity", "digest")
    assert save.call_args.kwargs["local_request_id"] == "identity"
    assert save.call_args.kwargs["local_request_hash"] == "digest"


def test_publish_replay_does_not_allocate_another_number(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_user",
        Mock(return_value={"username": "demo", "role": app.ROLE_ADMIN}),
    )
    monkeypatch.setattr(app, "db_connect", MagicMock())
    monkeypatch.setattr(
        "receipt_command_sync.find_confirmed_request", Mock(return_value=(7, 11))
    )
    number = Mock(side_effect=AssertionError("Confirmed receipt must be reused"))
    monkeypatch.setattr(app, "get_next_recibo_number", number)
    assert app.publish_local_receipt_command(job(), "identity", "digest") == (7, 11)
    number.assert_not_called()


def test_worker_idle_loop_checks_pending_commands_and_closes_renderer(worker):
    renderer = worker.renderer
    worker.warm_up = Mock()
    worker.jobs = Mock()
    worker.jobs.get.side_effect = [queue.Empty(), None]
    worker._synchronize_local_receipts = Mock()
    worker.run()
    worker._synchronize_local_receipts.assert_called_once()
    renderer.close.assert_called_once()
    assert worker.renderer is None


@pytest.mark.parametrize("identity", [True, False])
def test_confirmation_resets_pause_and_preserves_local_receipt(worker, identity):
    request_id = local_receipt_store().enqueue(job()) if identity else ""
    worker._local_retry_gate.failed(RuntimeError("Payment Required"))
    worker._confirm_local_command(request_id, 7, 11)
    assert worker.can_contact_central
    if identity:
        row = local_receipt_store().row_for_user(request_id, "demo")
        assert (row["state"], row["central_id"], row["central_number"]) == (
            "SYNCED",
            7,
            11,
        )


@pytest.mark.parametrize("mode", [None, app.STORAGE_SNAPSHOT])
def test_worker_renders_existing_document_in_its_storage_mode(
    worker, monkeypatch, mode
):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.execute.return_value.fetchone.return_value = {
        "document_storage_mode": mode,
        "pdf_filename": "existing.pdf",
    }
    monkeypatch.setattr(app, "db_connect", Mock(return_value=connection))
    snapshot = {"numero": 11}
    load = Mock(return_value=snapshot)
    render = Mock(return_value="snapshot.pdf")
    resolve = Mock(return_value="resolved.pdf")
    monkeypatch.setattr(app, "load_current_receipt_snapshot", load)
    monkeypatch.setattr(app, "render_receipt_snapshot_pdf", render)
    monkeypatch.setattr(app, "resolve_receipt_document_path", resolve)
    result = worker._render_saved_receipt(7)
    expected = (
        ("snapshot.pdf", app.STORAGE_LEGACY) if mode is None else ("resolved.pdf", mode)
    )
    assert result == expected
    assert load.called == (mode is None)
    assert render.called == (mode is None)
    assert resolve.called == (mode is not None)


@pytest.mark.parametrize("render_error", [False, True])
def test_online_worker_keeps_confirmation_when_pdf_fails(
    worker, monkeypatch, render_error
):
    monkeypatch.setattr(app, "get_next_recibo_number", Mock(return_value=11))
    monkeypatch.setattr(app, "save_receipt_with_items", Mock(return_value=7))
    monkeypatch.setattr(app, "write_pdf_performance", Mock())
    worker._render_saved_receipt = Mock(
        side_effect=OSError("Synthetic PDF failure") if render_error else None,
        return_value=("central.pdf", app.STORAGE_SNAPSHOT),
    )
    worker.process(job())
    row = local_receipt_store().rows("demo")[0]
    assert row["state"] == "SYNCED"
    assert row["central_number"] == 11
    assert worker.signals.finished_signal.emit.call_args.args[0]


@pytest.mark.parametrize(
    "offline,tariffs,privileged",
    [(True, True, True), (True, False, True), (True, True, False), (False, True, True)],
)
def test_offline_module_availability_requires_cached_tariffs_and_permission(
    offline, tariffs, privileged
):
    window = SimpleNamespace(
        offline_login=offline,
        module_tabs=Mock(),
        emergency_module_index=0,
        billing_module_index=1,
        current_user={"role": app.ROLE_ADMIN if privileged else app.ROLE_AUX},
        _local_tariff_names=["ARS DEMO"] if tariffs else [],
    )
    window.module_tabs.count.return_value = 2
    app.MainWindow._configure_offline_modules(window)
    enabled = any(
        call.args == (1, True)
        for call in window.module_tabs.setTabEnabled.call_args_list
    )
    assert enabled == (offline and tariffs and privileged)


@pytest.mark.parametrize("offline", [True, False])
def test_history_routes_to_local_copies_only_when_offline(monkeypatch, offline):
    window = Mock(offline_login=offline)
    history = Mock()
    monkeypatch.setattr(app, "ReceiptHistoryDialog", history)
    app.MainWindow.open_receipts_history_dialog(window)
    assert window.open_local_receipts_dialog.called == offline
    assert history.called != offline


def test_local_dialog_action_opens_the_existing_dialog(monkeypatch):
    dialog = Mock()
    monkeypatch.setattr("local_receipts_dialog.LocalReceiptsDialog", dialog)
    window = Mock()
    app.MainWindow.open_local_receipts_dialog(window)
    dialog.assert_called_once_with(window)
    dialog.return_value.exec.assert_called_once()


@pytest.mark.parametrize("pdf_path", ["", "local.pdf"])
def test_local_saved_notification_preserves_selected_date(monkeypatch, pdf_path):
    window = Mock(preferences={"auto_print": False})
    window._handle_local_receipt_reopen.return_value = False
    date = app.QDate(2026, 8, 1)
    window.date_edit.date.return_value = date
    toast = Mock()
    warning = Mock()
    monkeypatch.setattr(app, "FloatingToast", toast)
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    app.MainWindow.on_pdf_generated(
        window, True, "__LOCAL_RECEIPT_SAVED__:id", pdf_path, 0
    )
    window.date_edit.setDate.assert_called_once_with(date)
    assert "pendiente" in toast.call_args.args[0]
    assert warning.called == (not pdf_path)
    assert window._open_pdf_after_generation.called == bool(pdf_path)


def test_reopening_local_copy_does_not_reset_current_cart():
    window = Mock()
    window._handle_local_receipt_reopen.return_value = True
    app.MainWindow.on_pdf_generated(
        window, True, "__LOCAL_RECEIPT_REOPENED__:id", "local.pdf", 0
    )
    window.reset_all.assert_not_called()


@pytest.mark.parametrize("failed", [False, True])
def test_loaded_tariff_cache_error_does_not_block_billing(monkeypatch, failed):
    remember = Mock(side_effect=OSError() if failed else None)
    monkeypatch.setattr("receipt_continuity.remember_tariff", remember)
    monkeypatch.setattr(app, "ARS_RUNTIME_CACHE", Mock())
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    window = Mock(current_ars="ARS DEMO")
    app.MainWindow._on_ars_runtime_loaded(window, "ARS DEMO", {"sala": 500}, 1.0)
    window._apply_ars_runtime_data.assert_called_once_with("ARS DEMO", {"sala": 500})
    remember.assert_called_once_with("ARS DEMO", {"sala": 500})


@pytest.mark.parametrize("duplicate", [False, True])
def test_business_denial_keeps_local_command_for_review(worker, monkeypatch, duplicate):
    error = (
        app.DuplicateReceiptError({"numero": 11})
        if duplicate
        else app.AdmissionAttentionUnavailableError()
    )
    monkeypatch.setattr(app, "get_next_recibo_number", Mock(side_effect=error))
    worker.process(job())
    assert local_receipt_store().rows("demo")[0]["state"] == "REVIEW"
    assert not worker.signals.finished_signal.emit.call_args.args[0]


def test_offline_deferred_start_keeps_pdf_worker_available():
    window = Mock(_deferred_services_started=False, offline_login=True)
    app.MainWindow.start_deferred_services(window)
    window._start_pdf_services.assert_called_once()
    assert window._deferred_services_started


def test_pdf_services_receive_current_identity_and_session(monkeypatch):
    worker_factory = Mock()
    monkeypatch.setattr(app, "PDFDatabaseWorker", worker_factory)
    monkeypatch.setattr(app, "PDFStorageSyncWorker", Mock())
    window = Mock(current_user={"username": "demo"}, session_id="session-demo")
    app.MainWindow._start_pdf_services(window)
    worker_factory.assert_called_once_with("demo", "session-demo")
    worker_factory.return_value.start.assert_called_once()


def test_offline_self_pay_label_is_always_pending_central_validation(monkeypatch):
    monkeypatch.setattr("billing_workspace_design.update_billing_summary", Mock())
    monkeypatch.setattr(app.MainWindow, "_apply_billing_field_policy", Mock())
    monkeypatch.setattr(app, "form_coverage", Mock(return_value="EXTRANJERO"))
    window = Mock(
        offline_login=True,
        editing_recibo_id=None,
        current_admission_attention=None,
        current_user={"role": app.ROLE_ADMIN},
    )
    app.MainWindow._update_document_flow_ui(window)
    window.btn_generate.setText.assert_called_with("GUARDAR RECIBO LOCAL (F5)")
    assert "pendiente" in window.document_flow_hint.setText.call_args.args[0]


def test_cache_failure_does_not_interrupt_session_health(monkeypatch):
    monkeypatch.setattr(
        "receipt_continuity.local_receipt_store", Mock(side_effect=OSError())
    )
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    window = Mock(_logout_finalizing=False)
    app.MainWindow._on_session_health_completed(window, {"alive": True}, 1.0)
    window._handle_inactive_login_session.assert_not_called()


def test_pdf_renderer_preserves_complete_local_identifier():
    from pdf_engine.renderer import ReceiptPDFRenderer

    renderer = ReceiptPDFRenderer()
    identity = "12345678-1234-1234-1234-123456789012"
    assert (
        renderer._prepare_data({"local_request_id": identity})["local_request_id"]
        == identity
    )
    assert renderer._prepare_data({})["local_request_id"] == ""


def test_admission_backup_failure_is_logged_without_aborting_sync():
    from admission_v15_adapter import _HybridAdmissionRuntime

    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime._backup_schedule = Mock()
    runtime._backup_schedule.run_due.side_effect = OSError()
    runtime.logger = Mock()
    runtime._run_scheduled_backup()
    runtime.logger.exception.assert_called_once()


def test_individual_restriction_stops_the_remaining_admission_uploads():
    from admission_hybrid import AdmissionSyncService

    event = Mock(event_uuid="12345678-1234-1234-1234-123456789012")
    event.payload_json.return_value = "{}"
    store = Mock()
    store.last_cloud_cursor.return_value = 0
    store.pending_events.return_value = [event, event]
    cloud = Mock()
    cloud.push_events.return_value = {}
    cloud.push_event.side_effect = RuntimeError("Payment Required")
    service = AdmissionSyncService(store, cloud)
    with pytest.raises(RuntimeError, match="Payment Required"):
        service.push_outbox()
    assert cloud.push_event.call_count == 1
