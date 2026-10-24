"""Native print failures/copies and a real Qt PDF paint device, without paper."""

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PyPDF2 import PdfReader, PdfWriter
from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

import pdf_printing as printing
from admission_pdf_layout import fit_admission_pages_to_letter


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def source_pdf(tmp_path):
    path = tmp_path / "synthetic.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    writer.add_blank_page(width=792, height=612)
    with path.open("wb") as stream:
        writer.write(stream)
    return path


@pytest.fixture
def paint_context(monkeypatch):
    printer = Mock()
    printer.setPageSize.return_value = True
    printer.setPageOrientation.return_value = True
    printer.supportsMultipleCopies.return_value = True
    printer.printerState.return_value = QPrinter.PrinterState.Idle
    document = Mock()
    document.pageCount.return_value = 2
    document.pagePointSize.return_value = QSizeF(612, 792)
    painter = Mock()
    painter.end.return_value = True
    painter.isActive.return_value = False
    monkeypatch.setattr(printing, "QPainter", lambda: painter)
    monkeypatch.setattr(
        printing, "render_print_page", Mock(return_value=(QRectF(0, 0, 10, 20), Mock()))
    )
    return document, printer, painter


@pytest.mark.parametrize(
    "hardware,copies,draws,new_pages",
    [(True, 3, 2, 1), (False, 3, 6, 5), (False, 1, 2, 1)],
)
def test_copies_are_one_job_with_no_extra_processes(
    paint_context, hardware, copies, draws, new_pages
):
    document, printer, painter = paint_context
    printer.supportsMultipleCopies.return_value = hardware
    printing.paint_document(document, printer, copies)
    printer.setCopyCount.assert_called_once_with(copies if hardware else 1)
    painter.begin.assert_called_once_with(printer)
    assert painter.drawImage.call_count == draws
    assert printer.newPage.call_count == new_pages
    painter.end.assert_called_once()
    printer.abort.assert_not_called()


@pytest.mark.parametrize(
    "failure",
    ["begin", "end", "newPage", "render", "driver_error", "driver_aborted", "draw"],
)
def test_print_failure_aborts_without_starting_another_job(
    paint_context, monkeypatch, failure
):
    document, printer, painter = paint_context
    if failure in ("begin", "end"):
        getattr(painter, failure).return_value = False
    elif failure == "newPage":
        printer.newPage.return_value = False
    elif failure == "render":
        monkeypatch.setattr(
            printing,
            "render_print_page",
            Mock(side_effect=RuntimeError("render failed")),
        )
    elif failure == "draw":
        painter.drawImage.side_effect = RuntimeError("draw failed")
        painter.isActive.return_value = True
    else:
        printer.printerState.return_value = (
            QPrinter.PrinterState.Error
            if failure == "driver_error"
            else QPrinter.PrinterState.Aborted
        )
    with pytest.raises(RuntimeError):
        printing.paint_document(document, printer, 1)
    assert painter.begin.call_count <= 1
    if failure not in ("begin", "render"):
        printer.abort.assert_called_once()


@pytest.mark.parametrize("width,height", [(0, 1), (1, 0), (-1, 1)])
def test_invalid_page_dimensions(width, height):
    with pytest.raises(ValueError):
        printing.fit_page_rect(width, height, QRectF(0, 0, 10, 20))


def test_fit_preserves_aspect_ratio_and_centers():
    result = printing.fit_page_rect(100, 200, QRectF(5, 7, 300, 400))
    assert result == QRectF(55, 7, 200, 400)
    with pytest.raises(ValueError):
        printing.fit_page_rect(1, 1, QRectF(0, 0, 0, 20))


@pytest.mark.parametrize("bad_setting", ["setPageSize", "setPageOrientation"])
def test_driver_rejects_letter_before_starting_job(bad_setting):
    printer = Mock()
    getattr(printer, bad_setting).return_value = False
    with pytest.raises(RuntimeError):
        printing.configure_letter_page(printer, QSizeF(612, 792))


