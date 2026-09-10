from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import pytest

import CALCULOS_QT as app


@pytest.mark.parametrize(
    "change",
    [
        {"offline": True},
        {"pending_sync_count": 1},
        {"local_device_id": ""},
        {"role": ""},
    ],
)
def test_recovery_requires_synchronized_station(change):
    state = dict(role="PRIMARY", local_device_id="PC", primary_device_id="PC", **{})
    state.update(change)
    coordinator = Mock()
    workspace = SimpleNamespace(
        full_page=SimpleNamespace(
            _hybrid_runtime=SimpleNamespace(state=lambda: state),
            _hybrid_coordinator=coordinator,
        )
    )
    app.EmergencyWorkspacePage._recover_committed_closures(workspace)
    coordinator.submit_background.assert_not_called()


@pytest.mark.parametrize(
    "state",
    [
        dict(
            role="PRIMARY",
            local_device_id="PC",
            primary_device_id="PC",
            pending_sync_count=0,
        ),
        dict(
            role="SECONDARY",
            local_device_id="BILLING-PC",
            primary_device_id="ADMISSION-PC",
            pending_sync_count=0,
        ),
    ],
)
def test_any_synchronized_station_recovers_committed_closure_data(state):
    coordinator = Mock()
    workspace = SimpleNamespace(
        full_page=SimpleNamespace(
            _hybrid_runtime=SimpleNamespace(state=lambda: state),
            _hybrid_coordinator=coordinator,
        ),
        _prepare_committed_closures=Mock(),
        _finish_committed_closures=Mock(),
        _failed_committed_closures=Mock(),
    )

    app.EmergencyWorkspacePage._recover_committed_closures(workspace)

    coordinator.submit_background.assert_called_once()


def test_recovery_queues_once_and_emits_all_committed_results():
    coordinator = Mock()
    workspace = SimpleNamespace(
        full_page=SimpleNamespace(
            _hybrid_runtime=SimpleNamespace(
                state=lambda: dict(
                    role="PRIMARY", local_device_id="PC", primary_device_id="PC"
                )
            ),
            _hybrid_coordinator=coordinator,
        ),
        _prepare_committed_closures=Mock(),
        _finish_committed_closures=Mock(),
        _failed_committed_closures=Mock(),
        shift_closure_ready=Mock(),
        projection_changed=Mock(),
    )
    app.EmergencyWorkspacePage._recover_committed_closures(workspace)
    app.EmergencyWorkspacePage._recover_committed_closures(workspace)
    assert coordinator.submit_background.call_count == 1
    app.EmergencyWorkspacePage._finish_committed_closures(
        workspace, [("source", 1), ("source", 2)]
    )
    assert not workspace._closure_recovery_busy
    assert workspace.shift_closure_ready.emit.call_count == 2


def test_recovery_failure_releases_busy_guard(monkeypatch):
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    workspace = SimpleNamespace(_closure_recovery_busy=True)
    app.EmergencyWorkspacePage._failed_committed_closures(workspace, "DATABASE_ERROR")
    assert not workspace._closure_recovery_busy


def test_recovered_report_queue_deduplicates_while_worker_is_busy():
    window = SimpleNamespace(cierre_facturacion_en_progreso=True)
    app.MainWindow.process_shift_closure_report(window, "source", 1)
    app.MainWindow.process_shift_closure_report(window, "source", 1)
    app.MainWindow.process_shift_closure_report(window, "source", 2)
    assert window._pending_closure_reports == [("source", 1), ("source", 2)]


def test_missing_runtime_or_coordinator_does_not_start_recovery():
    app.EmergencyWorkspacePage._recover_committed_closures(SimpleNamespace())
    workspace = SimpleNamespace(
        full_page=SimpleNamespace(
            _hybrid_runtime=SimpleNamespace(
                state=lambda: dict(
                    role="PRIMARY", local_device_id="PC", primary_device_id="PC"
                )
            )
        )
    )
    app.EmergencyWorkspacePage._recover_committed_closures(workspace)
    assert not getattr(workspace, "_closure_recovery_busy", False)


