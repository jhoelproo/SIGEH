from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest
from PyPDF2 import PdfWriter
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
import billing_closure_recovery as recovery


def test_bad_closure_does_not_discard_other_recoverable_turns(monkeypatch):
    monkeypatch.setattr(app, "db_connect", MagicMock())
    monkeypatch.setattr(
        recovery, "pending_central_closures", lambda con: [{"turn": 1}, {"turn": 2}]
    )
    monkeypatch.setattr(
        recovery,
        "closure_from_interval",
        lambda row: SimpleNamespace(source_instance_id="source", turn_id=row["turn"]),
    )
    monkeypatch.setattr(recovery, "closed_turn_attentions", lambda *args: [])
    monkeypatch.setattr(app, "write_runtime_log", Mock())

    def capture(event, rows):
        if event.turn_id == 1:
            raise RuntimeError("synthetic failure")

    monkeypatch.setattr(app, "capture_shift_closure_snapshot", capture)
    assert app.EmergencyWorkspacePage._prepare_committed_closures(None) == [
        ("source", 2)
    ]


@pytest.mark.parametrize("viewer", [False, OSError("viewer unavailable")])
def test_print_is_attempted_even_if_pdf_viewer_fails(tmp_path, monkeypatch, viewer):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"synthetic PDF")
    open_pdf = (
        Mock(side_effect=viewer)
        if isinstance(viewer, Exception)
        else Mock(return_value=viewer)
    )
    monkeypatch.setattr(app, "open_file_path", open_pdf)
    monkeypatch.setattr(app, "claim_shift_print_once", lambda closure: True)
    finish = Mock()
    monkeypatch.setattr(app, "finish_shift_print", finish)
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    printer = Mock(return_value=True)
    window = SimpleNamespace(
        emergency_workspace=SimpleNamespace(print_pdf_with_v15=printer)
    )
    closure = {"turn_id": 9, "source_instance_id": "source"}
    with pytest.raises(OSError):
        app.MainWindow._open_and_print_shift_report(window, closure, str(path))
    printer.assert_called_once_with(str(path), copies=1)
    finish.assert_called_once_with(closure)


def test_generated_closure_resumes_unrequested_print_without_regeneration(monkeypatch):
    closure = {
        "source_instance_id": "source",
        "turn_id": 9,
        "report_filename": "report.pdf",
        "print_requested_at": None,
    }
    monkeypatch.setattr(app, "get_shift_closure", lambda *args: closure)
    resolve = Mock(return_value="recovered.pdf")
    monkeypatch.setattr(app, "resolve_report_document", resolve)
    monkeypatch.setattr(app, "mark_shift_report_opened", Mock())
    monkeypatch.setattr(app, "log_action", Mock())
    claim = Mock()
    monkeypatch.setattr(app, "claim_shift_closure", claim)
    deliver = Mock()
    worker = app.ShiftClosureReportWorker("source", 9, "operator", deliver)
    worker.run()
    deliver.assert_called_once_with(closure, "recovered.pdf")
    claim.assert_not_called()


def test_zero_eligible_patients_still_produce_a_closure_report(monkeypatch):
    closure = {"source_instance_id": "source", "turn_id": 9}
    monkeypatch.setattr(app, "get_shift_closure", lambda *args: None)
    monkeypatch.setattr(app, "claim_shift_closure", lambda *args: closure)
    monkeypatch.setattr(
        app, "build_shift_closure_report_data", lambda value: {"details": []}
    )
    worker = app.ShiftClosureReportWorker("source", 9, "operator", Mock())
    worker._generate_report = Mock(return_value="zero.pdf")
    worker._complete_generated_report = Mock()
    monkeypatch.setattr(app, "mark_shift_report_skipped_empty", Mock())
    worker.run()
    worker._generate_report.assert_called_once_with(closure, {"details": []})
    worker._complete_generated_report.assert_called_once_with(closure, "zero.pdf")


@pytest.mark.parametrize(
    "reserved,viewer,printed",
    [
        (False, True, True),
        (False, False, True),
        (True, True, False),
        (True, True, OSError("printer")),
    ],
)
def test_print_reservation_and_failure_states(
    tmp_path, monkeypatch, reserved, viewer, printed
):
    path = tmp_path / "report.pdf"
    path.write_bytes(b"synthetic")
    monkeypatch.setattr(app, "open_file_path", lambda path: viewer)
    monkeypatch.setattr(app, "claim_shift_print_once", lambda closure: reserved)
    finish = Mock()
    monkeypatch.setattr(app, "finish_shift_print", finish)
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    printer = (
        Mock(side_effect=printed)
        if isinstance(printed, Exception)
        else Mock(return_value=printed)
    )
    window = SimpleNamespace(
        emergency_workspace=SimpleNamespace(print_pdf_with_v15=printer)
    )
    if not viewer or reserved:
        with pytest.raises((RuntimeError, OSError)):
            app.MainWindow._open_and_print_shift_report(window, {}, str(path))
    else:
        app.MainWindow._open_and_print_shift_report(window, {}, str(path))
    assert printer.call_count == int(reserved)
    assert finish.call_count == int(reserved)


