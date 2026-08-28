from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QApplication, QWidget

from admission_hybrid import AdmissionCloudRepository, AdmissionSyncService, SyncEvent
from database_capacity import (
    MIB,
    CapacityTrend,
    DatabaseCapacityAnalyzer,
    _mapping,
    calculate_capacity_trend,
    capacity_status,
    classify_import_staging_batch,
    format_bytes,
)
from patient_directory import PatientDirectoryService

UTC = timezone.utc


def test_capacity_threshold_boundaries():
    assert capacity_status(69.999)[0] == "NORMAL"
    assert capacity_status(70)[0] == "ATTENTION"
    assert capacity_status(84.999)[0] == "ATTENTION"
    assert capacity_status(85)[0] == "CRITICAL"
    assert capacity_status(94.999)[0] == "CRITICAL"
    assert capacity_status(95)[0] == "VERY_CRITICAL"
    assert format_bytes(10) == "10 bytes"
    assert "KB" in format_bytes(2048)
    assert "MB" in format_bytes(2 * MIB)
    assert "GB" in format_bytes(2 * 1024 * MIB)
    assert _mapping(None) == {}
    assert _mapping(object()) == {}


def test_trend_rejects_short_span_and_handles_real_growth_and_decrease():
    start = datetime(2026, 7, 1, tzinfo=UTC)
    assert calculate_capacity_trend([]).state == "INSUFFICIENT"
    short = calculate_capacity_trend(
        [
            {"captured_at": start, "database_size_bytes": 400 * MIB},
            {
                "captured_at": start + timedelta(minutes=2),
                "database_size_bytes": 399 * MIB,
            },
        ]
    )
    assert short.state == "INSUFFICIENT"
    assert short.monthly_bytes is None
    growing = calculate_capacity_trend(
        [
            {"captured_at": start, "database_size_bytes": 400 * MIB},
            {
                "captured_at": start + timedelta(days=30),
                "database_size_bytes": 430 * MIB,
            },
        ]
    )
    assert growing.state == "GROWING"
    assert 30 * MIB < growing.monthly_bytes < 31 * MIB
    decreasing = calculate_capacity_trend(
        [
            {"captured_at": start, "database_size_bytes": 430 * MIB},
            {
                "captured_at": start + timedelta(days=30),
                "database_size_bytes": 400 * MIB,
            },
        ]
    )
    assert decreasing.state == "DECREASING"
    assert decreasing.monthly_bytes < 0
    stable = calculate_capacity_trend(
        [
            {"captured_at": start, "database_size_bytes": 400 * MIB},
            {
                "captured_at": start + timedelta(days=30),
                "database_size_bytes": 400 * MIB,
            },
        ]
    )
    assert stable.state == "STABLE"


@pytest.mark.parametrize(
    ("status", "age_days", "incomplete", "expected"),
    [
        ("APPLYING", 40, 0, "ACTIVE_JOB"),
        ("ANALYZED", 40, 0, "AWAITING_APPLY"),
        ("COMPLETED", 1, 0, "RETENTION_ACTIVE"),
        ("COMPLETED", 8, 0, "SAFE_AFTER_RETENTION"),
        ("COMPLETED", 8, 1, "INCOMPLETE_RESULTS"),
        ("FAILED", 31, 0, "SAFE_AFTER_FAILED_RETENTION"),
    ],
)
def test_staging_retention_policy(status, age_days, incomplete, expected):
    now = datetime(2026, 8, 24, tzinfo=UTC)
    row = {
        "status": status,
        "completed_at": now - timedelta(days=age_days),
        "incomplete_rows": incomplete,
    }
    assert classify_import_staging_batch(row, now=now) == expected


class _Cursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self, row):
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, _params=()):
        return _Cursor(self.row)


