"""Cached suggestions survive offline use, faults, retries, and restarts."""

from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget, QDialog, QMenu

import diagnosis_recommendation_ui as ui
from diagnosis_recommendations import normalize_config
from diagnosis_recommendation_store import apply_changes, apply_response, restore_model
from network_retry import NetworkRetryGate


def snapshot():
    config = normalize_config(
        {
            "diagnoses": [{"name": "ALFA", "aliases": ["A"]}],
            "rules": [
                {
                    "diagnosis": "ALFA",
                    "specialty": "GENERAL",
                    "category": "Medicamentos",
                    "item": "MED A",
                    "mode": "pin",
                    "priority": 1,
                }
            ],
        }
    )
    return {
        "cursor": 1,
        "generation": "qa-generation",
        "config": {**config, "version": 1},
        "groups": {},
    }


def wait_task(task):
    done = Event()
    task.finished.connect(done.set, Qt.DirectConnection)
    if not task.isFinished():
        assert done.wait(2)
    assert task.wait(1000)
    QApplication.processEvents()


@pytest.fixture
def runtime(monkeypatch):
    memory = SimpleNamespace(value=snapshot())
    memory.cached = Mock(side_effect=lambda *_: deepcopy(memory.value))
    memory.cache = Mock(
        side_effect=lambda _key, value: setattr(memory, "value", deepcopy(value))
    )
    monkeypatch.setattr(ui, "local_receipt_store", lambda: memory)
    backend = SimpleNamespace(
        db_pool=None,
        write_runtime_log=Mock(),
        user_is_admin=lambda user: False,
        db_connect=Mock(),
    )
    window = QWidget()
    window.dx_edit = QLineEdit("A", window)
    window.universal = {"Medicamentos": {"MED A": 10}}
    window.ars_cache = {}
    window.current_user = {"username": "aux", "role": "auxiliar"}
    window.current_admission_attention = {}
    window.refresh_picker = Mock()
    controller = ui.RecommendationController(window, backend)
    window.recommendation_controller = controller
    yield controller, memory, backend, window
    controller.stop()
    window.close()
    window.deleteLater()


def test_offline_restart_retains_suggestions_without_network(runtime):
    controller, memory, backend, window = runtime
    assert ("Medicamentos", "MED A") in window.recommendation_labels
    controller.start()
    backend.db_connect.assert_not_called()
    controller.stop()
    assert not controller.timer.isActive()
    assert memory.value["cursor"] == 1


def test_new_revision_is_persisted_but_empty_poll_does_not_repaint(runtime):
    controller, memory, _backend, window = runtime
    blocked = snapshot()["config"]
    blocked["rules"][0]["mode"] = "block"
    controller.loaded(
        {
            "installed": True,
            "generation": "qa-generation",
            "changes": [{"change_key": "config", "revision": 2, "payload": blocked}],
            "more": False,
        }
    )
    assert not window.recommendation_labels and memory.value["cursor"] == 2
    window.refresh_picker.assert_called_once()
    window.refresh_picker.reset_mock()
    controller.loaded({"changes": [], "more": False})
    window.refresh_picker.assert_not_called()
    assert memory.cache.call_count == 1


def test_corrupt_delta_preserves_previous_model(runtime):
    controller, memory, backend, _window = runtime
    before = deepcopy(controller.model)
    controller.loaded(
        {
            "changes": [
                {"change_key": "group:bad", "revision": 2, "payload": {"bad": "data"}}
            ],
            "more": False,
        }
    )
    assert controller.model == before and not controller.retry.ready
    memory.cache.assert_not_called()
    backend.write_runtime_log.assert_called()


def test_storage_error_does_not_remove_in_memory_suggestions(runtime):
    controller, memory, backend, window = runtime
    memory.cache.side_effect = OSError("Disk unavailable")
    controller.loaded(
        {
            "changes": [
                {"change_key": "config", "revision": 2, "payload": snapshot()["config"]}
            ],
            "more": False,
        }
    )
    assert controller.model["cursor"] == 2
    assert window.recommendation_labels
    backend.write_runtime_log.assert_called()


