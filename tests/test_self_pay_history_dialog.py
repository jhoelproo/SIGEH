import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from openpyxl import load_workbook
from PySide6.QtCore import QDate
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication

import self_pay_history_dialog as ui
from self_pay_billing import payment_summary
from report_engine import ReportHTMLRenderer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


class Connection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, *_args):
        return Mock(fetchall=lambda: [])


@pytest.fixture
def dialog(qt_application, monkeypatch):
    monkeypatch.setattr(ui.QMessageBox, "warning", Mock())
    result = ui.SelfPayHistoryDialog(
        lambda: Connection(),
        actor="TEST",
        logo="",
        open_receipt=Mock(return_value="test.pdf"),
        edit_receipt=Mock(),
        preview=Mock(),
        new_receipt=Mock(),
    )
    result.start.setDate(QDate(2026, 10, 1))
    result.end.setDate(QDate(2026, 10, 3))
    yield result
    result.worker = None
    result.close()


def sample_row(**changes):
    return {
        "id": 7,
        "numero": 123,
        "nombre": "SINTÉTICO",
        "fecha": "2026-10-02",
        "coverage": "EXTRANJERO",
        "payment_status": "PAGADO",
        "total": "5.99",
        "username": "TEST",
        "matched_count": 101,
        **changes,
    }


def test_filters_real_controls_and_empty_selection(dialog):
    assert dialog.filters() == {
        "start": "2026-10-01",
        "end": "2026-10-03",
        "code": "",
        "status": "",
        "search": "",
    }
    assert dialog.selected_id() is None
    dialog.open_selected()
    dialog.edit_selected()
    dialog.open_receipt.assert_not_called()
    dialog.edit_receipt.assert_not_called()
    dialog.coverage.setCurrentIndex(1)
    dialog.status.setCurrentIndex(1)
    dialog.search.setText("  paciente  ")
    assert dialog.filters()["code"] == "EXTRANJERO"
    assert dialog.filters()["status"] == "PAGADO"
    assert dialog.filters()["search"] == "paciente"


def test_invalid_dates_show_errors_and_do_not_query(dialog):
    dialog.start.setDate(QDate(2026, 10, 4))
    with pytest.raises(ValueError):
        dialog.filters()
    dialog.load_page()
    dialog.create_report()
    dialog.export_report()
    assert ui.QMessageBox.warning.call_count == 3


def test_table_summary_selection_and_pagination(dialog, monkeypatch):
    row = sample_row()
    summary = payment_summary([row])
    dialog.show_page(([row], summary))
    assert dialog.table.item(0, 3).text() == "Extranjero"
    assert "5.99" in dialog.summary.text()
    dialog.table.selectRow(0)
    assert dialog.selected_id() == 7
    task = Mock()
    monkeypatch.setattr(dialog, "run_task", task)
    dialog.open_selected()
    operation, handler = task.call_args.args
    assert operation() == "test.pdf"
    assert handler == dialog.preview
    dialog.edit_selected()
    dialog.edit_receipt.assert_called_once_with(7)
    monkeypatch.setattr(dialog, "load_page", Mock())
    dialog.change_page(1)
    assert dialog.offset == 100
    dialog.change_page(-1)
    dialog.change_page(-1)
    assert dialog.offset == 0
    dialog.offset = 200
    dialog.search_receipts()
    assert dialog.offset == 0


def test_empty_table_and_new_receipt_callback(dialog):
    dialog.show_page(([], payment_summary([])))
    assert dialog.table.rowCount() == 0 and "0 recibos" in dialog.message.text()
    callback = Mock()
    dialog.start_receipt(callback, "NO_ASEGURADO")
    callback.assert_called_once_with("NO_ASEGURADO")


def test_worker_signals_on_success_and_failure(qt_application):
    success, failure = [], []
    worker = ui.HistoryTask(lambda: {"value": 1}, None)
    worker.ready.connect(success.append)
    worker.run()
    assert success == [{"value": 1}]

    def fail():
        raise ConnectionError("Sin conexión")

    worker = ui.HistoryTask(fail, None)
    worker.failed.connect(failure.append)
    worker.run()
    assert failure == ["Sin conexión"]


def test_task_disables_filters_prevents_overlap_and_reenables(dialog, monkeypatch):
    task = Mock()
    monkeypatch.setattr(ui, "HistoryTask", Mock(return_value=task))
    operation = Mock()
    handler = Mock()
    dialog.run_task(operation, handler)
    assert all(not control.isEnabled() for control in dialog.query_controls)
    dialog.run_task(operation, handler)
    task.start.assert_called_once()
    task.ready.connect.assert_called_once_with(handler)
    dialog.rows = [sample_row()]
    dialog.task_finished()
    assert dialog.worker is None and dialog.next.isEnabled()
    assert not dialog.previous.isEnabled()
    assert all(control.isEnabled() for control in dialog.query_controls)
    dialog.offset = 100
    dialog.rows = [sample_row(matched_count=101)]
    dialog.worker = Mock()
    dialog.task_finished()
    assert dialog.previous.isEnabled() and not dialog.next.isEnabled()


