import json

import portable_launcher


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