def test_restriction_stops_repeat_queries_for_fifteen_minutes(runtime):
    controller, _memory, backend, _window = runtime
    clock = [0.0]
    controller.retry = NetworkRetryGate(clock=lambda: clock[0])
    controller.failed(RuntimeError("egress quota exceeded"))
    controller.request_sync()
    backend.db_connect.assert_not_called()
    clock[0] = 899
    assert not controller.retry.ready
    clock[0] = 900
    assert controller.retry.ready


def test_real_background_task_updates_cursor_and_finishes_without_gui_io(
    runtime, monkeypatch
):
    controller, _memory, backend, window = runtime
    backend.db_pool = object()
    con = Mock()

    @contextmanager
    def connect():
        yield con

    backend.db_connect = connect
    fetch = Mock(return_value={"changes": [], "more": False, "installed": True})
    monkeypatch.setattr(ui, "synchronize", fetch)
    controller.request_sync()
    task = controller.worker
    wait_task(task)
    assert controller.worker is None and window._recommendation_worker is None
    assert fetch.call_args.args[1] == 1
    assert fetch.call_args.kwargs["generation"] == "qa-generation"


def test_background_failure_and_cancellation_signal_outcomes():
    def broken():
        raise RuntimeError("Synthetic failure")

    task = ui.RecommendationTask(broken)
    errors = QSignalSpy(task.failed)
    task.start()
    wait_task(task)
    assert errors.count() == 1
    assert task.cancel()
    task.connection = SimpleNamespace(
        cancel=Mock(side_effect=RuntimeError("Already closed"))
    )
    assert task.cancel() is False
    task.deleteLater()


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {"config": None},
        {"groups": []},
        {"cursor": -1},
        {"groups": {"bad": {}}},
    ],
)
def test_invalid_cache_is_rejected_for_safe_rebuild(value):
    with pytest.raises(ValueError):
        restore_model(value)


def test_reset_and_tombstone_remove_stale_groups_without_receipt_data():
    model = apply_changes(
        {},
        [{"revision": 1, "change_key": "group:old", "payload": {"diagnosis": "ALFA"}}],
    )
    updated = apply_changes(
        model, [{"revision": 2, "change_key": "group:old", "payload": None}]
    )
    assert not updated["groups"]
    reset = apply_response(
        model, {"installed": True, "reset": True, "generation": "new", "changes": []}
    )
    assert reset["cursor"] == 0 and not reset["groups"]
    assert apply_response(model, {"installed": False}) is model


def test_real_sqlite_cache_is_available_to_a_new_offline_window(
    runtime, tmp_path, monkeypatch
):
    from local_receipts import LocalReceiptStore

    controller, _memory, backend, _window = runtime
    store = LocalReceiptStore(tmp_path / "offline.sqlite3")
    monkeypatch.setattr(ui, "local_receipt_store", lambda: store)
    controller.loaded(
        {
            "changes": [
                {"change_key": "config", "revision": 2, "payload": snapshot()["config"]}
            ],
            "more": False,
        }
    )
    window = QWidget()
    window.dx_edit = QLineEdit("A", window)
    window.universal = {"Medicamentos": {"MED A": 10}}
    window.ars_cache = {}
    next_controller = ui.RecommendationController(window, backend)
    assert next_controller.model["cursor"] == 2
    assert ("Medicamentos", "MED A") in window.recommendation_labels
    backend.db_connect.assert_not_called()
    next_controller.stop()
    window.deleteLater()


def test_bad_cache_keeps_billing_window_available(runtime, monkeypatch):
    _controller, memory, backend, window = runtime
    memory.cached.side_effect = ValueError("Broken JSON")
    fallback = ui.RecommendationController(window, backend)
    assert fallback.model["cursor"] == 0 and not window.recommendation_labels
    backend.write_runtime_log.assert_called()
    fallback.stop()


def test_closed_controller_ignores_late_results_and_does_not_repaint(runtime):
    controller, memory, _backend, window = runtime
    controller.stop()
    controller.loaded(
        {
            "changes": [
                {"change_key": "config", "revision": 2, "payload": snapshot()["config"]}
            ],
            "more": True,
        }
    )
    controller.refresh()
    memory.cache.assert_not_called()
    window.refresh_picker.assert_not_called()