def test_close_is_blocked_only_while_worker_runs(dialog):
    event = QCloseEvent()
    dialog.worker = Mock()
    dialog.closeEvent(event)
    assert not event.isAccepted()
    dialog.worker = None
    event = QCloseEvent()
    dialog.closeEvent(event)
    assert event.isAccepted()


def test_load_page_captures_filters_and_uses_consistent_transaction(
    dialog, monkeypatch
):
    connection = Mock(wraps=Connection())
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    dialog.connection_factory = lambda: connection
    task = Mock()
    monkeypatch.setattr(dialog, "run_task", task)
    dialog.load_page()
    operation, handler = task.call_args.args
    rows, summary = operation()
    assert rows == [] and all(row["count"] == 0 for row in summary)
    assert "REPEATABLE READ READ ONLY" in connection.execute.call_args_list[0].args[0]
    assert handler == dialog.show_page


def test_pdf_export_uses_only_summary_and_existing_engine(dialog, monkeypatch):
    task = Mock()
    monkeypatch.setattr(dialog, "run_task", task)
    renderer = Mock()
    monkeypatch.setattr("report_engine.ReportHTMLRenderer", Mock(return_value=renderer))
    dialog.create_report()
    operation, handler = task.call_args.args
    operation()
    context, path = renderer.render_pdf.call_args.args
    assert (
        context["mode"] == "self_pay"
        and context["data"]["period"] == "2026-10-01 al 2026-10-03"
    )
    assert Path(path).suffix == ".pdf" and handler == dialog.preview


def test_excel_export_and_cancel(dialog, monkeypatch, tmp_path):
    destination = str(tmp_path / "cobros.xlsx")
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", Mock(return_value=("", "")))
    task = Mock()
    monkeypatch.setattr(dialog, "run_task", task)
    dialog.export_report()
    task.assert_not_called()
    monkeypatch.setattr(
        ui.QFileDialog, "getSaveFileName", Mock(return_value=(destination, ""))
    )
    dialog.export_report()
    operation, handler = task.call_args.args
    operation()
    assert Path(destination).exists()
    handler(None)
    assert "exportado" in dialog.message.text()


def test_summary_excel_and_html_preserve_cents_and_escape_metadata(tmp_path):
    summary = payment_summary(
        [
            sample_row(),
            sample_row(
                coverage="NO_ASEGURADO", payment_status="EXONERADO", total="100.01"
            ),
        ]
    )
    filters = {"start": "2026-10-01", "end": "2026-10-03"}
    path = tmp_path / "summary.xlsx"
    ui.export_summary(path, summary, filters)
    sheet = load_workbook(path).active
    assert sheet["D3"].value == 5.99 and sheet["D4"].value == 0
    assert sheet["F4"].value == 100.01 and sheet["C3"].value == 1
    html = ReportHTMLRenderer().render_html(
        ui.report_context(summary, filters, "<script>x</script>", "")
    )
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "Recaudado" in html and "5.99" in html and "100.01" in html


def test_history_without_new_receipt_callback_and_idle_completion(qt_application):
    dialog = ui.SelfPayHistoryDialog(
        lambda: Connection(),
        actor="QA",
        logo="",
        open_receipt=Mock(),
        edit_receipt=Mock(),
        preview=Mock(),
    )
    assert not any(button.text().startswith("Nuevo") for button in dialog.buttons)
    dialog.task_finished()
    assert dialog.worker is None
    dialog.close()


def test_pending_filter_and_summary_are_readable(dialog):
    dialog.status.setCurrentIndex(dialog.status.findData("PENDIENTE_PAGO"))
    assert dialog.filters()["status"] == "PENDIENTE_PAGO"
    row = sample_row(payment_status="PENDIENTE_PAGO")
    dialog.show_page(([row], payment_summary([row])))
    assert dialog.table.item(0, 4).text() == "Pendiente de pago"
    assert "1 pendientes de pago" in dialog.summary.text()


def test_pending_report_html_includes_debt_separately():
    summary = payment_summary(
        [dict(coverage="EXTRANJERO", payment_status="PENDIENTE_PAGO", total="5.99")]
    )
    html = ReportHTMLRenderer().render_html(
        ui.report_context(summary, dict(start="2026-10-01", end="2026-10-03"), "QA", "")
    )
    assert "Pendientes de pago" in html and "Importe pendiente" in html
    assert "5.99" in html and "RD$ 0.00" in html
