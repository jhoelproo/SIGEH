from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path

import pytest

import database_config
import qa.collect_multistation_evidence as collector
from qa.collect_multistation_evidence import collect_local_snapshot


def _create_local_replica(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """CREATE TABLE admission_operational_cache(
                   singleton INTEGER PRIMARY KEY,
                   operational_source_id TEXT,turn_id INTEGER,generation INTEGER
               );
               CREATE TABLE sync_runtime_context(
                   singleton INTEGER PRIMARY KEY,device_id TEXT,
                   operational_source_id TEXT,operational_turn_id INTEGER,
                   generation INTEGER
               );
               CREATE TABLE atenciones(
                   global_attention_id TEXT,operational_source_id TEXT,
                   operational_turn_id INTEGER,generation INTEGER,
                   sync_state TEXT,origin_device_id TEXT,
                   device_local_sequence INTEGER,is_deleted INTEGER,estado TEXT
               );
               CREATE TABLE sync_outbox(
                   event_uuid TEXT,entity_type TEXT,entity_uuid TEXT,
                   operation TEXT,sync_status TEXT,retry_count INTEGER,
                   operational_source_id TEXT,turn_id INTEGER,generation INTEGER,
                   device_id TEXT,created_at TEXT
               );
               INSERT INTO admission_operational_cache VALUES(1,'source-a',77,4);
               INSERT INTO sync_runtime_context VALUES(1,'PC-A','source-a',77,4);
               INSERT INTO atenciones VALUES
                 ('00000000-0000-0000-0000-000000000001','source-a',77,4,
                  'SYNCED','PC-A',1,0,'ACTIVA'),
                 ('00000000-0000-0000-0000-000000000002','source-a',77,4,
                  'PENDING','PC-A',2,0,'ACTIVA'),
                 ('00000000-0000-0000-0000-000000000003','source-a',77,4,
                  'SYNCED','PC-A',3,1,'ANULADA');
               INSERT INTO sync_outbox VALUES(
                 'event-2','attention',
                 '00000000-0000-0000-0000-000000000002','UPSERT','PENDING',0,
                 'source-a',77,4,'PC-A','2026-08-30T00:00:00Z'
               );"""
        )


def test_local_evidence_contains_only_active_global_ids_and_pending_trace(tmp_path):
    database = tmp_path / "pacientes.db"
    _create_local_replica(database)

    evidence = collect_local_snapshot(
        database,
        station="HOSPITAL",
        trace_global_attention_id="00000000-0000-0000-0000-000000000002",
    )

    assert evidence["sqlite_quick_check"] == "ok"
    assert evidence["device_id"] == "PC-A"
    assert evidence["identity"] == {
        "operational_source_id": "source-a",
        "turn_id": 77,
        "generation": 4,
        "operational_revision": 0,
    }
    assert evidence["local_count"] == 2
    assert evidence["pending_count"] == 1
    assert evidence["global_attention_ids"] == [
        "00000000-0000-0000-0000-000000000001",
        "00000000-0000-0000-0000-000000000002",
    ]
    assert evidence["trace"]["attention"]["sync_state"] == "PENDING"
    assert evidence["trace"]["outbox"][0]["sync_status"] == "PENDING"
    assert collect_local_snapshot(database, station="HOSPITAL")["trace"] == {}


def test_local_evidence_rejects_a_missing_replica(tmp_path):
    with pytest.raises(FileNotFoundError, match="No existe la réplica local"):
        collect_local_snapshot(tmp_path / "missing.db", station="SECONDARY")


