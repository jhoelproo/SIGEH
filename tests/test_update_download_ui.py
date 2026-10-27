"""The launcher keeps transfer and preparation off the Qt event thread."""

from types import MethodType, SimpleNamespace
from unittest.mock import Mock
from threading import Event

import pytest
from PySide6.QtWidgets import QApplication, QProgressBar
from PySide6.QtCore import QEventLoop, QThread, QTimer

import lanzador


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.mark.parametrize("failure", [False, True])
def test_worker_prepares_in_background_and_retains_cache_on_failure(
    tmp_path, monkeypatch, application, failure
):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(lanzador, "get_real_dir", lambda: str(tmp_path / "install"))
    service = Mock(
        side_effect=OSError("offline") if failure else None,
        return_value=tmp_path / "prepared",
    )
    monkeypatch.setattr(lanzador, "prepare_update", service)
    worker = lanzador.DownloadWorker("url", "a" * 64, "1.3.6")
    finished, error = Mock(), Mock()
    worker.finished_download.connect(finished)
    worker.error_download.connect(error)
    worker.run()
    finished.assert_called_once_with("" if failure else str(tmp_path / "prepared"))
    assert error.call_count == int(failure)
    assert service.call_args.args[2] == tmp_path / "SIGEH/updates"
    assert service.call_args.kwargs["progress"] == worker.report_progress


def test_progress_events_are_throttled_with_final_event_guaranteed(
    monkeypatch, application
):
    worker = lanzador.DownloadWorker("url", "a" * 64)
    clock = Mock(side_effect=[100, 100.01, 100.2, 100.21])
    monkeypatch.setattr(lanzador.time, "monotonic", clock)
    progress = Mock()
    worker.progress_update.connect(progress)
    for done in (0, 1, 2, 3):
        worker.report_progress(done, 3)
    assert [call.args for call in progress.call_args_list] == [(0, 3), (2, 3), (3, 3)]


def test_progress_ui_handles_resume_fallback_and_large_archives(
    monkeypatch, application
):
    owner = SimpleNamespace(progress=QProgressBar(), _download_sample=None)
    monkeypatch.setattr(
        lanzador.time, "monotonic", Mock(side_effect=[100, 102, 103, 104])
    )
    update = MethodType(lanzador.LauncherDialog.update_progress, owner)
    update(1024, 4096)
    update(2048, 4096)
    assert "KB/s" in owner.progress.format()
    assert "restantes" in owner.progress.format()
    update(0, 4 * 1024**3)
    assert owner.progress.maximum() == 1000
    update(1024, 0)
    assert owner.progress.maximum() == 0


def test_start_download_passes_verified_delta_and_starts_worker(
    monkeypatch, application
):
    factory = Mock()
    monkeypatch.setattr(lanzador, "DownloadWorker", factory)
    delta = object()
    owner = SimpleNamespace(
        lbl_status=Mock(),
        progress=Mock(),
        checker=SimpleNamespace(payload=SimpleNamespace(delta=delta)),
        update_progress=Mock(),
        apply_update=Mock(),
        handle_download_error=Mock(),
    )
    lanzador.LauncherDialog.start_download(owner, "1.3.6", "url", "a" * 64)
    assert factory.call_args.kwargs == {"delta": delta}
    factory.return_value.start.assert_called_once_with()
    factory.return_value.status_update.connect.assert_called_once_with(
        owner.lbl_status.setText
    )


def test_prepared_directory_is_handed_off_without_extraction(
    tmp_path, monkeypatch, application
):
    monkeypatch.setattr(lanzador, "get_real_dir", lambda: str(tmp_path / "install"))
    owner = SimpleNamespace(
        lbl_status=Mock(),
        launch_main_app=Mock(),
        _handoff_payload=Mock(),
        _extract_legacy_archive=Mock(),
    )
    lanzador.LauncherDialog.apply_update(owner, str(tmp_path))
    owner._handoff_payload.assert_called_once()
    owner._extract_legacy_archive.assert_not_called()
    owner.launch_main_app.assert_not_called()


def test_channel_failure_is_reported_without_starting_download(
    monkeypatch, application
):
    monkeypatch.setattr(
        lanzador, "get_latest_release", Mock(side_effect=OSError("offline"))
    )
    worker = lanzador.UpdateChecker()
    errors, found = Mock(), Mock()
    worker.error_found.connect(errors)
    worker.update_found.connect(found)
    worker.run()
    assert "offline" in errors.call_args.args[0]
    found.assert_not_called()


@pytest.mark.parametrize("kind", ["absent", "failure"])
def test_invalid_preparation_opens_existing_installation(
    tmp_path, monkeypatch, application, kind
):
    monkeypatch.setattr(lanzador, "get_real_dir", lambda: str(tmp_path / "install"))
    monkeypatch.setattr(lanzador, "write_launcher_log", Mock())
    owner = SimpleNamespace(
        lbl_status=Mock(),
        launch_main_app=Mock(),
        _handoff_payload=Mock(side_effect=OSError("error")),
    )
    path = tmp_path / "absent" if kind == "absent" else tmp_path
    lanzador.LauncherDialog.apply_update(owner, str(path))
    owner.launch_main_app.assert_called_once_with()


def test_real_worker_keeps_the_gui_event_loop_responsive(
    tmp_path, monkeypatch, application
):
    release_transfer = Event()
    timer_ran = []

    def prepare(*_args, **_kwargs):
        assert QThread.currentThread() != application.thread()
        assert release_transfer.wait(3)
        return tmp_path

    monkeypatch.setattr(lanzador, "prepare_update", prepare)
    worker = lanzador.DownloadWorker("url", "a" * 64)
    loop = QEventLoop()
    worker.finished.connect(loop.quit)

    def gui_tick():
        timer_ran.append(True)
        release_transfer.set()

    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    watchdog.start(4000)
    worker.start()
    QTimer.singleShot(0, gui_tick)
    loop.exec()
    watchdog.stop()
    assert worker.wait(1000)
    assert timer_ran == [True]


@pytest.mark.parametrize("dark", [False, True])
def test_real_launcher_surface_uses_legible_theme_and_async_checker(
    monkeypatch, application, dark
):
    settings = Mock()
    settings.value.return_value = dark
    checker = Mock()
    monkeypatch.setattr(lanzador, "QSettings", Mock(return_value=settings))
    monkeypatch.setattr(lanzador, "UpdateChecker", Mock(return_value=checker))
    dialog = lanzador.LauncherDialog()
    assert dialog._is_dark is dark
    checker.start.assert_called_once_with()
    expected = lanzador.visual_theme_tokens(dark)["success" if dark else "success_bg"]
    assert f"QProgressBar::chunk{{background:{expected}" in dialog.styleSheet()
    dialog.close()