def test_pdf_external_verification_requires_identity_size_and_hash(tmp_path: Path):
    payload = b"%PDF-1.4\nverified\n%%EOF"
    archive = tmp_path / "archive"
    archive.mkdir()
    external = archive / "doc.pdf"
    external.write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    row = {
        "filename": "doc.pdf",
        "file_data": payload,
        "document_type": "RECEIPT",
        "owner_receipt_id": 7,
        "source_table": "recibos",
        "source_key": "7",
        "sha256": digest,
        "size_bytes": len(payload),
        "archive_relative_path": "doc.pdf",
        "status": "AVAILABLE",
        "verified_at": datetime.now(UTC),
    }
    analyzer = DatabaseCapacityAnalyzer(
        lambda: _Connection(dict(row)), archive_root=archive
    )
    assert analyzer.verify_external_pdf("doc.pdf")["safe"] is True
    mismatch = dict(row, sha256="0" * 64)
    analyzer = DatabaseCapacityAnalyzer(
        lambda: _Connection(mismatch), archive_root=archive
    )
    assert analyzer.verify_external_pdf("doc.pdf")["reason"] == "HASH_OR_SIZE_MISMATCH"
    unknown = dict(row, source_table=None, source_key=None, document_type="UNKNOWN")
    analyzer = DatabaseCapacityAnalyzer(
        lambda: _Connection(unknown), archive_root=archive
    )
    assert (
        analyzer.verify_external_pdf("doc.pdf")["reason"] == "UNCLASSIFIED_OR_UNLINKED"
    )
    analyzer = DatabaseCapacityAnalyzer(lambda: _Connection(None), archive_root=archive)
    assert analyzer.verify_external_pdf("missing.pdf")["reason"] == "NOT_FOUND"
    analyzer = DatabaseCapacityAnalyzer(lambda: _Connection(dict(row)))
    assert (
        analyzer.verify_external_pdf("doc.pdf")["reason"]
        == "ARCHIVE_ROOT_NOT_CONFIGURED"
    )
    escaped = dict(row, archive_relative_path="../escape.pdf")
    analyzer = DatabaseCapacityAnalyzer(
        lambda: _Connection(escaped), archive_root=archive
    )
    assert analyzer.verify_external_pdf("doc.pdf")["reason"] == "ARCHIVE_PATH_ESCAPE"
    absent = dict(row, archive_relative_path="absent.pdf")
    analyzer = DatabaseCapacityAnalyzer(
        lambda: _Connection(absent), archive_root=archive
    )
    assert analyzer.verify_external_pdf("doc.pdf")["reason"] == "EXTERNAL_FILE_MISSING"


class _RowsCursor:
    def __init__(self, rows=(), row=None):
        self.rows = list(rows)
        self.row = row

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.row


class _MaintenanceConnection:
    def __init__(self):
        self.sql = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=()):
        self.sql.append(sql)
        if "DELETE FROM admission_import_staging" in sql:
            return _RowsCursor(rows=[(1,), (2,)])
        if sql.lstrip().startswith("DELETE FROM admission_"):
            return _RowsCursor(rows=[(1,), (2,), (3,)])
        return _RowsCursor()


class _MaintenanceAnalyzer(DatabaseCapacityAnalyzer):
    def __init__(self, connection):
        super().__init__(lambda: connection)

    def ensure_schema(self):
        return True

    def analyze_import_staging_cleanup(self, con=None, *, now=None):
        return {
            "batches": [
                {
                    "import_batch_id": "10000000-0000-0000-0000-000000000001",
                    "safe_to_purge": True,
                }
            ]
        }

    def _attention_projection_ready(self, con):
        return True

    def _patient_projection_ready(self, con):
        return True

    def analyze_event_retention(self, con, **kwargs):
        return {"retention_safe": True, "candidate_floor": 5, "max_sequence": 10}


def test_guarded_maintenance_requires_confirmation_and_revalidates():
    con = _MaintenanceConnection()
    analyzer = _MaintenanceAnalyzer(con)
    with pytest.raises(PermissionError):
        analyzer.purge_safe_import_staging()
    assert analyzer.purge_safe_import_staging(confirmed=True) == {
        "batches": 1,
        "rows": 2,
    }
    with pytest.raises(ValueError):
        analyzer.prune_safe_events("UNKNOWN", confirmed=True)
    with pytest.raises(PermissionError):
        analyzer.prune_safe_events("ATTENTION")
    attention = analyzer.prune_safe_events("ATTENTION", confirmed=True)
    patient = analyzer.prune_safe_events("PATIENT_DIRECTORY", confirmed=True)
    assert attention == {"rows": 3, "floor": 5, "checkpoint": 10}
    assert patient == attention