def test_more_batches_schedule_background_refresh_without_sleep(runtime, monkeypatch):
    controller, _memory, _backend, _window = runtime
    schedule = Mock()
    monkeypatch.setattr(ui.QTimer, "singleShot", schedule)
    controller.more = True
    controller.finished()
    assert schedule.call_args.args[0] == ui.BATCH_INTERVAL_MS
    controller.stop()
    schedule.reset_mock()
    controller.finished()
    schedule.assert_not_called()


def test_save_guard_honors_cancellation_and_legacy_window():
    ui.normalize_receipt_diagnosis(SimpleNamespace())
    ui.stop_recommendations(SimpleNamespace())
    window = SimpleNamespace(
        recommendation_controller=SimpleNamespace(normalize_before_save=lambda: False)
    )
    with pytest.raises(ValueError, match="cancelada"):
        ui.normalize_receipt_diagnosis(window)


@pytest.mark.parametrize(
    "selected,expected", [("ALFA", "ALFA"), ("Conservar texto ingresado", "X")]
)
def test_ambiguous_diagnosis_can_be_selected_or_preserved(
    runtime, monkeypatch, selected, expected
):
    controller, _memory, _backend, window = runtime
    controller.model["config"] = normalize_config(
        {
            "diagnoses": [
                {"name": "ALFA", "aliases": ["X"]},
                {"name": "BETA", "aliases": ["X"]},
            ]
        }
    )
    monkeypatch.setattr(ui.QInputDialog, "getItem", lambda *_: (selected, True))
    window.dx_edit.setText("X")
    assert controller.normalize_before_save() and window.dx_edit.text() == expected
    window.dx_edit.setText("DESCONOCIDO")
    assert controller.normalize_before_save() and window.dx_edit.text() == "DESCONOCIDO"


@pytest.mark.parametrize("result", [QDialog.Accepted, QDialog.Rejected])
def test_admin_dialog_result_only_refreshes_after_save(runtime, monkeypatch, result):
    import diagnosis_recommendation_admin as admin

    controller, _memory, backend, _window = runtime
    backend.user_is_admin = lambda _user: True
    backend.db_pool = object()
    controller.request_sync = Mock()
    monkeypatch.setattr(
        admin,
        "RecommendationAdminDialog",
        lambda *_: SimpleNamespace(exec=lambda: result),
    )
    controller.open_admin()
    assert controller.request_sync.call_count == int(result == QDialog.Accepted)


def test_offline_admin_receives_explanation_and_roles_control_menu(
    runtime, monkeypatch
):
    controller, _memory, backend, window = runtime
    notice = Mock()
    monkeypatch.setattr(ui.QMessageBox, "information", notice)
    backend.user_is_admin = lambda _user: True
    controller.open_admin()
    notice.assert_called_once()
    menu = QMenu(window)
    ui.install_recommendation_action(menu, window, backend)
    assert len(menu.actions()) == 1
    window.recommendation_controller.open_admin = Mock()
    menu.actions()[0].trigger()
    window.recommendation_controller.open_admin.assert_called_once()
    backend.user_is_admin = lambda _user: False
    empty = QMenu(window)
    ui.install_recommendation_action(empty, window, backend)
    assert not empty.actions()


def test_connection_scope_checks_cancellation_and_clears_raw_handle(
    runtime, monkeypatch
):
    _controller, _memory, backend, _window = runtime
    con = Mock()

    @contextmanager
    def connect():
        yield con

    backend.db_connect = connect
    with ui.recommendation_connection(backend):
        pass
    task = ui.RecommendationTask(lambda: None)
    monkeypatch.setattr(task, "isInterruptionRequested", lambda: True)
    monkeypatch.setattr(ui.QThread, "currentThread", lambda: task)
    with pytest.raises(InterruptedError):
        with ui.recommendation_connection(backend):
            pass
    assert task.connection is None
    task.run()
    task.deleteLater()


