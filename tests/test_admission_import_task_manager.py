from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
from admission_database_import import (
    AdmissionDatabaseImporter,
    AdmissionImportSchemaError,
    AdmissionImportTaskActiveError,
    import_progress_percent,
)


def _wait_until(predicate, timeout_ms=3000):
    if predicate():
        return
    loop = QEventLoop()
    timer = QTimer()
    timer.setInterval(10)
    timer.timeout.connect(lambda: predicate() and loop.quit())
    deadline = QTimer()
    deadline.setSingleShot(True)
    deadline.timeout.connect(loop.quit)
    timer.start()
    deadline.start(timeout_ms)
    loop.exec()
    timer.stop()
    assert predicate(), "La tarea en segundo plano no alcanzó el estado esperado."


class _Importer:
    def __init__(self, gate=None, active=None, failure=None):
        self.gate = gate
        self.active = active
        self.failure = failure
        self.started = threading.Event()
        self.progress_updates = []
        self.failed = []
        self.analyze_thread_id = None
        self.apply_thread_id = None

    def recover_stale_active_task(self):
        return None

    def find_active_task(self):
        return self.active

    def load_task(self, _task_id):
        return self.active

    def update_task_progress(self, task_id, **kwargs):
        self.progress_updates.append((task_id, dict(kwargs)))

    def mark_task_failed(self, task_id, error):
        self.failed.append((task_id, error))

    def analyze(self, _path, *, mode, import_batch_id, progress, **_kwargs):
        self.analyze_thread_id = threading.get_ident()
        self.started.set()
        progress("VALIDATE_SOURCE", 1, 1)
        progress("HASH_SOURCE", 1, 2)
        if self.gate is not None:
            assert self.gate.wait(2)
        if self.failure:
            raise RuntimeError(self.failure)
        progress("READ_SQLITE", 2, 2)
        progress("STAGE_ROWS", 2, 2)
        return {
            "import_batch_id": import_batch_id,
            "mode": mode,
            "source_sha256": "a" * 64,
            "source_path": _path,
            "records": 2,
            "patients": 1,
            "INSERT": 1,
        }

    def apply(self, task_id, *, progress, **_kwargs):
        self.apply_thread_id = threading.get_ident()
        progress("VERIFY_SOURCE", 1, 1)
        progress("APPLY_ATTENTIONS", 1, 1)
        progress("FINALIZE_APPLY", 1, 1)
        return {"import_batch_id": task_id, "APPLIED_INSERT": 1}


def _manager(importer):
    return app.AdmissionDatabaseImportTaskManager(
        {"username": "admin", "role": "Administrador"},
        "PC-TEST",
        importer_factory=lambda: importer,
    )


class _RowResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _TaskStateConnection:
    def __init__(self):
        self.batch_id = "cfe6cb90-324c-4a8c-8f9c-886414df3ff8"
        self.row = {
            "import_batch_id": self.batch_id,
            "source_filename": "patients.db",
            "mode": "SEED",
            "status": "ANALYZING",
            "totals_json": '{"records": 2, "INSERT": 1}',
            "current_phase": "READ_SQLITE",
            "progress_percent": 30,
            "processed_records": 3,
            "total_records": 10,
            "status_message": "Leyendo",
            "error_message": "",
            "started_at": None,
            "progress_updated_at": None,
            "last_heartbeat_at": None,
            "completed_at": None,
            "source_sha256": "hash",
            "legacy_source_instance_id": "source",
        }
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.statements.append((compact, params))
        if compact.startswith("SELECT import_batch_id,source_filename"):
            return _RowResult(dict(self.row))
        if compact.startswith("SELECT import_batch_id FROM admission_import_batches"):
            return _RowResult((self.batch_id,))
        return _RowResult()


class _ApplyLockConnection(_TaskStateConnection):
    def execute(self, sql, params=()):
        compact = " ".join(sql.split())
        self.statements.append((compact, params))
        if compact.startswith("SELECT status,mode,source_sha256"):
            return _RowResult(("ANALYZED",))
        if "AND (%s::TEXT='' OR import_batch_id::TEXT<>" in compact:
            return _RowResult(("other-active-batch",))
        return _RowResult()


def test_import_manager_survives_close_reopen_and_does_not_duplicate(tmp_path):
    qt_app = QApplication.instance() or QApplication([])
    gate = threading.Event()
    importer = _Importer(gate=gate)
    manager = _manager(importer)
    database = tmp_path / "patients.db"
    database.touch()
    manager.start_analysis(database, "SEED")
    assert importer.started.wait(1)
    _wait_until(lambda: manager.snapshot()["status"] == "ANALYZING")

    for _ in range(5):
        dialog = app.AdmissionDatabaseImportDialog(
            {"username": "admin", "role": "Administrador"}, "PC-TEST",
            task_manager=manager,
        )
        assert dialog.progress_bar.value() >= 0
        dialog.close()
    second = app.AdmissionDatabaseImportDialog(
        {"username": "admin", "role": "Administrador"}, "PC-TEST",
        task_manager=manager,
    )
    with pytest.raises(AdmissionImportTaskActiveError):
        manager.start_analysis(database, "SEED")
    gate.set()
    _wait_until(lambda: manager.snapshot()["status"] == "ANALYZED")
    _wait_until(lambda: not manager.is_running())
    assert manager.snapshot()["preview"]["records"] == 2
    second.close()
    assert qt_app is not None


