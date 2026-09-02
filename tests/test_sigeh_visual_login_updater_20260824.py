import json
import inspect
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import pytest

import CALCULOS_QT as shell
from ADMISION_PYSIDE6_V15 import qt_compat
from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App
import lanzador
import updater
from release_packaging import prepare_release, validate_dist
import portable_launcher
import release_packaging


def _app():
    return QApplication.instance() or QApplication([])


def test_login_and_completer_follow_light_and_dark_tokens(monkeypatch):
    application = _app()
    monkeypatch.setattr(shell, "list_login_usernames", lambda: ["ADMIN", "AUX TEST"])
    dialog = shell.LoginDialog(is_dark=False)
    try:
        for is_dark in (False, True, False):
            dialog.apply_theme(is_dark)
            tokens = shell.visual_theme_tokens(is_dark)
            assert tokens["window_bg"] in dialog.styleSheet()
            popup_qss = dialog.username_completer.popup().styleSheet()
            assert tokens["popup_bg"] in popup_qss
            assert tokens["selection_bg"] in popup_qss
            assert tokens["text_primary"] in popup_qss
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_forgot_password_is_mouse_and_keyboard_accessible(monkeypatch):
    application = _app()
    monkeypatch.setattr(shell, "list_login_usernames", lambda: ["ADMIN"])
    opened = []
    monkeypatch.setattr(
        shell.RecoverPasswordDialog,
        "exec",
        lambda self: opened.append(self._is_dark) or 0,
    )
    dialog = shell.LoginDialog(is_dark=True)
    dialog.show()
    try:
        assert dialog.btn_recover.focusPolicy() == Qt.StrongFocus
        assert dialog.btn_recover.cursor().shape() == Qt.PointingHandCursor
        QTest.mouseClick(dialog.btn_recover, Qt.LeftButton)
        dialog.btn_recover.setFocus()
        QTest.keyClick(dialog.btn_recover, Qt.Key_Return)
        QTest.keyClick(dialog.btn_recover, Qt.Key_Space)
        assert opened == [True, True, True]
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_recovery_dialog_uses_same_theme_and_popup(monkeypatch):
    application = _app()
    monkeypatch.setattr(shell, "list_login_usernames", lambda: ["ADMIN"])
    dialog = shell.RecoverPasswordDialog(is_dark=True)
    try:
        tokens = shell.visual_theme_tokens(True)
        assert tokens["window_bg"] in dialog.styleSheet()
        assert tokens["popup_bg"] in dialog.username_completer.popup().styleSheet()
        assert tokens["button_success_bg"] in dialog.btn_reset.styleSheet()
        dialog.apply_theme(False)
        light = shell.visual_theme_tokens(False)
        assert light["window_bg"] in dialog.styleSheet()
    finally:
        dialog.close()
        dialog.deleteLater()
        application.processEvents()


def test_config_canvas_and_disabled_buttons_follow_live_host_theme():
    application = _app()
    root = qt_compat.Window(owns_application_loop=False)
    canvas = qt_compat.Canvas(root, background="#07111f")
    button = qt_compat.Button(
        root, text="Confirmar", bootstyle="primary", state="disabled"
    )
    admission = object.__new__(App)
    admission.root = root
    admission.app_settings = {"font_size": 11}
    admission._host_theme_controlled = True
    admission._host_theme_is_dark = False
    admission._host_visual_theme = shell.visual_theme_tokens(False)
    admission._configurar_estilos_desde_preferencias()
    admission._aplicar_preferencias_a_widgets(root)
    light = shell.visual_theme_tokens(False)
    assert light["root"] in canvas.viewport().styleSheet()
    assert light["input_disabled_bg"] in button.styleSheet()
    admission._host_theme_is_dark = True
    admission._host_visual_theme = shell.visual_theme_tokens(True)
    admission._configurar_estilos_desde_preferencias()
    admission._aplicar_preferencias_a_widgets(root)
    dark = shell.visual_theme_tokens(True)
    assert dark["root"] in canvas.viewport().styleSheet()
    assert dark["input_disabled_bg"] in button.styleSheet()
    root.close()
    root.deleteLater()
    application.processEvents()