def test_guarded_event_retention_refuses_unready_projection():
    class _Unsafe(_MaintenanceAnalyzer):
        def analyze_event_retention(self, con, **kwargs):
            return {"retention_safe": False, "candidate_floor": 0, "max_sequence": 10}

    with pytest.raises(RuntimeError):
        _Unsafe(_MaintenanceConnection()).prune_safe_events("ATTENTION", confirmed=True)

    class _NothingEligible(_MaintenanceAnalyzer):
        def analyze_event_retention(self, con, **kwargs):
            return {"retention_safe": True, "candidate_floor": 0, "max_sequence": 10}

    assert _NothingEligible(_MaintenanceConnection()).prune_safe_events(
        "ATTENTION", confirmed=True
    ) == {"rows": 0, "floor": 0, "checkpoint": 10}


def test_absent_optional_relations_have_zero_safe_cleanup():
    class _Absent(DatabaseCapacityAnalyzer):
        @staticmethod
        def _relation_exists(con, relation):
            return False

    analyzer = _Absent(lambda: _MaintenanceConnection())
    assert analyzer.analyze_import_staging_cleanup()["safe_bytes"] == 0
    assert analyzer.analyze_pdf_storage()["relation_bytes"] == 0


@pytest.mark.parametrize(
    ("digest_available", "expected_rows"),
    [(False, 0), (True, 2)],
)
def test_pdf_analysis_conservatively_handles_missing_pgcrypto(
    monkeypatch, digest_available, expected_rows
):
    digest_queries = []
    analyzer = DatabaseCapacityAnalyzer(lambda: object())
    monkeypatch.setattr(analyzer, "_relation_exists", lambda *_args: True)
    monkeypatch.setattr(analyzer, "_relation_size", lambda *_args: 512)

    def fake_one(_con, sql, _params=()):
        if "to_regprocedure" in sql:
            return {"available": digest_available}
        if "DIGEST" in sql:
            digest_queries.append(sql)
            return {"rows": 2, "logical_bytes": 256}
        if "LEFT JOIN recibos" in sql:
            return {"receipt_rows": 2, "receipt_bytes": 256}
        return {"rows": 3, "logical_bytes": 384, "unknown_rows": 1}

    monkeypatch.setattr(analyzer, "_one", fake_one)

    result = analyzer.analyze_pdf_storage(object())

    assert result["verified_external_rows"] == expected_rows
    assert result["verified_external_bytes"] == (256 if digest_available else 0)
    assert len(digest_queries) == int(digest_available)


class _SchemaConnection(_MaintenanceConnection):
    def __init__(self, recent=None):
        super().__init__()
        self.recent = recent

    def execute(self, sql, _params=()):
        self.sql.append(sql)
        if "SELECT captured_at FROM database_capacity_history" in sql:
            return _RowsCursor(row=(self.recent,) if self.recent else None)
        return _RowsCursor()


def test_capacity_schema_and_sample_interval_are_idempotent():
    con = _SchemaConnection()
    analyzer = DatabaseCapacityAnalyzer(lambda: con)
    assert analyzer.ensure_schema() is True
    assert any("database_capacity_history" in sql for sql in con.sql)
    snapshot = {
        "database_size_bytes": 100,
        "database_limit_bytes": 500,
        "usage_percent": 20,
        "top_tables": [],
        "component_bytes": {},
    }
    assert analyzer._persist_sample(snapshot, "ADMIN") is True
    recent = _SchemaConnection(datetime.now(UTC) - timedelta(minutes=1))
    assert (
        DatabaseCapacityAnalyzer(lambda: recent)._persist_sample(snapshot, "ADMIN")
        is False
    )


class _AttentionStore:
    def __init__(self):
        self.cursor = 0
        self.rows = {}

    def last_cloud_cursor(self):
        return self.cursor

    def set_last_cloud_cursor(self, value):
        self.cursor = int(value)

    def _save(self, event):
        payload = dict(event.get("payload_json") or {})
        self.rows[str(event["entity_uuid"])] = payload

    def hydrate_remote_events(self, events):
        for event in events:
            self._save(event)
        return len(events)

    def is_remote_event_materialized(self, event):
        return str(event.get("entity_uuid")) in self.rows

    def apply_remote_events(self, events):
        for event in events:
            self._save(event)
            self.cursor = int(event["sequence"])
        return len(events)