def test_import_progress_is_monotonic_and_apply_reuses_the_same_task(tmp_path):
    qt_app = QApplication.instance() or QApplication([])
    importer = _Importer()
    gui_thread_id = threading.get_ident()
    manager = _manager(importer)
    database = tmp_path / "patients.db"
    database.touch()
    percentages = {"ANALYZE": [], "APPLY": []}
    manager.task_state_changed.connect(
        lambda state: percentages.setdefault(state["operation"], []).append(state["percent"])
    )
    manager.start_analysis(database, "MERGE")
    _wait_until(lambda: manager.snapshot()["status"] == "ANALYZED")
    _wait_until(lambda: not manager.is_running())
    analyzed_id = manager.snapshot()["import_batch_id"]
    manager.start_apply()
    _wait_until(lambda: manager.snapshot()["status"] == "COMPLETED")
    _wait_until(lambda: not manager.is_running())
    assert manager.snapshot()["import_batch_id"] == analyzed_id
    assert percentages["ANALYZE"] == sorted(percentages["ANALYZE"])
    assert percentages["APPLY"] == sorted(percentages["APPLY"])
    assert percentages["APPLY"][-1] == 100
    assert importer.progress_updates
    assert importer.analyze_thread_id != gui_thread_id
    assert importer.apply_thread_id != gui_thread_id
    assert qt_app is not None


@pytest.mark.parametrize("mode", ["SEED", "MERGE"])
def test_seed_and_merge_complete_analyze_then_apply_without_an_operational_turn(tmp_path, mode):
    """The task manager does not require a live Admission turn for imports."""
    qt_app = QApplication.instance() or QApplication([])
    importer = _Importer()
    manager = _manager(importer)
    database = tmp_path / f"{mode.lower()}.db"
    database.touch()

    manager.start_analysis(database, mode)
    _wait_until(lambda: manager.snapshot()["status"] == "ANALYZED")
    manager.start_apply()
    _wait_until(lambda: manager.snapshot()["status"] == "COMPLETED")

    assert manager.snapshot()["mode"] == mode
    assert importer.analyze_thread_id is not None
    assert importer.apply_thread_id is not None
    assert qt_app is not None


def test_schema_setup_error_is_not_persisted_as_a_failed_import(tmp_path):
    class _SchemaFailingImporter(_Importer):
        def recover_stale_active_task(self):
            raise AdmissionImportSchemaError(RuntimeError("migration unavailable"))

    importer = _SchemaFailingImporter()
    manager = _manager(importer)
    manager.recover_durable_task()

    assert manager.snapshot()["status"] == "SCHEMA_ERROR"
    assert not importer.failed
    with pytest.raises(RuntimeError, match="preparar el módulo"):
        manager.start_analysis(tmp_path / "source.db", "SEED")
    assert not importer.failed


def test_import_manager_marks_worker_failure_and_recovers_remote_active_task(tmp_path):
    qt_app = QApplication.instance() or QApplication([])
    importer = _Importer(failure="fallo controlado")
    manager = _manager(importer)
    database = tmp_path / "patients.db"
    database.touch()
    manager.start_analysis(database, "SEED")
    _wait_until(lambda: manager.snapshot()["status"] == "FAILED")
    _wait_until(lambda: not manager.is_running())
    assert importer.failed

    remote = _Importer(
        active={
            "import_batch_id": "remote-batch",
            "source_filename": "patients.db",
            "mode": "SEED",
            "status": "APPLYING",
            "current_phase": "APPLY_ATTENTIONS",
            "progress_percent": 55,
            "processed_records": 55,
            "total_records": 100,
            "status_message": "Aplicando atenciones históricas",
            "totals": {},
        }
    )
    remote_manager = _manager(remote)
    remote_manager.recover_durable_task()
    assert remote_manager.snapshot()["import_batch_id"] == "remote-batch"
    with pytest.raises(AdmissionImportTaskActiveError):
        remote_manager.start_analysis(database, "SEED")
    assert qt_app is not None


def test_durable_task_helpers_and_progress_ranges_cover_guards():
    connection = _TaskStateConnection()
    importer = AdmissionDatabaseImporter(lambda: connection)
    task = importer.load_task(connection.batch_id)
    assert task["totals"]["records"] == 2
    assert importer.find_active_task()["import_batch_id"] == connection.batch_id
    assert importer.recover_stale_active_task()["import_batch_id"] == connection.batch_id
    assert any("SET status='FAILED'" in sql for sql, _params in connection.statements)
    importer.update_task_progress(
        connection.batch_id,
        operation="ANALYZE",
        phase="READ_SQLITE",
        processed=5,
        total=10,
    )
    importer.update_task_progress(
        "", operation="ANALYZE", phase="READ_SQLITE", processed=0, total=0
    )
    importer.mark_task_failed("", "ignorado")
    assert import_progress_percent("ANALYZE", "READ_SQLITE", 0, 10) == 12
    assert import_progress_percent("ANALYZE", "READ_SQLITE", 10, 10) == 30
    assert import_progress_percent("APPLY", "APPLY_ATTENTIONS", 50, 100) == 46
    with pytest.raises(ValueError):
        importer.analyze(
            "not-a-sqlite.txt", mode="INVALID", current_user={"role": "Administrador"}
        )
    with pytest.raises(ValueError):
        importer.apply(
            connection.batch_id,
            current_user={"role": "Administrador"},
            device_id="PC-TEST",
        )


def test_apply_refuses_another_centrally_active_import():
    connection = _ApplyLockConnection()
    importer = AdmissionDatabaseImporter(lambda: connection)
    with pytest.raises(AdmissionImportTaskActiveError):
        importer.apply(
            connection.batch_id,
            current_user={"role": "Administrador"},
            device_id="PC-TEST",
        )
    assert any("pg_advisory_xact_lock" in sql for sql, _params in connection.statements)
