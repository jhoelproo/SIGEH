import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
import pytest

from admission_source.emergency_core.backup import (
    BackupError,
    BackupManager,
    DailyBackupSchedule,
)
from tests.test_admission_hybrid_offline_sync import (
    create_v15_database,
    create_attention,
    operational_session,
)
from admission_hybrid import OfflineAdmissionStore


@pytest.mark.parametrize("damaged", [False, True])
def test_new_backup_runs_one_final_integrity_check(tmp_path, monkeypatch, damaged):
    database = tmp_path / "admission.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE pending(id INTEGER PRIMARY KEY)")
    manager = BackupManager(database, tmp_path / "backups")
    original = manager.check_database
    checked = []

    def check_before_publishing(path):
        checked.append(path)
        assert not (path.parent / "manifest.json").exists()
        if damaged:
            raise BackupError("synthetic integrity failure")
        original(path)

    monkeypatch.setattr(manager, "check_database", check_before_publishing)
    if damaged:
        with pytest.raises(BackupError, match="synthetic integrity failure"):
            manager.create("respaldo_manual")
        assert manager.list_backups() == []
    else:
        folder = manager.create("respaldo_manual")
        assert manager.list_backups() == [folder]
    assert len(checked) == 1


@pytest.mark.parametrize(
    "case,message",
    [
        ("bad_format", "manifiesto del respaldo"),
        ("bad_entry", "entrada de archivo"),
        ("bad_size", "metadatos"),
        ("negative_size", "metadatos"),
        ("traversal", "fuera del respaldo"),
        ("duplicate", "repite el archivo"),
        ("wrong_database", "no figura"),
        ("redirected_member", "apunta fuera"),
        ("missing_member", "Falta o cambio"),
        ("invalid_hash", "metadatos"),
        ("hash_mismatch", "El hash no coincide"),
    ],
)
def test_backup_verification_rejects_invalid_members(
    tmp_path, monkeypatch, case, message
):
    database = tmp_path / "admission.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE pending(id INTEGER PRIMARY KEY)")
    manager = BackupManager(database, tmp_path / "backups")
    folder = manager.create("respaldo_manual")
    metadata_path = folder / "manifest.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    item = metadata["files"][0]

    match case:
        case "bad_format":
            metadata["format"] = 0
        case "bad_entry":
            metadata["files"] = [None]
        case "bad_size":
            item.pop("bytes")
        case "negative_size":
            item["bytes"] = -1
        case "traversal":
            item["name"] = "../outside.db"
        case "duplicate":
            metadata["files"].append(item)
        case "wrong_database":
            metadata["database"] = "missing.db"
        case "redirected_member":
            resolve = Path.resolve
            monkeypatch.setattr(
                Path,
                "resolve",
                lambda path, **kwargs: (
                    tmp_path / "outside.db"
                    if path == folder / database.name
                    else resolve(path, **kwargs)
                ),
            )
        case "missing_member":
            (folder / database.name).unlink()
        case "invalid_hash":
            item["sha256"] = "invalid"
        case "hash_mismatch":
            copy = folder / database.name
            data = copy.read_bytes()
            copy.write_bytes(bytes([data[0] ^ 1]) + data[1:])

    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(BackupError, match=message):
        manager.verify(folder)


def test_corrupted_daily_copy_is_replaced_with_verified_backup(tmp_path):
    database = tmp_path / "admission.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE sync_outbox(id TEXT PRIMARY KEY)")
        connection.execute("INSERT INTO sync_outbox VALUES('pending-event')")
    manager = BackupManager(database, tmp_path / "backups")
    first = manager.ensure_daily()
    (first / database.name).write_bytes(b"damaged")
    replacement = manager.ensure_daily()
    assert replacement is not None and replacement != first
    manager.verify(replacement)
    with sqlite3.connect(replacement / database.name) as connection:
        assert (
            connection.execute("SELECT id FROM sync_outbox").fetchone()[0]
            == "pending-event"
        )


def test_daily_backup_reopens_and_restores_attention_and_pending_event(tmp_path):
    database = tmp_path / "admission.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(operational_session(), device_id="PC-1")
    create_attention(database, 1)
    event = store.pending_events(10)[0]
    manager = BackupManager(database, tmp_path / "backups")
    backup = manager.ensure_daily()
    assert manager.ensure_daily() is None
    create_attention(database, 2)
    safety = manager.restore_database(backup)
    manager.verify(safety)
    reopened = OfflineAdmissionStore(database)
    assert reopened.pending_count() == 1
    assert reopened.pending_events(10)[0].event_uuid == event.event_uuid
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1
    with sqlite3.connect(safety / database.name) as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 2


def test_daily_schedule_is_bounded_and_retries_failure_without_sleep():
    now = [0.0]
    manager = Mock()
    manager.ensure_daily.side_effect = [OSError("disk unavailable"), "verified", None]
    schedule = DailyBackupSchedule(manager, clock=lambda: now[0])
    with pytest.raises(OSError):
        schedule.run_due()
    now[0] = 59.99
    assert schedule.run_due() is None
    assert manager.ensure_daily.call_count == 1
    now[0] = 60
    assert schedule.run_due() == "verified"
    now[0] = 3659.99
    assert schedule.run_due() is None
    assert manager.ensure_daily.call_count == 2
    now[0] = 3660
    assert schedule.run_due() is None
    assert manager.ensure_daily.call_count == 3