class _AttentionCloud:
    def __init__(self):
        self.snapshot = [
            {
                "entity_uuid": "00000000-0000-0000-0000-000000000001",
                "payload_json": {"version": 3, "name": "Actualizada"},
            },
            {
                "entity_uuid": "00000000-0000-0000-0000-000000000002",
                "payload_json": {"version": 4, "is_deleted": True},
            },
        ]
        self.incremental_cursor = None

    def event_window(self):
        return {
            "minimum_available_sequence": 10,
            "checkpoint_sequence": 20,
            "latest_sequence": 21,
        }

    def projection_snapshot_events(self, *, after_global_attention_id, limit):
        return [
            row
            for row in self.snapshot
            if row["entity_uuid"] > after_global_attention_id
        ][:limit]

    def events_after(self, cursor, *, limit):
        self.incremental_cursor = cursor
        if cursor < 21:
            return [
                {
                    "sequence": 21,
                    "entity_uuid": "00000000-0000-0000-0000-000000000003",
                    "payload_json": {"version": 1},
                }
            ]
        return []


def test_attention_old_cursor_bootstraps_tombstones_then_continues_incremental():
    store = _AttentionStore()
    cloud = _AttentionCloud()
    service = AdmissionSyncService(store, cloud)
    assert service.pull_cloud_changes(limit=100) == 3
    assert store.rows["00000000-0000-0000-0000-000000000002"]["is_deleted"] is True
    assert cloud.incremental_cursor == 20
    assert store.cursor == 21


class _ProjectionCursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _ProjectionConnection:
    def __init__(self):
        self.statements = []

    def execute(self, sql, params=()):
        assert sql.count("%s") == len(params), sql
        self.statements.append((sql, params))
        if "SELECT is_deleted,server_revision" in sql:
            return _ProjectionCursor(None)
        if "RETURNING attention_id" in sql and sql.lstrip().startswith("UPDATE"):
            return _ProjectionCursor(None)
        return _ProjectionCursor(None)


def test_projection_materialization_persists_reconstructible_payload():
    con = _ProjectionConnection()
    event = SyncEvent(
        event_uuid="10000000-0000-0000-0000-000000000001",
        entity_type="attention",
        entity_uuid="20000000-0000-0000-0000-000000000001",
        operation="CREATE",
        payload={
            "attention_id": 1,
            "patient_id": 2,
            "name": "Paciente de prueba",
            "service_date": "2026-08-24",
            "service_type": "EMERGENCIA",
            "specialty": "GENERAL",
            "turn_id": 3,
        },
        operational_session_id="30000000-0000-0000-0000-000000000001",
        generation=1,
        device_id="DEVICE-A",
        created_at="2026-08-24T10:00:00+00:00",
    )
    AdmissionCloudRepository._materialize_attention(con, event, 1)
    projection_inserts = [
        (sql, params)
        for sql, params in con.statements
        if sql.lstrip().startswith("INSERT INTO admission_attention_projection")
    ]
    assert len(projection_inserts) == 1
    assert "latest_payload_json" in projection_inserts[0][0]
    assert '"attention_id": 1' in projection_inserts[0][1][-1]


class _PatientLocal:
    def __init__(self):
        self.cursor = 0
        self.rows = {}

    def patient_cursor(self):
        return self.cursor

    def set_patient_cursor(self, value):
        self.cursor = int(value)

    def hydrate_many(self, rows, *, final_sequence=None):
        for row in rows:
            self.rows[str(row["global_patient_id"])] = dict(row)
        if final_sequence is not None:
            self.cursor = int(final_sequence)
        return len(rows)


class _PatientCentral:
    def event_window(self):
        return {
            "minimum_available_sequence": 5,
            "checkpoint_sequence": 8,
            "latest_sequence": 9,
        }

    def snapshot_page(self, *, after_global_patient_id, limit):
        rows = [
            {"global_patient_id": "00000000-0000-0000-0000-000000000010"},
            {
                "global_patient_id": "00000000-0000-0000-0000-000000000011",
                "is_deleted": True,
            },
        ]
        return [r for r in rows if r["global_patient_id"] > after_global_patient_id][
            :limit
        ]

    def events_after(self, sequence, *, limit):
        if sequence < 9:
            return [
                {
                    "sequence": 9,
                    "payload_json": {
                        "global_patient_id": "00000000-0000-0000-0000-000000000012"
                    },
                }
            ]
        return []


