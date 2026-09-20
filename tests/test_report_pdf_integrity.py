from pathlib import Path

import pytest
from PyPDF2 import PdfWriter

import report_documents as documents
from report_pdf_integrity import is_readable_report_pdf
from tests.test_report_snapshot_only_20260824 import _document, _snapshot


def write_pdf(path, *, pages=1, encrypted=False):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("synthetic")
    with Path(path).open("wb") as stream:
        writer.write(stream)


@pytest.mark.parametrize("content", [b"", b"not pdf", b"%PDF-1.4\ntruncated"])
def test_invalid_pdf_is_not_reused(tmp_path, content):
    path = tmp_path / "bad.pdf"
    path.write_bytes(content)
    assert not is_readable_report_pdf(path)


def test_pdf_requires_readable_pages(tmp_path):
    path = tmp_path / "document.pdf"
    assert not is_readable_report_pdf(path)
    write_pdf(path, pages=0)
    assert not is_readable_report_pdf(path)
    write_pdf(path, encrypted=True)
    assert not is_readable_report_pdf(path)
    write_pdf(path)
    assert is_readable_report_pdf(path)


def test_corrupt_cache_rebuilds_from_original_snapshot(tmp_path, monkeypatch):
    calls = []

    def render(self, context, destination, **kwargs):
        calls.append(context)
        write_pdf(destination)

    monkeypatch.setattr(documents, "report_cache_root", lambda: tmp_path)
    monkeypatch.setattr(documents.ReportHTMLRenderer, "render_pdf", render)
    document = _document(_snapshot())
    path = Path(documents.render_report_snapshot_pdf(document))
    path.write_bytes(b"corrupted cache")
    rebuilt = documents.render_report_snapshot_pdf(document)
    assert is_readable_report_pdf(rebuilt)
    assert len(calls) == 2
    assert calls[0] == calls[1]


def test_bad_render_cannot_replace_existing_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(documents, "report_cache_root", lambda: tmp_path)
    monkeypatch.setattr(
        documents.ReportHTMLRenderer,
        "render_pdf",
        lambda self, context, path, **kwargs: Path(path).write_bytes(b"invalid"),
    )
    with pytest.raises(documents.ReportDocumentError):
        documents.render_report_snapshot_pdf(_document(_snapshot()))
    assert list(tmp_path.iterdir()) == []


def test_history_view_displays_pages_and_rejects_invalid_pdf(tmp_path, monkeypatch):
    import CALCULOS_QT as app
    from PySide6.QtPdfWidgets import QPdfView

    application = app.QApplication.instance() or app.QApplication([])
    path = tmp_path / "history.pdf"
    write_pdf(path, pages=2)
    dialog = app.ComparisonPdfDialog(str(path), dialog_title="Reporte histórico")
    dialog.show()
    application.processEvents()
    view = dialog.findChild(QPdfView)
    assert view.document().pageCount() == 2
    assert dialog._pdf_document is view.document()
    assert dialog._pdf_view is view
    assert dialog.isVisible()
    dialog.close()
    assert not dialog.isVisible()
    path.write_bytes(b"invalid")
    with pytest.raises(OSError, match="páginas legibles"):
        app.ComparisonPdfDialog(str(path), dialog_title="Reporte histórico")


def test_packaged_report_viewer_self_test_contract(tmp_path):
    import CALCULOS_QT as app

    pdf_path = tmp_path / "viewer-self-test.pdf"
    write_pdf(pdf_path, pages=1)
    assert app.run_report_viewer_self_test(str(pdf_path)) == 0


def test_regular_report_dialog_does_not_require_embedded_pdf(tmp_path):
    import CALCULOS_QT as app

    application = app.QApplication.instance() or app.QApplication([])
    path = tmp_path / "external-report.pdf"
    path.write_bytes(b"external viewer handles this path")
    dialog = app.ComparisonPdfDialog(str(path))
    dialog.show()
    application.processEvents()
    assert dialog.isVisible()
    dialog.close()
