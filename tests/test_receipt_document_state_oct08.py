"""State and identity rendering cover confirmed, incomplete, and historical inputs."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest

import receipt_documents
import CALCULOS_QT as app
from pdf_engine import ReceiptPDFRenderer
from receipt_document_state import billed_snapshot_needs_refresh, receipt_document_state


@pytest.mark.parametrize(
    "state,billing,local,expected",
    [
        (None, None, "", "PRELIMINAR"),
        (" listo_auditoria ", "PENDIENTE", "", "LISTO_AUDITORIA"),
        ("PRELIMINAR", " facturado ", "", "FINAL"),
        ("FINAL", "FACTURADO", "local-id", "PRELIMINAR"),
        ("FINAL", "NO_FACTURADO", "", "FINAL"),
    ],
)
def test_confirmed_billing_state_and_local_drafts(state, billing, local, expected):
    assert receipt_document_state(state, billing, local_request_id=local) == expected


@pytest.mark.parametrize(
    "receipt,snapshot,expected",
    [
        ({}, {}, False),
        ({"estado_facturacion": "PENDIENTE"}, {}, False),
        ({"estado_facturacion": "FACTURADO"}, {}, True),
        (
            {"estado_facturacion": "FACTURADO"},
            {
                "header": {"document_state": "FINAL", "billing_status": "FACTURADO"},
                "document": {"document_state": "FINAL", "billing_status": "FACTURADO"},
            },
            False,
        ),
        (
            {"estado_facturacion": "FACTURADO"},
            {"header": {"document_state": "FINAL", "billing_status": "PENDIENTE"}},
            True,
        ),
    ],
)
def test_stale_billed_snapshots_are_distinguished_from_complete_versions(
    receipt, snapshot, expected
):
    assert billed_snapshot_needs_refresh(receipt, {"snapshot": snapshot}) is expected


def test_complete_snapshot_does_not_query_or_append_again():
    connection = Mock()
    document = {
        "snapshot": {
            section: {"document_state": "FINAL", "billing_status": "FACTURADO"}
            for section in ("header", "document")
        }
    }
    assert (
        receipt_documents.refresh_billed_receipt_snapshot(
            connection, {"estado_facturacion": "FACTURADO"}, document
        )
        is document
    )
    connection.execute.assert_not_called()


def test_receipt_deleted_before_snapshot_repair_is_explicit():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = None
    with pytest.raises(receipt_documents.SnapshotMissingError):
        receipt_documents.refresh_billed_receipt_snapshot(
            connection, {"id": 1, "estado_facturacion": "FACTURADO"}, {}
        )


def test_concurrent_reopening_uses_new_current_version(monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {
        "id": 1,
        "estado_facturacion": "PENDIENTE",
    }
    current = Mock(return_value={"version": 3})
    monkeypatch.setattr(receipt_documents, "load_current_receipt_snapshot", current)
    assert receipt_documents.refresh_billed_receipt_snapshot(
        connection, {"id": 1, "estado_facturacion": "FACTURADO"}, {}
    ) == {"version": 3}
    current.assert_called_once_with(connection, 1)
    assert connection.execute.call_count == 1


@pytest.mark.parametrize(
    "kind,number,label,shown",
    [
        (
            "NO. PÓLIZA",
            "000012345678901234567890",
            "NO. PÓLIZA",
            "000012345678901234567890",
        ),
        ("NO. AFILIADO", "HUM-001", "NO. AFILIADO", "HUM-001"),
        ("NO. CARNET", "SEM-001", "NO. CARNET", "SEM-001"),
        ("NSS", "987", "NSS", "001234567"),
        ("CÉDULA", "40200000000", "NSS", "001234567"),
        ("INVALID", "123", "NSS", "001234567"),
        (None, None, "NSS", "001234567"),
        ("NO. PÓLIZA", "", "NSS", "001234567"),
    ],
)
def test_alternate_insurance_identification_is_labeled_without_corrupting_nss(
    kind, number, label, shown
):
    renderer = ReceiptPDFRenderer()
    data = {
        "insurance_document_type": kind,
        "insurance_document_number": number,
        "nss": "001234567",
    }
    prepared = renderer._prepare_data(data)
    assert prepared["identity_label"] == label
    assert prepared["identity_number"] == shown
    assert prepared["nss"] == "001234567"
    html = renderer.render_html(data)
    assert f"<b>{label}:</b> <span>{shown}</span>" in html


def test_empty_identity_is_visible_and_alternate_values_are_escaped():
    renderer = ReceiptPDFRenderer()
    assert "NO REGISTRADO" in renderer.render_html({})
    html = renderer.render_html(
        {
            "insurance_document_type": "NO. PÓLIZA",
            "insurance_document_number": "<script>",
        }
    )
    assert "&lt;script&gt;" in html
    assert "<script>" not in html


def test_billed_hybrid_integrity_failure_cannot_select_any_fallback(monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {
        "id": 1,
        "numero": 991000,
        "estado_facturacion": "FACTURADO",
        "document_storage_mode": "HYBRID",
        "pdf_filename": "old.pdf",
    }

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(app, "db_connect", connect)
    monkeypatch.setattr(
        app,
        "load_current_receipt_snapshot",
        Mock(side_effect=receipt_documents.SnapshotHashError("CORRUPT")),
    )
    monkeypatch.setattr(
        app,
        "load_latest_receipt_snapshot",
        Mock(side_effect=receipt_documents.SnapshotHashError("CORRUPT")),
    )
    monkeypatch.setattr(
        app,
        "_structured_receipt_document",
        lambda *args: pytest.fail("Integrity failure hidden"),
    )
    monkeypatch.setattr(
        app,
        "_legacy_receipt_pdf_path",
        lambda *args: pytest.fail("Stale legacy PDF selected"),
    )
    with pytest.raises(receipt_documents.SnapshotHashError, match="CORRUPT"):
        app.resolve_receipt_document(1)


def test_repair_uses_completed_version_created_while_waiting_for_lock(monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {
        "id": 1,
        "estado_facturacion": "FACTURADO",
    }
    final = {
        "version": 2,
        "snapshot": {
            section: {"document_state": "FINAL", "billing_status": "FACTURADO"}
            for section in ("header", "document")
        },
    }
    monkeypatch.setattr(
        receipt_documents, "load_current_receipt_snapshot", lambda *args: final
    )
    assert (
        receipt_documents.refresh_billed_receipt_snapshot(
            connection, {"id": 1, "estado_facturacion": "FACTURADO"}, {}
        )
        is final
    )
    assert connection.execute.call_count == 1


def test_missing_current_marker_repairs_from_valid_historical_snapshot(monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = {
        "id": 1,
        "username": "",
        "estado_facturacion": "FACTURADO",
    }
    monkeypatch.setattr(
        receipt_documents,
        "load_current_receipt_snapshot",
        Mock(side_effect=receipt_documents.SnapshotMissingError("missing marker")),
    )
    append = Mock(return_value={"version": 2, "snapshot_hash": "new-hash"})
    monkeypatch.setattr(receipt_documents, "save_receipt_document_snapshot", append)
    stale = {
        "version": 1,
        "snapshot": {"document": {"document_title": "HISTORICAL TITLE"}},
    }
    result = receipt_documents.refresh_billed_receipt_snapshot(
        connection, {"id": 1, "estado_facturacion": "FACTURADO"}, stale
    )
    assert result["version"] == 2 and result["snapshot_hash"] == "new-hash"
    append.assert_called_once_with(
        connection,
        1,
        "Sistema",
        document_context={"document_title": "HISTORICAL TITLE"},
    )
    assert connection.execute.call_args.args == (
        "UPDATE recibos SET estado_documento='FINAL' WHERE id=%s",
        (1,),
    )
