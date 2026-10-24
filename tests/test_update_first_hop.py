"""Recover extended configuration during the old updater's real health check."""

import os

import pytest

import updater


def installation(tmp_path, timestamp=1000):
    current = tmp_path / "SIGEH"
    previous = tmp_path / f".SIGEH-rollback-{timestamp}"
    for directory in (current, previous):
        (directory / "_internal").mkdir(parents=True, exist_ok=True)
        for name in ("SIGEH.exe", "CALCULOS_QT.exe", "SIGEH_Updater.exe"):
            (directory / name).write_bytes(b"SYNTHETIC")
    return current, previous


def test_old_updater_health_check_recovers_every_extended_location(tmp_path):
    current, previous = installation(tmp_path)
    paths = (
        "app_settings.json",
        "turnos_config.json",
        "security.json",
        "_internal/assets/logo.jpg",
        "data/settings.json",
        "_internal/data/users.sqlite",
    )
    for relative in paths:
        for directory, value in ((previous, b"ORIGINAL"), (current, b"DEFAULT")):
            target = directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(value)
    assert updater.restore_legacy_update_state(current, now=1001) == len(paths)
    assert all((current / relative).read_bytes() == b"ORIGINAL" for relative in paths)
    assert updater.restore_legacy_update_state(current, now=1001) == len(paths)
    assert previous.exists()


@pytest.mark.parametrize("timestamp", [399, 1001])
def test_old_and_future_backups_fail_without_changing_settings(tmp_path, timestamp):
    current, previous = installation(tmp_path, timestamp)
    (previous / "app_settings.json").write_bytes(b"OLD")
    with pytest.raises(RuntimeError, match="intervalo"):
        updater.restore_legacy_update_state(current, now=1000)
    assert not (current / "app_settings.json").exists()


def test_invalid_backup_names_are_ignored(tmp_path):
    current, _ = installation(tmp_path, "invalid")
    assert updater.restore_legacy_update_state(current, now=1000) == 0


def test_backup_at_age_limit_is_recovered(tmp_path):
    current, previous = installation(tmp_path, 400)
    (previous / "app_settings.json").write_bytes(b"OLD")
    assert updater.restore_legacy_update_state(current, now=1000) == 1


def test_ambiguous_backups_fail_before_changing_anything(tmp_path):
    current, previous = installation(tmp_path)
    other = tmp_path / ".SIGEH-rollback-999"
    previous.rename(other)
    installation(tmp_path)
    with pytest.raises(RuntimeError, match="ambigu"):
        updater.restore_legacy_update_state(current, now=1001)
    assert not (current / "app_settings.json").exists()


def test_partial_backup_is_ignored(tmp_path):
    current, previous = installation(tmp_path)
    (previous / "SIGEH.exe").unlink()
    assert updater.restore_legacy_update_state(current, now=1001) == 0


def test_recovery_failure_propagates_to_trigger_old_updater_rollback(
    tmp_path, monkeypatch
):
    current, previous = installation(tmp_path)
    (previous / "app_settings.json").write_bytes(b"OLD")
    monkeypatch.setattr(updater, "merge_preserved", lambda *_: None)
    with pytest.raises(RuntimeError, match="conserv"):
        updater.restore_legacy_update_state(current, now=1001)


def test_normal_self_test_without_rollback_does_not_modify_installation(tmp_path):
    current = tmp_path / "SIGEH"
    current.mkdir()
    assert updater.restore_legacy_update_state(current, now=1001) == 0
    assert list(current.iterdir()) == []


def test_redirected_backup_is_ignored(tmp_path):
    current, previous = installation(tmp_path)
    other = tmp_path / "other"
    previous.rename(other)
    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(previous), str(other)],
            check=True,
            capture_output=True,
        )
    else:
        previous.symlink_to(other, target_is_directory=True)
    assert updater.restore_legacy_update_state(current, now=1001) == 0


def test_default_clock_recovers_recent_backup(tmp_path, monkeypatch):
    current, previous = installation(tmp_path)
    (previous / "app_settings.json").write_bytes(b"ORIGINAL")
    monkeypatch.setattr(updater.time, "time", lambda: 1001)
    assert updater.restore_legacy_update_state(current) == 1


@pytest.mark.parametrize("frozen", [False, True])
def test_launcher_health_check_runs_recovery_before_bootstrap(
    tmp_path, monkeypatch, frozen
):
    import runpy
    import sys
    from unittest.mock import Mock
    import portable_launcher

    recovery, bootstrap = Mock(), Mock(return_value=0)
    monkeypatch.setattr(updater, "restore_legacy_update_state", recovery)
    monkeypatch.setattr(portable_launcher, "main", bootstrap)
    monkeypatch.setattr(sys, "argv", ["SIGEH.exe", "--self-test"])
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SIGEH.exe"))
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "lanzador.py"
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(source), run_name="__main__")
    assert exit_info.value.code == 0
    assert recovery.call_count == int(frozen)
    bootstrap.assert_called_once_with()
    if frozen:
        recovery.assert_called_once_with(tmp_path)


def test_launcher_recovery_failure_prevents_successful_health_check(
    tmp_path, monkeypatch
):
    import runpy
    import sys
    from pathlib import Path
    from unittest.mock import Mock
    import portable_launcher

    def failure(_):
        raise RuntimeError("preservation failed")

    bootstrap = Mock()
    monkeypatch.setattr(updater, "restore_legacy_update_state", failure)
    monkeypatch.setattr(portable_launcher, "main", bootstrap)
    monkeypatch.setattr(sys, "argv", ["SIGEH.exe", "--self-test"])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "SIGEH.exe"))
    with pytest.raises(RuntimeError, match="preservation failed"):
        runpy.run_path(
            str(Path(__file__).resolve().parents[1] / "lanzador.py"),
            run_name="__main__",
        )
    bootstrap.assert_not_called()
