from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from pdf_engine import renderer


@pytest.mark.parametrize(
    "media_failure", [None, RuntimeError("print media unavailable")]
)
def test_renderer_sets_print_media_once_and_closes_on_failure(
    monkeypatch, media_failure
):
    playwright = Mock()
    browser = playwright.chromium.launch.return_value
    page = browser.new_page.return_value
    page.emulate_media.side_effect = media_failure
    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        Mock(return_value=Mock(start=Mock(return_value=playwright))),
    )
    monkeypatch.setattr(renderer, "_configure_playwright_browsers", Mock())
    receipt_renderer = renderer.ReceiptPDFRenderer()

    if media_failure:
        with pytest.raises(RuntimeError, match="print media unavailable"):
            receipt_renderer.start()
        page.close.assert_called_once_with()
        browser.close.assert_called_once_with()
        playwright.stop.assert_called_once_with()
    else:
        receipt_renderer.start()
        receipt_renderer.start()

    playwright.chromium.launch.assert_called_once()
    page.emulate_media.assert_called_once_with(media="print")


@pytest.mark.parametrize(
    ("exists", "auto_print", "expected_tasks"),
    [(False, True, 0), (True, False, 1), (True, True, 2)],
)
def test_receipt_output_dispatches_without_waiting_for_the_viewer(
    tmp_path, monkeypatch, exists, auto_print, expected_tasks
):
    path = tmp_path / "recibo.pdf"
    if exists:
        path.touch()
    pool = Mock()
    print_receipt = Mock()
    preview = Mock()
    monkeypatch.setattr(
        app, "QThreadPool", Mock(globalInstance=Mock(return_value=pool))
    )
    monkeypatch.setattr(app, "_print_generated_receipt", print_receipt)
    monkeypatch.setattr(app, "_open_generated_receipt", preview)
    window = Mock()

    app.MainWindow._open_pdf_after_generation(window, str(path), auto_print)

    print_receipt.assert_not_called()
    preview.assert_not_called()
    assert pool.start.call_count == expected_tasks
    tasks = [call.args[0] for call in pool.start.call_args_list]
    if auto_print and exists:
        tasks.pop(0)()
        print_receipt.assert_called_once_with(
            str(path), window.pdf_worker.signals.print_failed.emit
        )
    if exists:
        tasks.pop()()
        preview.assert_called_once_with(str(path))


@pytest.mark.parametrize("result", [True, False, TimeoutError("synthetic timeout")])
def test_receipt_print_uses_one_spooler_submission(monkeypatch, result):
    dispatch = (
        Mock(side_effect=result)
        if isinstance(result, Exception)
        else Mock(return_value=result)
    )
    monkeypatch.setattr(
        app,
        "load_v15_application_module",
        lambda: SimpleNamespace(imprimir_pdf=dispatch),
    )
    durations = Mock()
    runtime_log = Mock()
    monkeypatch.setattr(app, "write_pdf_performance", durations)
    monkeypatch.setattr(app, "write_runtime_log", runtime_log)
    failure = Mock()

    app._print_generated_receipt("recibo.pdf", failure)

    dispatch.assert_called_once_with("recibo.pdf", copias=1, propagar_error=True)
    if result is True:
        failure.assert_not_called()
        durations.assert_called_once()
    else:
        failure.assert_called_once_with()
        durations.assert_not_called()
        runtime_log.assert_called_once()


@pytest.mark.parametrize(
    "shell_result,fallback_result", [(True, True), (False, True), (False, False)]
)
def test_receipt_preview_uses_a_local_file_uri_as_fallback(
    monkeypatch, tmp_path, shell_result, fallback_result
):
    import webbrowser

    path = tmp_path / "recibo de prueba.pdf"
    fallback = Mock(return_value=fallback_result)
    runtime_log = Mock()
    monkeypatch.setattr(app, "open_file_path", Mock(return_value=shell_result))
    monkeypatch.setattr(app, "write_runtime_log", runtime_log)
    monkeypatch.setattr(webbrowser, "open", fallback)

    app._open_generated_receipt(str(path))

    if shell_result:
        fallback.assert_not_called()
    else:
        fallback.assert_called_once_with(path.as_uri())
    assert runtime_log.called is (not (shell_result or fallback_result))


def render_pdf_to_file(_data, filename, **_kwargs):
    Path(filename).write_bytes(b"%PDF-1.4\n")


