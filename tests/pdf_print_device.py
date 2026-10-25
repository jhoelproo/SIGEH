"""Real PDF paint device without enumerating the operating system's printers."""

from PySide6.QtGui import QPdfWriter
from PySide6.QtPrintSupport import QPrinter


class PdfPrintDevice(QPdfWriter):
    def __init__(self, target):
        super().__init__(str(target))
        self.copy_count = 1
        self.collate = False
        self.full_page = False
        self._duplex = QPrinter.DuplexMode.DuplexNone
        self.aborted = False

    def setDocName(self, name):
        self.setTitle(name)

    def supportsMultipleCopies(self):
        return False

    def setCopyCount(self, count):
        self.copy_count = count

    def setCollateCopies(self, collate):
        self.collate = collate

    def setFullPage(self, enabled):
        self.full_page = enabled

    def pageRect(self, _unit):
        return self.pageLayout().paintRectPixels(self.resolution())

    def printerState(self):
        return QPrinter.PrinterState.Idle

    def setDuplex(self, mode):
        self._duplex = mode

    def duplex(self):
        return self._duplex

    def abort(self):
        self.aborted = True
