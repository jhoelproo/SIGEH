from __future__ import annotations

import sqlite3
from pathlib import Path

from admission_source.emergency_core import db_migrations, paths


def test_new_distribution_data_root_is_internal_and_not_a_legacy_global_path(
    monkeypatch, tmp_path: Path
):
    distribution = tmp_path / "SistemaHospital"
    monkeypatch.delenv("EMERGENCIAS_DATA_DIR", raising=False)
    monkeypatch.setattr(paths, "source_or_executable_dir", lambda: distribution)

    root = paths.data_root()

    assert root == distribution / "_internal" / "data"
    assert root.is_dir()
    assert root.parent == distribution / "_internal"


def test_explicit_override_remains_available_only_for_controlled_migration_or_tests(
    monkeypatch, tmp_path: Path
):
    override = tmp_path / "isolated-test-replica"
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(override))

    assert paths.data_root() == override.resolve()


def test_latest_local_schema_skips_ddl_and_integrity_scan_on_normal_startup(
    tmp_path: Path,
):
    database = tmp_path / "admission.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE schema_version(id INTEGER PRIMARY KEY,version INTEGER NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_version(id,version) VALUES(1,?)",
            (db_migrations.LATEST_SCHEMA_VERSION,),
        )

    class Backup:
        db_path = database

    result = db_migrations.migrate_database(database, Backup())

    assert result == {
        "created": False,
        "migrated": False,
        "version": db_migrations.LATEST_SCHEMA_VERSION,
        "startup_checked": True,
    }
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pacientes'"
        ).fetchone() is None