def _write_payload(path: Path, marker: bytes = b"new"):
    (path / "_internal").mkdir(parents=True)
    for name in ("SIGEH.exe", "SIGEH_Updater.exe", "CALCULOS_QT.exe"):
        (path / name).write_bytes(marker)


def test_canonical_updater_names_and_onefile_spec():
    assert lanzador.UPDATER_NAME == "SIGEH_Updater.exe"
    assert lanzador.LAUNCHER_NAME == "SIGEH.exe"
    source = Path("build_updater.spec").read_text(encoding="utf-8")
    assert 'name="SIGEH_Updater"' in source
    assert "COLLECT(" not in source
    v15 = Path("ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py").read_text(
        encoding="utf-8"
    )
    assert "ACTUALIZADOR.exe" not in v15
    assert '"SIGEH_Updater.exe"' not in v15
    assert "buscar_actualizaciones" not in v15
    assert "return run_update_check_ui()" in inspect.getsource(lanzador.main)
    launcher = Path("lanzador.py").read_text(encoding="utf-8")
    assert '_EARLY_ARGS == ["--self-test"]' in launcher
    assert "_fast_launch_main_before_gui_imports()" in launcher


def test_updater_replaces_onedir_preserves_state_and_keeps_local_backup(
    tmp_path, monkeypatch
):
    install = tmp_path / "SIGEH"
    payload = tmp_path / "payload"
    _write_payload(payload)
    (install / "_internal" / "data").mkdir(parents=True)
    (install / "_internal" / "data" / "pacientes.db").write_bytes(b"history")
    (install / "_internal" / "data" / "device_id.json").write_bytes(b"device")
    (install / "old.dll").write_bytes(b"old")
    storage = tmp_path / "local" / "updates"
    monkeypatch.setattr(updater, "update_storage_root", lambda: storage)
    monkeypatch.setattr(updater, "wait_for_process", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "health_check_install", lambda *_args: True)
    monkeypatch.setattr(updater, "start_launcher", lambda *_args: None)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "product": "SIGEH",
                "version": "1.0.2",
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        ),
        encoding="utf-8",
    )
    assert updater.apply_update(manifest, 0) == 0
    assert (install / "SIGEH.exe").read_bytes() == b"new"
    assert not (install / "old.dll").exists()
    assert (install / "_internal" / "data" / "pacientes.db").read_bytes() == b"history"
    assert (install / "_internal" / "data" / "device_id.json").read_bytes() == b"device"
    assert (storage / "backup" / "1.0.2" / "old.dll").is_file()


def test_release_rejects_packaged_operational_database(tmp_path):
    dist = tmp_path / "SIGEH"
    data = dist / "_internal" / "data"
    data.mkdir(parents=True)
    (data / "pacientes.db").write_bytes(b"must-not-ship")

    with pytest.raises(ValueError, match="base operacional"):
        release_packaging.validate_no_operational_database(dist)


@pytest.mark.parametrize(
    "relative_path",
    ["_internal/database_url.bundle", "_internal/debug.log", ".env"],
)
def test_release_rejects_credentials_and_runtime_files(tmp_path, relative_path):
    dist = tmp_path / "SIGEH"
    forbidden = dist / relative_path
    forbidden.parent.mkdir(parents=True)
    forbidden.write_bytes(b"must-not-ship")

    with pytest.raises(ValueError, match="credenciales o archivos runtime"):
        release_packaging.validate_no_credentials_or_runtime_files(dist)