def test_missing_report_never_requests_print(tmp_path):
    with pytest.raises(OSError):
        app.MainWindow._open_and_print_shift_report(
            None, {}, str(tmp_path / "missing.pdf")
        )


def test_successful_shift_report_completion_is_visible(monkeypatch):
    toast = Mock()
    monkeypatch.setattr(app, "FloatingToast", toast)
    window = SimpleNamespace(cierre_facturacion_en_progreso=True)
    app.MainWindow._shift_report_completed(window, {"status": "GENERATED"})
    assert window.cierre_facturacion_en_progreso is False
    toast.return_value.show.assert_called_once()


@pytest.mark.parametrize("failure", [False, True])
def test_history_open_shows_view_or_visible_error(monkeypatch, failure):
    dialog = Mock(side_effect=OSError("unreadable")) if failure else Mock()
    warning = Mock()
    monkeypatch.setattr(app, "ComparisonPdfDialog", dialog)
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    app.LegacyReportsDialog._report_document_open_ready(
        SimpleNamespace(), "history.pdf"
    )
    assert warning.call_count == int(failure)
    if not failure:
        dialog.return_value.show.assert_called_once()


def test_history_print_preview_uses_integrated_viewer():
    parent = SimpleNamespace(_report_document_open_ready=Mock())
    app.ReportsDialog._show_prepared_report_print(parent, "history.pdf")
    parent._report_document_open_ready.assert_called_once_with("history.pdf")


@pytest.mark.parametrize(
    "dialog_type,button_name",
    [
        (app.LegacyReportsDialog, "btn_open"),
        (app.ReportsDialog, "btn_open"),
        (app.ReportsDialog, "btn_history_print"),
    ],
)
def test_history_button_resolves_and_opens_report_in_integrated_viewer(
    tmp_path, monkeypatch, dialog_type, button_name
):
    qt_app = QApplication.instance() or QApplication([])
    pdf_path = tmp_path / "closure.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    with pdf_path.open("wb") as stream:
        writer.write(stream)
    row = {
        "record_id": 7,
        "source_key": "V15-CENTRAL|7",
        "source_table": "billing_shift_closures",
        "report_type": "Cierre automático de turno",
        "start_date": "2026-09-18",
        "end_date": "2026-09-18",
        "generated_at": "2026-09-19 08:00:00",
        "generated_by": "SISTEMA",
        "filepath": pdf_path.name,
        "totals_json": "{}",
    }
    monkeypatch.setattr(app, "ars_list", lambda: [])
    monkeypatch.setattr(app, "list_usernames", lambda: [])
    monkeypatch.setattr(app, "list_report_history", lambda: [row])
    monkeypatch.setattr(
        app, "resolve_report_document", lambda *_args, **_kwargs: str(pdf_path)
    )
    dialog = dialog_type({"username": "audit", "role": app.ROLE_AUDIT})
    try:
        dialog.show()
        if isinstance(dialog, app.ReportsDialog):
            dialog.tabs.setCurrentIndex(dialog.tabs.count() - 1)
        QTest.mouseClick(getattr(dialog, button_name), Qt.LeftButton)
        for _ in range(100):
            qt_app.processEvents()
            preview = getattr(dialog, "_report_preview_dialog", None)
            if preview is not None and preview.isVisible():
                break
            QTest.qWait(10)
        preview = dialog._report_preview_dialog
        assert preview.isVisible()
        assert preview._pdf_document.pageCount() == 1
        assert dialog.btn_open.isEnabled()
    finally:
        preview = getattr(dialog, "_report_preview_dialog", None)
        if preview is not None:
            preview.close()
        dialog.close()


@pytest.mark.parametrize("failure", [False, True])
def test_worker_claim_conflict_and_read_failure(monkeypatch, failure):
    monkeypatch.setattr(
        app,
        "get_shift_closure",
        Mock(side_effect=OSError("unavailable"))
        if failure
        else Mock(return_value=None),
    )
    monkeypatch.setattr(app, "claim_shift_closure", Mock(return_value=None))
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    worker = app.ShiftClosureReportWorker("source", 9, "operator", Mock())
    worker._record_generation_failure = Mock()
    results = []
    errors = []
    worker.completed.connect(results.append)
    worker.failed.connect(errors.append)
    worker.run()
    assert bool(errors) == failure
    if not failure:
        assert results[0]["status"] == "ALREADY_CLAIMED"