def test_standalone_collector_bootstraps_the_repository_import_path(monkeypatch):
    repository_root = str(Path(collector.__file__).resolve().parents[1])
    monkeypatch.setattr(
        sys,
        "path",
        [path for path in sys.path if str(Path(path).resolve()) != repository_root],
    )
    spec = importlib.util.spec_from_file_location(
        "standalone_multistation_evidence",
        collector.__file__,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert sys.path[0] == repository_root


def test_central_evidence_collects_exact_uuid_set_primary_and_trace(monkeypatch):
    global_id = "00000000-0000-0000-0000-000000000001"

    class Cursor:
        def __init__(self):
            self.query = ""

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, query, _params=None):
            self.query = " ".join(str(query).split())

        def fetchone(self):
            if "FROM admission_operational_sessions" in self.query:
                return {
                    "operational_source_id": "source-a",
                    "turn_id": 77,
                    "generation": 4,
                    "operational_revision": 9,
                    "status": "ACTIVE",
                    "primary_device_id": "PC-A",
                }
            if "COUNT(*) AS active_primary_count" in self.query:
                return {"active_primary_count": 1}
            return {
                "global_attention_id": global_id,
                "operational_source_id": "source-a",
                "turn_id": 77,
                "generation": 4,
                "source_status": "ACTIVA",
                "is_deleted": False,
                "server_revision": 3,
            }

        def fetchall(self):
            if "ORDER BY global_attention_id::TEXT" in self.query:
                return [{"global_attention_id": global_id}]
            return [
                {
                    "event_uuid": "event-1",
                    "operation": "UPSERT",
                    "generation": 4,
                    "turn_id": 77,
                    "operational_source_id": "source-a",
                    "device_id": "PC-A",
                    "server_revision": 3,
                }
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def set_session(self, **_kwargs):
            return None

        def cursor(self, **_kwargs):
            return Cursor()

    psycopg2 = types.ModuleType("psycopg2")
    psycopg2.connect = lambda *_args, **_kwargs: Connection()
    extras = types.ModuleType("psycopg2.extras")
    extras.RealDictCursor = object
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2)
    monkeypatch.setitem(sys.modules, "psycopg2.extras", extras)

    evidence = collector.collect_central_snapshot(
        "postgresql://protected",
        {"operational_source_id": "source-a", "turn_id": 77},
        trace_global_attention_id=global_id,
    )

    assert evidence["central_count"] == 1
    assert evidence["active_primary_count"] == 1
    assert evidence["global_attention_ids"] == [global_id]
    assert evidence["trace"]["projection"]["global_attention_id"] == global_id
    assert evidence["trace"]["events"][0]["event_uuid"] == "event-1"
    assert collector.collect_central_snapshot(
        "postgresql://protected",
        {"operational_source_id": "source-a", "turn_id": 77},
    )["trace"] == {}


def test_build_evidence_uses_protected_connection_and_compares_identity(monkeypatch):
    local = {
        "identity": {
            "operational_source_id": "source-a",
            "turn_id": 77,
            "generation": 4,
            "operational_revision": 9,
        },
        "global_attention_ids": ["global-1"],
    }
    central = {
        "active_operational_state": {
            "operational_source_id": "source-a",
            "turn_id": 77,
        },
        "global_attention_ids": ["global-1"],
    }
    monkeypatch.setattr(collector, "collect_local_snapshot", lambda *_a, **_k: local)
    monkeypatch.setattr(collector, "collect_central_snapshot", lambda *_a, **_k: central)
    monkeypatch.setattr(
        database_config,
        "resolve_database_url",
        lambda _root: "postgresql://protected",
    )

    evidence = collector.build_evidence(
        Path("replica.db"),
        station="HOSPITAL",
        bundle_root=Path("bundle"),
    )

    assert evidence["identity_matches_active"] is True
    assert evidence["dataset_matches_central"] is True
    central["global_attention_ids"] = ["other"]
    assert collector.build_evidence(
        Path("replica.db"), station="SECONDARY", bundle_root=Path("bundle")
    )["dataset_matches_central"] is False


def test_build_evidence_requires_a_resolved_central_connection(monkeypatch):
    monkeypatch.setattr(
        collector,
        "collect_local_snapshot",
        lambda *_a, **_k: {"identity": {}, "global_attention_ids": []},
    )
    monkeypatch.setattr(database_config, "resolve_database_url", lambda _root: "")

    with pytest.raises(RuntimeError, match="conexión central protegida"):
        collector.build_evidence(
            Path("replica.db"), station="HOSPITAL", bundle_root=Path("bundle")
        )


def test_main_writes_atomic_json_and_reports_identity_mismatch(tmp_path, monkeypatch):
    output = tmp_path / "evidence" / "secondary.json"
    monkeypatch.setattr(
        collector,
        "build_evidence",
        lambda *_a, **_k: {
            "identity_matches_active": False,
            "dataset_matches_central": False,
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "collect_multistation_evidence.py",
            "--local-db",
            str(tmp_path / "replica.db"),
            "--station",
            "SECONDARY",
            "--bundle-root",
            str(tmp_path),
            "--output",
            str(output),
        ],
    )

    assert collector.main() == 2
    assert output.is_file()
    assert '"identity_matches_active": false' in output.read_text(encoding="utf-8")
    assert not output.with_suffix(".json.tmp").exists()
