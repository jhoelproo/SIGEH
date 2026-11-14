import json

import pytest

import portable_launcher
from database_config import (
    BUNDLED_DATABASE_FILE,
    SEALED_DATABASE_FILE,
    read_sealed_database_url,
    write_bundled_database_url,
)


@pytest.fixture(autouse=True)
def isolated_connection_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "profile"))


def test_launcher_restores_protected_connection_after_install_moves(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    first = tmp_path / "original"
    second = tmp_path / "replacement"
    for root in (first, second):
        (root / "_internal").mkdir(parents=True)
        (root / "CALCULOS_QT.exe").write_bytes(b"synthetic")
    bundle = first / "_internal" / BUNDLED_DATABASE_FILE
    url = "postgresql://synthetic:synthetic@central.example/hospital"
    write_bundled_database_url(bundle, url)
    calls = []
    monkeypatch.setattr(
        portable_launcher.subprocess, "Popen", lambda *a, **kw: calls.append(kw)
    )
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: first)
    assert portable_launcher.main() == 0
    bundle.unlink()
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: second)
    assert portable_launcher.main() == 0
    assert calls[-1]["env"]["DATABASE_URL"] == url
    assert read_sealed_database_url(second / SEALED_DATABASE_FILE) == url
    assert "user_protected_backup" in (second / "lanzador_log.txt").read_text(encoding="utf8")


def test_existing_bundle_still_launches_when_local_profile_is_unavailable(
    tmp_path,
):
    url = "postgresql://synthetic:synthetic@central.example/hospital"
    bundle = tmp_path / BUNDLED_DATABASE_FILE
    write_bundled_database_url(bundle, url)
    for profile in ("", str(bundle)):
        environment = {"LOCALAPPDATA": profile}
        ready, source = portable_launcher._prepare_connection(tmp_path, environment)
        assert ready
        assert source["config_source"] == "portable_bundle"
        assert environment["DATABASE_URL"] == url
    assert "CONFIG_BACKUP" in (tmp_path / "lanzador_log.txt").read_text(encoding="utf8")


def test_launcher_uses_bundled_database_configuration_without_env_file(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    executable = tmp_path / "CALCULOS_QT.exe"
    executable.write_bytes(b"test")
    (tmp_path / "_internal").mkdir()
    captured = {}

    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda environment, base_dir: environment.setdefault(
            "DATABASE_URL", "postgresql://bundled"
        ),
    )

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(portable_launcher.subprocess, "Popen", fake_popen)

    assert portable_launcher.main() == 0
    assert captured["command"] == [str(executable)]
    assert captured["cwd"] == str(tmp_path)
    assert captured["env"]["DATABASE_URL"] == "postgresql://bundled"
    assert not (tmp_path / ".env").exists()


def test_launcher_fails_when_database_configuration_cannot_be_resolved(
    tmp_path, monkeypatch
):
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"test")
    (tmp_path / "_internal").mkdir()
    errors = []

    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda environment, base_dir: None,
    )
    monkeypatch.setattr(portable_launcher, "show_error", errors.append)

    assert portable_launcher.main() == 5
    assert errors and "base de datos central" in errors[0]
    events = [
        json.loads(line)
        for line in (tmp_path / "lanzador_log.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.startswith("{")
    ]
    backend_event = next(
        event for event in events if event["event"] == "BACKEND_BOOTSTRAP"
    )
    assert backend_event["status"] == "FAIL"
    assert backend_event["error_code"] == "CONFIGURATION_MISSING"
    assert backend_event["credentials_present"] is False


def test_launcher_recovers_existing_install_for_direct_download(tmp_path, monkeypatch):
    executable = tmp_path / "CALCULOS_QT.exe"
    executable.write_bytes(b"test")
    (tmp_path / "_internal").mkdir()
    captured = {}
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda environment, base_dir: "",
    )
    monkeypatch.setattr(
        portable_launcher,
        "recover_database_url_from_existing_install",
        lambda root, environment: "postgresql://recovered",
    )

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(portable_launcher.subprocess, "Popen", fake_popen)

    assert portable_launcher.main() == 0
    assert captured["env"]["DATABASE_URL"] == "postgresql://recovered"


@pytest.mark.parametrize("error_type", [OSError, RuntimeError, ValueError])
def test_recovery_persistence_failure_does_not_block_launch_or_expose_credentials(
    tmp_path, monkeypatch, error_type
):
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"synthetic")
    (tmp_path / "_internal").mkdir()
    secret = "postgresql://synthetic:never-log-this@central.example/hospital"
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(
        portable_launcher, "install_database_url_for_child", lambda *_args, **_kw: ""
    )
    monkeypatch.setattr(
        portable_launcher,
        "recover_database_url_from_existing_install",
        lambda *_args, **_kw: secret,
    )

    def fail(*_args):
        raise error_type(secret)

    monkeypatch.setattr(portable_launcher, "write_sealed_database_url", fail)
    captured = {}
    monkeypatch.setattr(
        portable_launcher.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command, **kwargs),
    )
    assert portable_launcher.main() == 0
    assert captured["env"]["DATABASE_URL"] == secret
    log = (tmp_path / "lanzador_log.txt").read_text(encoding="utf-8")
    assert "CONFIGURATION_WRITE_FAILED" in log
    assert error_type.__name__ in log
    assert secret not in log
    assert "never-log-this" not in log


