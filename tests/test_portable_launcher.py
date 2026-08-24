import portable_launcher


def test_launcher_uses_bundled_database_configuration_without_env_file(
    tmp_path, monkeypatch
):
    executable = tmp_path / "CALCULOS_QT.exe"
    executable.write_bytes(b"test")
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
