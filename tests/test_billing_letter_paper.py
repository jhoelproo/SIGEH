"""Receipt and report print devices also start with Letter instead of driver A4."""

from types import SimpleNamespace
from unittest.mock import Mock

from PyPDF2 import PdfReader, PdfWriter
from PySide6.QtGui import QPageLayout, QPageSize
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


def test_comparison_printer_uses_landscape_letter():
    application = QApplication.instance() or QApplication([])
    printer = app.ComparisonPdfDialog._printer(None)
    assert printer.pageLayout().pageSize().id() == QPageSize.PageSizeId.Letter
    assert printer.pageLayout().orientation() == QPageLayout.Orientation.Landscape
    assert application is not None


def test_html_receipt_pdf_uses_letter(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "PDFS_DIR", str(tmp_path))
    app.imprimir_factura_desde_html("<p>PRUEBA DE PAPEL CARTA</p>")
    paths = list(tmp_path.glob("factura_*.pdf"))
    assert len(paths) == 1
    page = PdfReader(paths[0]).pages[0]
    assert (float(page.mediabox.width), float(page.mediabox.height)) == (612, 792)
    assert application is not None


def test_shift_report_uses_letter_with_driver_isolated(tmp_path, monkeypatch):
    application = QApplication.instance() or QApplication([])
    source, target = tmp_path / "source.pdf", tmp_path / "printed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=792, height=612)
    with source.open("wb") as stream:
        writer.write(stream)
    printer = QPrinter()
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(target))
    factory = Mock(return_value=printer)
    factory.HighResolution = QPrinter.HighResolution
    factory.Unit = QPrinter.Unit
    monkeypatch.setattr(app, "QPrinter", factory)
    monkeypatch.setattr(
        app,
        "QPrinterInfo",
        SimpleNamespace(defaultPrinter=lambda: SimpleNamespace(isNull=lambda: False)),
    )
    app.MainWindow._print_shift_pdf_once(None, str(source))
    page = PdfReader(target).pages[0]
    assert (float(page.mediabox.width), float(page.mediabox.height)) == (792, 612)
    assert application is not None