def test_launcher_recovers_real_bundle_from_versioned_previous_install(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    one_drive = tmp_path / "OneDrive"
    downloaded = one_drive / "Desktop" / "SIGEH-1.2.5-windows-x64" / "SIGEH"
    previous = one_drive / "Desktop" / "SIGEH-1.2.4-windows-x64" / "SIGEH" / "_internal"
    downloaded.mkdir(parents=True)
    previous.mkdir(parents=True)
    (downloaded / "CALCULOS_QT.exe").write_bytes(b"synthetic")
    (downloaded / "_internal").mkdir()
    expected = "postgresql://synthetic-user:synthetic-pass@central.example/hospital"
    write_bundled_database_url(previous / BUNDLED_DATABASE_FILE, expected)
    captured = {}
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: downloaded)
    monkeypatch.setattr(
        portable_launcher.subprocess,
        "Popen",
        lambda command, **kwargs: captured.update(command=command, **kwargs),
    )

    assert portable_launcher.main() == 0
    assert captured["env"]["DATABASE_URL"] == expected
    assert captured["command"] == [str(downloaded / "CALCULOS_QT.exe")]
    assert read_sealed_database_url(downloaded / SEALED_DATABASE_FILE) == expected
    (previous / BUNDLED_DATABASE_FILE).unlink()
    captured.clear()
    assert portable_launcher.main() == 0
    assert captured["env"]["DATABASE_URL"] == expected


def test_self_test_validates_install_without_launching(tmp_path, monkeypatch):
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"test")
    (tmp_path / "_internal").mkdir()
    launched = []
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda environment, base_dir: True,
    )
    monkeypatch.setattr(portable_launcher.sys, "argv", ["SIGEH.exe", "--self-test"])
    monkeypatch.setattr(
        portable_launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )

    assert portable_launcher.main() == 0
    assert launched == []


def test_self_test_rejects_missing_internal_directory(tmp_path, monkeypatch):
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"test")
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(portable_launcher.sys, "argv", ["SIGEH.exe", "--self-test"])

    assert portable_launcher.main() == 3


def test_launcher_logs_failed_launch_with_traceback_and_without_database_url(
    tmp_path, monkeypatch
):
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"test")
    (tmp_path / "_internal").mkdir()
    secret = "postgresql://user:do-not-log@central.example/hospital"
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda environment, base_dir: environment.setdefault("DATABASE_URL", secret),
    )
    monkeypatch.setattr(
        portable_launcher,
        "describe_database_configuration",
        lambda *_args, **_kwargs: {
            "config_source": "portable_bundle",
            "project_ref_redacted": "cent…mple",
            "host_redacted": "cent…mple",
            "port": 5432,
            "database": "hospital",
            "ssl_mode": "require",
            "credentials_present": True,
        },
    )
    monkeypatch.setattr(
        portable_launcher.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("launch failed")),
    )
    monkeypatch.setattr(portable_launcher, "show_error", lambda _message: None)

    assert portable_launcher.main() == 6
    log_text = (tmp_path / "lanzador_log.txt").read_text(encoding="utf-8")
    assert "launch_main" in log_text
    assert "OSError" in log_text
    assert "traceback" in log_text
    assert secret not in log_text
    assert "do-not-log" not in log_text