def test_empty_recovery_does_not_emit_projection_change():
    workspace = SimpleNamespace(shift_closure_ready=Mock(), projection_changed=Mock())
    app.EmergencyWorkspacePage._finish_committed_closures(workspace, [])
    workspace.projection_changed.emit.assert_not_called()


def test_preparation_captures_central_records_before_signaling(monkeypatch):
    import billing_closure_recovery as recovery

    connection = MagicMock()
    monkeypatch.setattr(app, "db_connect", lambda: connection)
    monkeypatch.setattr(
        recovery, "pending_central_closures", lambda con: [{"turn_id": 4}]
    )
    event = SimpleNamespace(source_instance_id="central", turn_id=4)
    monkeypatch.setattr(recovery, "closure_from_interval", lambda row: event)
    records = [{"global_attention_id": "synthetic"}]
    monkeypatch.setattr(
        recovery, "closed_turn_attentions", lambda con, closure: records
    )
    capture = Mock()
    monkeypatch.setattr(app, "capture_shift_closure_snapshot", capture)
    assert app.EmergencyWorkspacePage._prepare_committed_closures(
        SimpleNamespace()
    ) == [("central", 4)]
    capture.assert_called_once_with(event, records)


def test_finished_report_schedules_next_without_losing_queue(monkeypatch):
    scheduled = []
    monkeypatch.setattr(
        app.QTimer, "singleShot", lambda delay, callback: scheduled.append(callback)
    )
    worker = Mock()
    window = SimpleNamespace(
        _shift_report_worker=worker,
        _pending_closure_reports=[("central", 1), ("central", 2)],
        process_shift_closure_report=Mock(),
    )
    app.MainWindow._shift_report_worker_finished(window)
    worker.deleteLater.assert_called_once()
    assert window._pending_closure_reports == [("central", 2)]
    assert len(scheduled) == 1
    scheduled[0]()
    window.process_shift_closure_report.assert_called_once_with("central", 1)


def test_finished_report_with_no_queue_does_not_schedule(monkeypatch):
    timer = Mock()
    monkeypatch.setattr(app.QTimer, "singleShot", timer)
    app.MainWindow._shift_report_worker_finished(
        SimpleNamespace(_shift_report_worker=None)
    )
    timer.assert_not_called()


def test_workspace_starts_recovery_timer_without_database_access(monkeypatch):
    application = app.QApplication.instance() or app.QApplication([])
    scheduled = []
    monkeypatch.setattr(
        app.QTimer,
        "singleShot",
        lambda delay, callback: scheduled.append((delay, callback)),
    )
    monkeypatch.setattr(app, "AdmissionReadOnlyRepository", Mock())
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("PC", "Synthetic"))
    monkeypatch.setattr(
        app,
        "AdmissionV15Factory",
        lambda *args, **kwargs: SimpleNamespace(
            create_widget=lambda parent: app.QWidget(parent)
        ),
    )
    monkeypatch.setattr(app, "AdmissionV15EventAdapter", Mock())
    monkeypatch.setattr(app, "write_admission_integration_log", Mock())
    workspace = app.EmergencyWorkspacePage({"username": "synthetic"}, "session")
    try:
        assert workspace._closure_recovery_timer.isActive()
        assert workspace._closure_recovery_timer.interval() == 30000
        immediate = [
            callback
            for delay, callback in scheduled
            if delay == 0 and callback == workspace._recover_committed_closures
        ]
        assert len(immediate) == 1
        immediate[0]()
        workspace._closure_recovery_timer.timeout.emit()
        assert not getattr(workspace, "_closure_recovery_busy", False)
    finally:
        workspace._closure_recovery_timer.stop()
        workspace.close()
        workspace.deleteLater()
        application.processEvents()


def test_committed_handoff_requests_recovery():
    workspace = SimpleNamespace(_recover_committed_closures=Mock())
    app.EmergencyWorkspacePage._handle_v15_shift_closed(workspace, "source", 1)
    workspace._recover_committed_closures.assert_called_once()


def test_idle_secondary_never_starts_report_worker():
    window = SimpleNamespace(
        cierre_facturacion_en_progreso=False,
        current_user={"role": "Administrador"},
        _is_primary_admission_station=lambda: False,
    )
    app.MainWindow.process_shift_closure_report(window, "source", 1)
    assert not window.cierre_facturacion_en_progreso
