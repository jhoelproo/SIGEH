"""Validate disposable PDF renders before reusing or publishing them."""

from pathlib import Path

from PyPDF2 import PdfReader


def is_readable_report_pdf(path: str | Path) -> bool:
    try:
        with Path(path).open("rb") as stream:
            document = PdfReader(stream, strict=True)
            return not document.is_encrypted and len(document.pages) > 0
    except Exception:
        return False