def test_patient_new_replica_bootstraps_projection_and_continues_incremental():
    service = PatientDirectoryService.__new__(PatientDirectoryService)
    service.local = _PatientLocal()
    service.central = _PatientCentral()
    service.is_online = lambda: True
    assert service.pull_incremental(limit=100) == 1
    assert service.local.cursor == 9
    assert len(service.local.rows) == 3
    assert (
        service.local.rows["00000000-0000-0000-0000-000000000011"]["is_deleted"] is True
    )


class _CapacityHost(QWidget):
    theme_toggled = Signal(bool)

    def __init__(self):
        super().__init__()
        self.is_dark_mode = False
        self.current_user = {"username": "ADMIN"}


def test_capacity_dialog_progress_and_live_light_dark_theme(monkeypatch):
    import CALCULOS_QT as app_module

    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        app_module.DatabaseCapacityDialog, "_start_analysis", lambda self: None
    )
    host = _CapacityHost()
    dialog = app_module.DatabaseCapacityDialog(host)
    dialog.show()
    application.processEvents()
    snapshot = {
        "database_size_bytes": 450 * MIB,
        "database_limit_bytes": 500 * MIB,
        "usage_percent": 90,
        "free_bytes": 50 * MIB,
        "status": ("CRITICAL", "Crítico"),
        "trend": CapacityTrend(
            "INSUFFICIENT", "Aún no hay suficientes datos.", None, 0
        ),
        "top_tables": [{"name": "pdf_storage", "total_bytes": 140 * MIB}],
        "top_indexes": [],
        "staging": {"batches": []},
        "pdf": {"unknown_rows": 1},
        "sync_events": {"projection_ready": False},
        "patient_events": {"projection_ready": True},
        "safe_recoverable_bytes": 0,
        "after_pdf_bytes": 100 * MIB,
        "after_checkpoint_bytes": 20 * MIB,
    }
    dialog._apply_snapshot(snapshot)
    assert dialog.progress.value() == 900
    assert dialog.progress.format() == "90.0 %"
    assert dialog.close_button.text() == "Cerrar"
    light_style = dialog.close_button.styleSheet()
    host.theme_toggled.emit(True)
    application.processEvents()
    dark_style = dialog.close_button.styleSheet()
    assert light_style != dark_style
    assert dialog.close_button.isVisible() is True
    dialog.close()


def test_maintenance_source_contains_no_unsafe_physical_reclaim():
    root = Path(__file__).resolve().parents[1]
    capacity_source = (root / "database_capacity.py").read_text(encoding="utf-8")
    migration_source = (root / "document_system_migration.py").read_text(
        encoding="utf-8"
    )
    assert "VACUUM FULL" not in capacity_source.upper()
    assert 'execute("TRUNCATE TABLE pdf_storage")' not in migration_source


@pytest.mark.skipif(
    os.environ.get("RUN_REAL_CAPACITY_INTEGRATION") != "1",
    reason="explicit real PostgreSQL capacity integration test",
)
def test_real_capacity_dry_run_preserves_operational_counts():
    from CALCULOS_QT import db_connect

    count_sql = """SELECT
      (SELECT COUNT(*) FROM admission_attention_projection) AS attentions,
      (SELECT COUNT(*) FROM admission_patient_directory) AS patients,
      (SELECT COUNT(*) FROM recibos) AS receipts,
      (SELECT COUNT(*) FROM recibo_items) AS receipt_items,
      (SELECT COUNT(*) FROM admission_import_staging) AS staging_rows"""
    with db_connect() as con:
        before = tuple(con.execute(count_sql).fetchone())
    snapshot = DatabaseCapacityAnalyzer(db_connect).analyze(
        actor="CODEX_CAPACITY_INTEGRATION", persist_sample=True
    )
    with db_connect() as con:
        after = tuple(con.execute(count_sql).fetchone())
    assert snapshot["database_size_bytes"] > 0
    assert snapshot["safe_recoverable_bytes"] == 0
    assert before == after