def test_self_cancelled_task_does_not_notify_a_closing_window():
    task = ui.RecommendationTask(lambda: None)

    def cancel_and_return():
        task.requestInterruption()
        return {"ignored": True}

    task.callback = cancel_and_return
    completed = QSignalSpy(task.completed)
    task.start()
    wait_task(task)
    assert completed.count() == 0
    task.connection = SimpleNamespace(cancel=Mock())
    assert task.cancel()
    assert task.wait_for_exit()
    task.deleteLater()


@pytest.mark.parametrize(
    "value",
    [
        {"config": {"diagnoses": [1]}},
        {
            "groups": {
                "x": {
                    "diagnosis": "ALFA",
                    "specialty": "GENERAL",
                    "receipts": -1,
                    "items": [],
                }
            }
        },
        {
            "groups": {
                "x": {
                    "diagnosis": "ALFA",
                    "specialty": "EMERGENCIOLOGÍA",
                    "receipts": 1,
                    "items": None,
                }
            }
        },
        {
            "groups": {
                "x": {
                    "diagnosis": "ALFA",
                    "specialty": "EMERGENCIOLOGÍA",
                    "receipts": 1,
                    "items": [{}],
                }
            }
        },
        {
            "groups": {
                "x": {
                    "diagnosis": "ALFA",
                    "specialty": "EMERGENCIOLOGÍA",
                    "receipts": 1,
                    "items": [
                        {
                            "category": "Medicamentos",
                            "item": "MED A",
                            "uses": 1,
                            "pending": 0,
                            "invoiced": 0,
                            "candidates": 0,
                        }
                    ],
                }
            }
        },
    ],
)
def test_malformed_nested_cache_is_rejected(value):
    with pytest.raises(ValueError):
        restore_model(value)


def test_refresh_requested_while_busy_runs_immediately_after_completion(
    runtime, monkeypatch
):
    controller, _memory, backend, _window = runtime
    backend.db_pool = object()
    controller.worker = Mock()
    schedule = Mock()
    monkeypatch.setattr(ui.QTimer, "singleShot", schedule)
    controller.request_sync()
    assert controller.requested
    controller.finished()
    assert schedule.call_args.args[0] == 0 and not controller.requested


def test_valid_group_cache_and_old_delta_replay_are_idempotent():
    value = snapshot()
    value["groups"] = {
        "alfa::EMERGENCIOLOGÍA": {
            "diagnosis": "ALFA",
            "specialty": "EMERGENCIOLOGÍA",
            "receipts": 10,
            "items": [
                {
                    "category": "Medicamentos",
                    "item": "MED A",
                    "uses": 10,
                    "invoiced": 5,
                    "pending": 5,
                    "candidates": 0,
                }
            ],
        }
    }
    restored = restore_model(value)
    assert restored == value
    assert (
        apply_changes(
            restored, [{"revision": 0, "change_key": "config", "payload": {}}]
        )["config"]
        == value["config"]
    )


def test_stopping_active_refresh_preserves_cache_and_releases_thread(
    runtime, monkeypatch
):
    controller, memory, backend, _window = runtime
    started, release = Event(), Event()
    backend.db_pool = object()

    @contextmanager
    def connect():
        yield Mock()

    backend.db_connect = connect

    def fetch(*_args, **_kwargs):
        started.set()
        assert release.wait(2)
        return {"changes": [], "more": False, "installed": True}

    monkeypatch.setattr(ui, "synchronize", fetch)
    controller.request_sync()
    task = controller.worker
    assert started.wait(2)
    controller.stop()
    release.set()
    wait_task(task)
    assert controller.worker is None and controller.closed
    memory.cache.assert_not_called()


def test_cancelled_failure_is_not_reported_after_window_closes():
    task = ui.RecommendationTask(lambda: None)

    def fail_after_cancel():
        task.requestInterruption()
        raise RuntimeError("Cancelled operation")

    task.callback = fail_after_cancel
    failures = QSignalSpy(task.failed)
    task.start()
    wait_task(task)
    assert not failures.count()
    task.deleteLater()