@pytest.mark.parametrize("same_receipt", [True, False])
def test_receipt_render_only_waits_for_its_own_cached_document(
    tmp_path, monkeypatch, same_receipt
):
    entered, finish = Event(), Event()
    initial = {"receipt_number": 1}
    concurrent = {"receipt_number": 1 if same_receipt else 2}
    monkeypatch.setattr(
        app,
        "_receipt_snapshot_cache_path",
        lambda document: tmp_path / f"{document['receipt_number']}.pdf",
    )
    monkeypatch.setattr(app, "_receipt_snapshot_render_data", lambda document: document)

    def render_first(*args, **kwargs):
        entered.set()
        assert finish.wait(timeout=8)
        render_pdf_to_file(*args, **kwargs)

    first_renderer = Mock(render_pdf=Mock(side_effect=render_first))
    second_renderer = Mock(render_pdf=Mock(side_effect=render_pdf_to_file))
    with ThreadPoolExecutor(max_workers=2) as work:
        first = work.submit(app.render_receipt_snapshot_pdf, initial, first_renderer)
        try:
            assert entered.wait(timeout=5)
            second = work.submit(
                app.render_receipt_snapshot_pdf, concurrent, second_renderer
            )
            if not same_receipt:
                assert Path(second.result(timeout=5)).name == "2.pdf"
        finally:
            finish.set()
        assert Path(first.result(timeout=5)).name == "1.pdf"
        assert Path(second.result(timeout=5)).name == (
            "1.pdf" if same_receipt else "2.pdf"
        )
    assert second_renderer.render_pdf.call_count == int(not same_receipt)


@pytest.mark.parametrize(
    "success,message,path,expects_output",
    [
        (True, "Éxito", "receipt.pdf", True),
        (True, "__RECEIPT_SAVED_RENDER_ERROR__:prueba", "receipt.pdf", False),
        (True, "Éxito", "", False),
        (False, "Fallo de prueba", "receipt.pdf", False),
    ],
)
def test_successful_receipt_queues_output_before_reset(
    monkeypatch, success, message, path, expects_output
):
    window = Mock(preferences={"auto_print": True})
    window._handle_local_receipt_reopen.return_value = False
    events = []
    window.reset_all.side_effect = lambda: events.append("reset")
    window._open_pdf_after_generation.side_effect = lambda *args, **kwargs: (
        events.append("output")
    )
    monkeypatch.setattr(app, "FloatingToast", Mock())
    monkeypatch.setattr(app, "QMessageBox", Mock())

    app.MainWindow.on_pdf_generated(window, success, message, path, 42)

    expected = ["output", "reset"] if expects_output else (["reset"] if success else [])
    assert events == expected
    if expects_output:
        window._open_pdf_after_generation.assert_called_once_with(path, auto_print=True)


def test_automatic_print_can_run_while_preview_is_blocked(monkeypatch, tmp_path):
    from threading import get_ident

    main_thread = get_ident()
    path = tmp_path / "receipt.pdf"
    path.touch()
    release, printing, preview = Event(), Event(), Event()
    worker_ids = []

    def dispatch(*_args):
        worker_ids.append(get_ident())
        printing.set()

    def open_viewer(*_args):
        preview.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(app, "_print_generated_receipt", dispatch)
    monkeypatch.setattr(app, "_open_generated_receipt", open_viewer)
    window = Mock()
    pool = app.QThreadPool()
    pool.setMaxThreadCount(2)
    monkeypatch.setattr(
        app, "QThreadPool", Mock(globalInstance=Mock(return_value=pool))
    )

    try:
        app.MainWindow._open_pdf_after_generation(window, str(path), True)
        assert preview.wait(timeout=5)
        assert printing.wait(timeout=5)
        assert worker_ids[0] != main_thread
    finally:
        release.set()
        assert pool.waitForDone(5000)


def test_print_failure_warns_without_resaving_receipt(monkeypatch):
    toast = Mock()
    monkeypatch.setattr(app, "FloatingToast", toast)
    window = Mock()
    app.MainWindow._show_receipt_print_failure(window)
    assert "Recibo guardado" in toast.call_args.args[0]
    assert toast.call_args.kwargs["is_error"] is True
    toast.return_value.show.assert_called_once_with()
    assert window.mock_calls == []


def test_viewer_launch_exception_is_logged(monkeypatch):
    import webbrowser

    log = Mock()
    monkeypatch.setattr(app, "open_file_path", Mock(return_value=False))
    monkeypatch.setattr(webbrowser, "open", Mock(side_effect=OSError))
    monkeypatch.setattr(app, "write_runtime_log", log)
    app._open_generated_receipt("synthetic.pdf")
    log.assert_called_once_with("RECEIPT_PREVIEW_OPEN_FAILED")


def test_billing_connects_background_print_failure_to_qt_ui(monkeypatch):
    pdf_worker, storage_worker = Mock(), Mock()
    monkeypatch.setattr(app, "PDFDatabaseWorker", Mock(return_value=pdf_worker))
    monkeypatch.setattr(app, "PDFStorageSyncWorker", Mock(return_value=storage_worker))
    window = Mock(current_user={"username": "qa"}, session_id="local")
    app.MainWindow._start_pdf_services(window)
    pdf_worker.signals.print_failed.connect.assert_called_once_with(
        window._show_receipt_print_failure
    )
    pdf_worker.start.assert_called_once_with()