def test_failed_health_check_rolls_back_complete_install(tmp_path, monkeypatch):
    install = tmp_path / "SIGEH"
    payload = tmp_path / "payload"
    _write_payload(payload)
    install.mkdir()
    (install / "SIGEH.exe").write_bytes(b"old")
    (install / "CALCULOS_QT.exe").write_bytes(b"old")
    (install / "SIGEH_Updater.exe").write_bytes(b"old")
    (install / "_internal").mkdir()
    monkeypatch.setattr(updater, "update_storage_root", lambda: tmp_path / "local")
    monkeypatch.setattr(updater, "wait_for_process", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(updater, "health_check_install", lambda *_args: False)
    monkeypatch.setattr(updater, "start_launcher", lambda *_args: None)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "product": "SIGEH",
                "version": "1.0.2",
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        ),
        encoding="utf-8",
    )
    assert updater.apply_update(manifest, 0) == 1
    assert (install / "SIGEH.exe").read_bytes() == b"old"


def test_health_check_retries_only_transient_sqlite_cleanup(tmp_path, monkeypatch):
    install = tmp_path / "SIGEH"
    _write_payload(install)
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        if command[0].endswith("SIGEH.exe"):
            return SimpleNamespace(returncode=0)
        result_path = Path(command[-1])
        if len([item for item in calls if item[0].endswith("CALCULOS_QT.exe")]) == 1:
            result_path.write_text(
                json.dumps({"status": "FAIL", "exception_type": "PermissionError"}),
                encoding="utf-8",
            )
            return SimpleNamespace(returncode=3)
        result_path.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    assert updater.health_check_install(install)
    assert len([item for item in calls if item[0].endswith("CALCULOS_QT.exe")]) == 2


def test_health_check_waits_for_delayed_pyinstaller_gui_result(tmp_path, monkeypatch):
    install = tmp_path / "SIGEH"
    _write_payload(install)
    delayed_result = None

    def fake_run(command, **_kwargs):
        nonlocal delayed_result
        if command[0].endswith("SIGEH.exe"):
            return SimpleNamespace(returncode=0)
        delayed_result = Path(command[-1])
        return SimpleNamespace(returncode=0)

    def finish_delayed_result(_seconds):
        delayed_result.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    monkeypatch.setattr(updater.time, "sleep", finish_delayed_result)

    assert updater.health_check_install(install)


def test_health_result_wait_rejects_missing_or_incomplete_json(tmp_path):
    result_path = tmp_path / "v15.json"
    assert updater._read_health_result_when_ready(result_path, timeout=0) is None
    result_path.write_text("{", encoding="utf-8")
    assert updater._read_health_result_when_ready(result_path, timeout=0) is None


def test_health_check_rejects_missing_packaged_result(tmp_path, monkeypatch):
    install = tmp_path / "SIGEH"
    _write_payload(install)
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(updater, "_read_health_result_when_ready", lambda _path: None)

    assert not updater.health_check_install(install)