def test_default_printer_presence_and_health(monkeypatch):
    info = Mock()
    info.isNull.return_value = True
    monkeypatch.setattr(
        printing, "QPrinterInfo", SimpleNamespace(defaultPrinter=lambda: info)
    )
    with pytest.raises(RuntimeError, match="predeterminada"):
        printing.default_printer()
    info.isNull.return_value = False
    factory = Mock()
    factory.PrinterMode.PrinterResolution = QPrinter.PrinterMode.PrinterResolution
    factory.return_value.isValid.return_value = False
    monkeypatch.setattr(printing, "QPrinter", factory)
    with pytest.raises(RuntimeError, match="disponible"):
        printing.default_printer()
    factory.return_value.isValid.return_value = True
    assert printing.default_printer() is factory.return_value


def test_render_rejects_null_image():
    document, printer = Mock(), Mock()
    document.pagePointSize.return_value = QSizeF(612, 792)
    printer.pageRect.return_value = QRectF(20, 30, 500, 700)
    document.render.return_value.isNull.return_value = True
    with pytest.raises(RuntimeError, match="preparar"):
        printing.render_print_page(document, 0, printer)
    document.render.return_value.isNull.return_value = False
    rect, _ = printing.render_print_page(document, 0, printer)
    assert rect.x() == 0
    assert rect.y() >= 0


def test_no_application_is_the_only_automatic_fallback(monkeypatch):
    monkeypatch.setattr(
        printing, "QGuiApplication", SimpleNamespace(instance=lambda: None)
    )
    with pytest.raises(printing.NativePrintingUnavailable):
        printing.print_pdf_native("unused.pdf")


def test_invalid_input_never_opens_a_print_job(qt, tmp_path):
    printer, log = Mock(), Mock()
    with pytest.raises(ValueError):
        printing.print_pdf_native("unused.pdf", copies=0, printer=printer)
    invalid = tmp_path / "invalid.pdf"
    invalid.write_bytes(b"not a pdf")
    with pytest.raises(ValueError):
        printing.print_pdf_native(str(invalid), printer=printer, log=log)
    printer.setCopyCount.assert_not_called()
    assert "stage=LOAD" in log.call_args.args[0]


def test_real_qt_print_keeps_letter_and_landscape_without_a_dialog(
    qt, source_pdf, tmp_path
):
    target = tmp_path / "printed.pdf"
    printer = QPrinter()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(target))
    logs = []
    printing.print_pdf_native(str(source_pdf), printer=printer, log=logs.append)
    pages = PdfReader(target).pages
    assert [(float(p.mediabox.width), float(p.mediabox.height)) for p in pages] == [
        (612, 792),
        (792, 612),
    ]
    assert printer.resolution() == 300
    assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.Letter
    assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape
    assert "stage=SUBMITTED" in logs[0]


def test_printer_resolution_failure_is_logged_and_not_swallowed(
    qt, source_pdf, monkeypatch
):
    printer, log = Mock(), Mock()
    printer.setResolution.side_effect = RuntimeError("driver unavailable")
    monkeypatch.setattr(printing, "default_printer", lambda: printer)
    with pytest.raises(RuntimeError):
        printing.print_pdf_native(str(source_pdf), log=log)
    assert "stage=PRINTER" in log.call_args.args[0]


def test_real_qt_print_is_supported_inside_output_worker(qt, source_pdf, tmp_path):
    def worker():
        printer = QPrinter()
        printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
        printer.setOutputFileName(str(tmp_path / "worker.pdf"))
        printing.print_pdf_native(str(source_pdf), printer=printer, copies=2)

    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(worker).result(timeout=15)
    assert len(PdfReader(tmp_path / "worker.pdf").pages) == 4


def test_normalization_retains_content_and_rejects_invalid_boxes():
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    fit_admission_pages_to_letter(writer)
    page = writer.pages[0]
    assert tuple(page.mediabox) == (0, 0, 612, 792)
    fit_admission_pages_to_letter(writer)
    page.mediabox.upper_right = (0, 0)
    with pytest.raises(ValueError):
        fit_admission_pages_to_letter(writer)
