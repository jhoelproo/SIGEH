from __future__ import annotations

import sqlite3
from pathlib import Path

from admission_source.emergency_core import db_migrations, paths
from admission_source.emergency_core.backup import BackupManager


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


def test_v18_migration_adds_rectification_audit_columns_after_verified_backup(
    tmp_path: Path,
):
    database = tmp_path / "admission.db"
    backup_manager = BackupManager(database, tmp_path / "backups")
    db_migrations.migrate_database(database, backup_manager)
    correction_columns = (
        "correction_reason",
        "correction_actor",
        "correction_at",
        "correction_before_json",
        "correction_after_json",
        "correction_changed_fields_json",
    )
    with sqlite3.connect(database) as connection:
        for column in correction_columns:
            connection.execute(f"ALTER TABLE atenciones DROP COLUMN {column}")
        connection.execute("UPDATE schema_version SET version=18 WHERE id=1")

    result = db_migrations.migrate_database(database, backup_manager)

    assert result["migrated"] is True
    assert result["from_version"] == 18
    assert result["to_version"] == db_migrations.LATEST_SCHEMA_VERSION
    backup_folder = Path(result["backup"])
    assert backup_folder.is_dir()
    assert backup_manager.verify(backup_folder)["database"] == database.name
    with sqlite3.connect(database) as connection:
        migrated_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(atenciones)")
        }
        migration = connection.execute(
            "SELECT name,details_json FROM schema_migrations WHERE version=19"
        ).fetchone()
    assert set(correction_columns) <= migrated_columns
    assert migration == (
        "attention_rectification_audit_metadata",
        '{"from_version": 18}',
    )
