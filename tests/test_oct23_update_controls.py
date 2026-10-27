"""Manual and startup checks share the single authorized update channel."""

from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
import pytest

import lanzador
import sigeh_update
import update_controls
from tests.test_sigeh_update import _release


@pytest.mark.parametrize("asset_index", [0, 1, 2])
@pytest.mark.parametrize(
    "url",
    [
        "",
        "http://github.com/fake.zip",
        "https://evil.example/zip",
        "file:///C:/bad.zip",
    ],
)
def test_external_release_asset_urls_are_rejected(asset_index, url):
    data = _release()
    data["assets"][asset_index]["browser_download_url"] = url
    with pytest.raises(sigeh_update.UpdateError, match="canal SIGEH"):
        sigeh_update.parse_release(data)


@pytest.mark.parametrize("available", [False, True])
def test_manual_worker_checks_version_and_validates_new_package(monkeypatch, available):
    release = SimpleNamespace(version="9.0.0" if available else "1.0.0")
    monkeypatch.setattr(update_controls, "get_latest_release", lambda **_: release)
    validate = Mock()
    monkeypatch.setattr(update_controls, "resolve_release_payload", validate)
    worker = update_controls.UpdateCheckWorker()
    checked = Mock()
    worker.checked.connect(checked)
    worker.run()
    checked.assert_called_once_with(release.version, available)
    assert validate.call_count == int(available)


def test_manual_worker_network_failure_is_reported_without_details(monkeypatch):
    def fail(**_kwargs):
        raise OSError("synthetic private detail")

    monkeypatch.setattr(update_controls, "get_latest_release", fail)
    worker = update_controls.UpdateCheckWorker()
    failed = Mock()
    worker.failed.connect(failed)
    worker.run()
    failed.assert_called_once_with()


@pytest.mark.parametrize("result", ["new", "current", "offline"])
def test_button_checks_asynchronously_and_recovers_after_completion(
    monkeypatch, result
):
    qt = QApplication.instance() or QApplication([])
    surface = QWidget()
    control = update_controls.UpdateCheckControl(surface)
    assert not control.button.icon().isNull()
    information = Mock()
    monkeypatch.setattr(QMessageBox, "information", information)

    def release(**_kwargs):
        if result == "offline":
            raise OSError("Offline")
        return SimpleNamespace(version="9.0.0" if result == "new" else "1.0.0")

    monkeypatch.setattr(update_controls, "get_latest_release", release)
    monkeypatch.setattr(update_controls, "resolve_release_payload", Mock())
    assert control.button.accessibleName() == "Buscar actualizaciones"
    control.button.click()
    assert not control.button.isEnabled()
    worker = control.worker
    control.start()
    assert control.worker is worker
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    qt.processEvents()
    assert control.worker is None and control.button.isEnabled()
    information.assert_called_once()
    message = information.call_args.args[2]
    assert (
        ("9.0.0" in message)
        if result == "new"
        else (
            "actualizado" in message
            if result == "current"
            else "seguir trabajando" in message
        )
    )
    surface.close()
    surface.deleteLater()
    qt.processEvents()


@pytest.mark.parametrize("new", [False, True])
def test_startup_checker_uses_the_same_channel_on_each_run(monkeypatch, new):
    release = SimpleNamespace(
        version="9.0.0" if new else "1.0.0", archive_url="https://example.invalid/test"
    )
    query = Mock(return_value=release)
    monkeypatch.setattr(lanzador, "get_latest_release", query)
    monkeypatch.setattr(
        lanzador, "resolve_release_payload", lambda _: SimpleNamespace(sha256="a" * 64)
    )
    monkeypatch.setattr(lanzador, "get_local_version", lambda: "1.2.13")
    worker = lanzador.UpdateChecker()
    found, current = Mock(), Mock()
    worker.update_found.connect(found)
    worker.no_update.connect(current)
    worker.run()
    worker.run()
    assert query.call_count == 2
    assert found.call_count == (2 if new else 0)
    assert current.call_count == (0 if new else 2)


def test_startup_failure_opens_local_app(monkeypatch):
    owner = SimpleNamespace(lbl_status=Mock(), launch_main_app=Mock())
    monkeypatch.setattr(lanzador, "write_launcher_log", Mock())
    lanzador.LauncherDialog.handle_update_check_error(owner, "offline")
    owner.launch_main_app.assert_called_once_with()


def test_closing_window_during_check_does_not_destroy_running_thread(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    release_query = Event()

    def release(**_kwargs):
        assert release_query.wait(3)
        return SimpleNamespace(version="1.0.0")

    monkeypatch.setattr(update_controls, "get_latest_release", release)
    surface = QWidget()
    control = update_controls.UpdateCheckControl(surface)
    control.start()
    worker = control.worker
    surface.close()
    surface.deleteLater()
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    release_query.set()
    loop = QEventLoop()
    worker.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    qt.processEvents()


def test_launcher_uses_new_verified_payload_updater(tmp_path, monkeypatch):
    import json
    import zipfile

    install, download = tmp_path / "installed", tmp_path / "download"
    install.mkdir()
    download.mkdir()
    (install / "SIGEH_Updater.exe").write_bytes(b"old updater")
    archive = download / "update.zip"
    with zipfile.ZipFile(archive, "w") as package:
        for name in (
            "CALCULOS_QT.exe",
            "SIGEH.exe",
            "SIGEH_Updater.exe",
            "_internal/runtime.dll",
        ):
            package.writestr("SIGEH/" + name, b"new verified payload")
    monkeypatch.setattr(lanzador, "get_real_dir", lambda: str(install))
    monkeypatch.setattr(lanzador.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(lanzador, "write_launcher_log", Mock())
    process = Mock()
    monkeypatch.setattr(lanzador.subprocess, "Popen", process)
    qt = QApplication.instance() or QApplication([])
    owner = SimpleNamespace(
        lbl_status=Mock(), new_version_string="9.0.0", launch_main_app=Mock()
    )
    from types import MethodType

    for name in ("_extract_legacy_archive", "_handoff_payload"):
        setattr(owner, name, MethodType(getattr(lanzador.LauncherDialog, name), owner))
    monkeypatch.setattr(qt, "quit", Mock())
    from pathlib import Path

    lanzador.LauncherDialog.apply_update(owner, str(archive))
    process.assert_called_once()
    command = process.call_args.args[0]
    assert Path(command[0]).read_bytes() == b"new verified payload"
    manifest = json.loads(Path(command[2]).read_text())
    assert manifest["install_dir"] == str(install)
    owner.launch_main_app.assert_not_called()
    qt.processEvents()
