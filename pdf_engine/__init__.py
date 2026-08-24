"""Motor de generación de recibos PDF de la aplicación."""

from .renderer import PDFRenderer, ReceiptPDFRenderer

__all__ = ["PDFRenderer", "ReceiptPDFRenderer"]
