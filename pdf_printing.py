"""Submit PDF pages to the Windows print engine already loaded by Qt.

Each call owns its document, painter and printer. A failure after begin() is
never retried through another backend: the spooler may have accepted pages.
"""

from pathlib import Path
from time import perf_counter
from typing import Callable

from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QGuiApplication, QPageLayout, QPageSize, QPainter
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo

PRINT_DPI = 300


class NativePrintingUnavailable(RuntimeError):
    """No Qt application is running; no print job has been started."""


def fit_page_rect(width: float, height: float, area: QRectF) -> QRectF:
    if min(width, height, area.width(), area.height()) <= 0:
        raise ValueError("La página o el área de impresión no tiene un tamaño válido.")
    scale = min(area.width() / width, area.height() / height)
    w, h = width * scale, height * scale
    return QRectF(
        area.x() + (area.width() - w) / 2, area.y() + (area.height() - h) / 2, w, h
    )


def configure_letter_page(printer: QPrinter, page_size) -> None:
    orientation = (
        QPageLayout.Orientation.Landscape
        if page_size.width() > page_size.height()
        else QPageLayout.Orientation.Portrait
    )
    if not printer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter)):
        raise RuntimeError("La impresora no aceptó papel Carta.")
    if not printer.setPageOrientation(orientation):
        raise RuntimeError("La impresora no aceptó la orientación de la hoja.")


def default_printer() -> QPrinter:
    info = QPrinterInfo.defaultPrinter()
    if info.isNull():
        raise RuntimeError("No hay una impresora predeterminada disponible.")
    printer = QPrinter(info, QPrinter.PrinterMode.PrinterResolution)
    if not printer.isValid():
        raise RuntimeError("La impresora predeterminada no está disponible.")
    return printer


def render_print_page(document: QPdfDocument, index: int, printer: QPrinter):
    page = document.pagePointSize(index)
    printable = printer.pageRect(QPrinter.Unit.DevicePixel)
    # QPainter's origin is already the top-left of the printable area.
    target = fit_page_rect(
        page.width(), page.height(), QRectF(0, 0, printable.width(), printable.height())
    )
    image = document.render(
        index, QSize(max(1, round(target.width())), max(1, round(target.height())))
    )
    if image.isNull():
        raise RuntimeError("No se pudo preparar una página para imprimir.")
    return target, image


def paint_document(document: QPdfDocument, printer: QPrinter, copies: int) -> None:
    hardware_copies = printer.supportsMultipleCopies()
    printer.setCopyCount(copies if hardware_copies else 1)
    printer.setCollateCopies(True)
    configure_letter_page(printer, document.pagePointSize(0))
    # Validate and rasterize the first page before any job can reach the spooler.
    first = render_print_page(document, 0, printer)
    painter = QPainter()
    if not painter.begin(printer):
        raise RuntimeError("Windows no pudo iniciar la impresión.")
    try:
        _paint_pages(
            document, printer, painter, first, 1 if hardware_copies else copies
        )
        if not painter.end():
            raise RuntimeError("Windows no confirmó el envío completo de la impresión.")
        if printer.printerState() in (
            QPrinter.PrinterState.Error,
            QPrinter.PrinterState.Aborted,
        ):
            raise RuntimeError("El controlador notificó un error de impresión.")
    except Exception:
        printer.abort()
        raise
    finally:
        if painter.isActive():
            painter.end()


def _paint_pages(document, printer, painter, first, passes: int) -> None:
    for copy in range(passes):
        for index in range(document.pageCount()):
            if copy or index:
                configure_letter_page(printer, document.pagePointSize(index))
                if not printer.newPage():
                    raise RuntimeError("La impresora rechazó una página de la hoja.")
            target, image = (
                first if index == 0 else render_print_page(document, index, printer)
            )
            painter.drawImage(target, image)


def print_pdf_native(
    path: str,
    *,
    copies: int = 1,
    printer: QPrinter | None = None,
    log: Callable[[str], None] | None = None,
) -> None:
    if QGuiApplication.instance() is None:
        raise NativePrintingUnavailable(
            "No hay una aplicación Qt activa para imprimir."
        )
    if copies < 1:
        raise ValueError("La cantidad de copias debe ser positiva.")
    started = perf_counter()
    stage = "LOAD"
    document = QPdfDocument()
    try:
        if (
            document.load(str(Path(path).resolve())) != QPdfDocument.Error.None_
            or document.pageCount() < 1
        ):
            raise ValueError("El PDF no contiene páginas válidas para imprimir.")
        stage = "PRINTER"
        printer = printer if printer is not None else default_printer()
        printer.setResolution(PRINT_DPI)
        printer.setFullPage(False)
        printer.setDocName("SIGEH")
        stage = "SPOOL"
        paint_document(document, printer, copies)
        stage = "SUBMITTED"
    finally:
        document.close()
        if log:
            log(
                f"PDF_PRINT_DISPATCH backend=NATIVE_QT stage={stage} paper=LETTER copies={copies} elapsed_ms={(perf_counter() - started) * 1000:.1f}"
            )