def test_health_check_rejects_incomplete_and_nontransient_install(
    tmp_path, monkeypatch
):
    assert not updater.health_check_install(tmp_path)
    install = tmp_path / "SIGEH"
    _write_payload(install)

    def fake_run(command, **_kwargs):
        if command[0].endswith("SIGEH.exe"):
            return SimpleNamespace(returncode=0)
        Path(command[-1]).write_text(
            json.dumps({"status": "FAIL", "exception_type": "RuntimeError"}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=3)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    assert not updater.health_check_install(install)


def test_updater_helpers_and_invalid_payloads(tmp_path, monkeypatch):
    updater.wait_for_process(0)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    assert updater.update_storage_root() == tmp_path / "local" / "SIGEH" / "updates"

    install = tmp_path / "install"
    with pytest.raises(FileNotFoundError):
        updater.start_launcher(install)
    install.mkdir()
    (install / "SIGEH.exe").write_bytes(b"exe")
    launched = []
    monkeypatch.setattr(
        updater.subprocess,
        "Popen",
        lambda *args, **kwargs: launched.append((args, kwargs)),
    )
    updater.start_launcher(install)
    assert launched and launched[0][0][0] == [str(install / "SIGEH.exe")]

    manifest = tmp_path / "bad-product.json"
    manifest.write_text(
        json.dumps(
            {
                "product": "OTHER",
                "version": "1.0.2",
                "install_dir": str(install),
                "payload_dir": str(tmp_path / "payload"),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no pertenece"):
        updater.apply_update(manifest, 0)

    manifest.write_text(
        json.dumps(
            {
                "product": "SIGEH",
                "version": "1.0.2",
                "install_dir": str(install),
                "payload_dir": str(tmp_path / "payload"),
            }
        ),
        encoding="utf-8",
    )
    assert updater.apply_update(manifest, 0) == 2


def test_updater_logging_wait_and_preserved_directories(tmp_path, monkeypatch):
    log = tmp_path / "logs" / "updater.log"
    updater.write_log(log, "ready")
    assert "ready" in log.read_text(encoding="utf-8")

    monkeypatch.setattr(updater.sys, "platform", "linux")
    monkeypatch.setattr(
        updater.os, "kill", lambda *_args: (_ for _ in ()).throw(OSError())
    )
    updater.wait_for_process(123, timeout=1)

    backup = tmp_path / "backup"
    install = tmp_path / "install"
    (backup / "recibos").mkdir(parents=True)
    (backup / "recibos" / "receipt.pdf").write_bytes(b"receipt")
    (backup / "lanzador_log.txt").write_text("old", encoding="utf-8")
    updater.merge_preserved(backup, install)
    assert (install / "recibos" / "receipt.pdf").read_bytes() == b"receipt"
    assert (install / "lanzador_log.txt").read_text(encoding="utf-8") == "old"


def test_health_check_handles_launcher_and_subprocess_failures(tmp_path, monkeypatch):
    install = tmp_path / "SIGEH"
    _write_payload(install)
    monkeypatch.setattr(
        updater.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=4),
    )
    assert not updater.health_check_install(install)

    def fail(*_args, **_kwargs):
        raise OSError("cannot execute")

    monkeypatch.setattr(updater.subprocess, "run", fail)
    assert not updater.health_check_install(install)


def test_updater_installs_without_previous_version_and_cli_delegates(
    tmp_path, monkeypatch
):
    install = tmp_path / "SIGEH"
    payload = tmp_path / "payload"
    _write_payload(payload)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "product": "SIGEH",
                "version": "1.0.2",
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(updater, "update_storage_root", lambda: tmp_path / "local")
    monkeypatch.setattr(updater, "wait_for_process", lambda *_args: None)
    monkeypatch.setattr(updater.time, "sleep", lambda *_args: None)
    monkeypatch.setattr(updater, "health_check_install", lambda *_args: True)
    monkeypatch.setattr(updater, "start_launcher", lambda *_args: None)
    assert updater.apply_update(manifest, 0) == 0
    assert (install / "SIGEH.exe").is_file()

    delegated = []
    monkeypatch.setattr(
        updater, "apply_update", lambda path, pid: delegated.append((path, pid)) or 7
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["SIGEH_Updater.exe", "--manifest", str(manifest), "--wait-pid", "55"],
    )
    assert updater.main() == 7
    assert delegated == [(manifest, 55)]


def test_portable_launcher_missing_app_and_database_errors(tmp_path, monkeypatch):
    errors = []
    monkeypatch.setattr(portable_launcher, "portable_root", lambda: tmp_path)
    monkeypatch.setattr(portable_launcher, "show_error", errors.append)
    monkeypatch.setattr(portable_launcher.sys, "argv", ["SIGEH.exe"])
    assert portable_launcher.main() == 2
    assert errors

    errors.clear()
    (tmp_path / "CALCULOS_QT.exe").write_bytes(b"exe")
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda *_args, **_kwargs: "configured",
    )
    assert portable_launcher.main() == 3
    assert errors

    (tmp_path / "_internal").mkdir()
    errors.clear()
    monkeypatch.setattr(
        portable_launcher,
        "install_database_url_for_child",
        lambda *_args, **_kwargs: "",
    )
    assert portable_launcher.main() == 5
    assert errors


def test_portable_root_uses_frozen_executable(monkeypatch, tmp_path):
    monkeypatch.setattr(portable_launcher.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        portable_launcher.sys, "executable", str(tmp_path / "SIGEH.exe")
    )
    assert portable_launcher.portable_root() == tmp_path


def test_launcher_uses_compiled_version_when_metadata_is_stale(monkeypatch, tmp_path):
    (tmp_path / lanzador.CONFIG_FILE).write_text(
        json.dumps({"product": lanzador.PRODUCT_ID, "version": "1.0.4"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(lanzador, "DEFAULT_VERSION", "1.0.5")
    monkeypatch.setattr(lanzador, "get_real_dir", lambda: str(tmp_path))
    monkeypatch.setattr(lanzador, "get_bundle_dir", lambda: str(tmp_path))

    assert lanzador.get_local_version() == "1.0.5"


def test_launcher_accepts_matching_metadata_without_warning(monkeypatch, tmp_path):
    (tmp_path / lanzador.CONFIG_FILE).write_text(
        json.dumps({"product": lanzador.PRODUCT_ID, "version": "1.0.5"}),
        encoding="utf-8",
    )
    warnings = []
    monkeypatch.setattr(lanzador, "DEFAULT_VERSION", "1.0.5")
    monkeypatch.setattr(lanzador, "get_real_dir", lambda: str(tmp_path))
    monkeypatch.setattr(lanzador, "get_bundle_dir", lambda: str(tmp_path))
    monkeypatch.setattr(lanzador, "write_launcher_log", warnings.append)

    assert lanzador.get_local_version() == "1.0.5"
    assert warnings == []


def test_release_packaging_helpers_and_cli(tmp_path, monkeypatch):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"payload")
    assert (
        release_packaging.sha256_file(source)
        == __import__("hashlib").sha256(b"payload").hexdigest()
    )
    with pytest.raises(FileNotFoundError, match="updater ONEFILE"):
        prepare_release(tmp_path, tmp_path / "missing.exe", tmp_path / "output")

    dist = tmp_path / "SIGEH"
    _write_payload(dist)
    updater_exe = tmp_path / "SIGEH_Updater.exe"
    updater_exe.write_bytes(b"updater")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "release_packaging.py",
            "--dist",
            str(dist),
            "--updater",
            str(updater_exe),
            "--output",
            str(tmp_path / "release"),
            "--version",
            "1.0.2",
        ],
    )
    assert release_packaging.main() == 0


def test_release_guard_requires_updater_and_builds_complete_assets(tmp_path):
    dist = tmp_path / "SIGEH"
    dist.mkdir()
    (dist / "SIGEH.exe").write_bytes(b"launcher")
    (dist / "CALCULOS_QT.exe").write_bytes(b"app")
    (dist / "_internal").mkdir()
    try:
        validate_dist(dist)
    except FileNotFoundError as exc:
        assert "SIGEH_Updater.exe" in str(exc)
    else:
        raise AssertionError("El guard permitió una distribución sin updater")
    updater_exe = tmp_path / "SIGEH_Updater.exe"
    updater_exe.write_bytes(b"MZ-updater")
    result = prepare_release(dist, updater_exe, tmp_path / "release", version="1.0.2")
    assert result["archive"].is_file()
    expected_version = {"product": "SIGEH", "version": "1.0.2"}
    assert json.loads(
        (dist / "version_config.json").read_text(encoding="utf-8")
    ) == expected_version
    assert json.loads(
        (dist / "_internal" / "version_config.json").read_text(encoding="utf-8")
    ) == expected_version
    with ZipFile(result["archive"]) as archive:
        packaged_version = json.loads(
            archive.read("SIGEH/version_config.json").decode("utf-8")
        )
        bundled_version = json.loads(
            archive.read("SIGEH/_internal/version_config.json").decode("utf-8")
        )
    assert packaged_version == expected_version
    assert bundled_version == expected_version
    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert manifest["entrypoint"] == "SIGEH.exe"
    assert manifest["updater"] == "SIGEH_Updater.exe"
    assert manifest["sha256"] == result["sha256"]
