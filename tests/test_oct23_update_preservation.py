"""Local configuration wins over defaults in update and rollback."""

import json

import pytest

import updater


@pytest.mark.parametrize(
    "relative",
    [
        "settings.json",
        "app_settings.json",
        "turnos_config.json",
        "security.json",
        ".env",
        "database_url.protected",
        "_internal/data/config/logo.png",
        "_internal/data/users.sqlite",
        "assets/logo.jpg",
        "_internal/assets/logo.jpg",
    ],
)
def test_update_preserves_configuration_even_if_payload_contains_defaults(
    tmp_path, relative
):
    old, new = tmp_path / "old", tmp_path / "new"
    source, target = old / relative, new / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"EXISTING HOSPITAL CONFIGURATION")
    target.write_bytes(b"PACKAGE DEFAULT")
    before = updater.snapshot_preserved_local_state(old)
    assert relative in before
    updater.merge_preserved(old, new)
    updater.verify_preserved_local_state(before, new)
    assert target.read_bytes() == source.read_bytes()


@pytest.mark.parametrize("failure", [False, True])
def test_update_and_rollback_keep_settings_logo_and_users(
    tmp_path, monkeypatch, failure
):
    install, payload = tmp_path / "SIGEH", tmp_path / "payload"
    for directory, marker in ((install, b"OLD"), (payload, b"NEW")):
        (directory / "_internal").mkdir(parents=True)
        for name in ("SIGEH.exe", "CALCULOS_QT.exe", "SIGEH_Updater.exe"):
            (directory / name).write_bytes(marker)
    originals = {
        "app_settings.json": b'{"hospital_name":"PRUEBA","users":["user"]}',
        "turnos_config.json": b'{"start":"08:00","end":"08:00"}',
        "_internal/assets/logo.jpg": b"CUSTOM LOGO",
        "_internal/data/users.sqlite": b"SYNTHETIC USERS",
    }
    for name, contents in originals.items():
        path = install / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contents)
    instruction = tmp_path / "update.json"
    instruction.write_text(
        json.dumps(
            {
                "product": "SIGEH",
                "version": "test",
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        )
    )
    monkeypatch.setattr(updater, "update_storage_root", lambda: tmp_path / "updates")
    monkeypatch.setattr(updater.time, "sleep", lambda _: None)
    monkeypatch.setattr(updater, "health_check_install", lambda _: not failure)
    starts = []
    monkeypatch.setattr(updater, "start_launcher", starts.append)
    assert updater.apply_update(instruction, 0) == int(failure)
    assert (install / "SIGEH.exe").read_bytes() == (b"OLD" if failure else b"NEW")
    assert starts == [install]
    assert all(
        (install / name).read_bytes() == contents
        for name, contents in originals.items()
    )


def test_bundled_executables_are_not_preserved(tmp_path):
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"program")
    assert updater.snapshot_preserved_local_state(tmp_path) == {}


def test_missing_install_has_empty_snapshot(tmp_path):
    assert updater.snapshot_preserved_local_state(tmp_path / "absent") == {}
