"""Historical receipt recovery preserves fallback order and explicit failures."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from receipt_documents import SnapshotMissingError


@pytest.fixture
def historical_receipt(monkeypatch, tmp_path):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {
        "id": 41,
        "numero": 990041,
        "document_storage_mode": app.STORAGE_LEGACY,
        "estado_facturacion": app.BILLING_PENDING,
        "pdf_filename": "historical.pdf",
    }

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(app, "db_connect", connect)
    for loader in ("load_current_receipt_snapshot", "load_latest_receipt_snapshot"):
        monkeypatch.setattr(
            app, loader, Mock(side_effect=SnapshotMissingError("absent"))
        )
    monkeypatch.setattr(
        app, "_legacy_receipt_pdf_path", Mock(side_effect=FileNotFoundError("absent"))
    )
    blob = Mock(side_effect=FileNotFoundError("absent"))
    structured = Mock(return_value={"snapshot": {"header": {"receipt_number": 990041}}})
    render = Mock(return_value=str(tmp_path / "recovered.pdf"))
    runtime_log = Mock()
    monkeypatch.setattr(app, "_stored_pdf_blob_path", blob)
    monkeypatch.setattr(app, "_structured_receipt_document", structured)
    monkeypatch.setattr(app, "render_receipt_snapshot_pdf", render)
    monkeypatch.setattr(app, "write_runtime_log", runtime_log)
    return connection, blob, structured, render, runtime_log


def test_missing_file_recovers_stored_binary_before_structured_document(
    historical_receipt, tmp_path
):
    connection, blob, structured, render, runtime_log = historical_receipt
    recovered = str(tmp_path / "binary.pdf")
    blob.side_effect = None
    blob.return_value = recovered

    assert app.resolve_receipt_document(41, "print") == recovered
    blob.assert_called_once_with(
        connection,
        filename="historical.pdf",
        namespace="receipt",
        identity="41",
        receipt_id=41,
    )
    structured.assert_not_called()
    render.assert_not_called()
    assert "accion=print" in runtime_log.call_args.args[0]
    assert "origen=blob_legado" in runtime_log.call_args.args[0]


def test_missing_file_and_binary_recovers_structured_historical_document(
    historical_receipt,
):
    connection, blob, structured, render, runtime_log = historical_receipt
    renderer = object()

    assert (
        app.resolve_receipt_document(41, "save_copy", renderer) == render.return_value
    )
    blob.assert_called_once()
    assert structured.call_args.args[0] is connection
    assert structured.call_args.args[1].receipt_id == 41
    render.assert_called_once_with(structured.return_value, renderer=renderer)
    assert "origen=estructura_historica" in runtime_log.call_args.args[0]


@pytest.mark.parametrize("failure", ["missing_structure", "renderer_failure"])
def test_unrecoverable_historical_document_reports_all_attempts(
    historical_receipt, failure
):
    _, blob, structured, render, runtime_log = historical_receipt
    if failure == "missing_structure":
        structured.side_effect = ValueError("no structured data")
    else:
        render.side_effect = RuntimeError("renderer unavailable")

    with pytest.raises(
        app.ReceiptDocumentError, match="información documental suficiente"
    ):
        app.resolve_receipt_document(41)

    blob.assert_called_once()
    structured.assert_called_once()
    if failure == "missing_structure":
        render.assert_not_called()
    else:
        render.assert_called_once()
    message = runtime_log.call_args.args[0]
    for origin in (
        "snapshot_actual",
        "snapshot_historico",
        "pdf_legado",
        "blob_legado",
        "estructura_historica",
    ):
        assert origin in message
    assert "id=41" in message
