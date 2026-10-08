"""State correction and ownership guards on disposable loopback PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor

import pytest

import CALCULOS_QT as app
import receipt_documents
from tests.test_billing_consistency_postgres import server as server
from tests.test_inherited_receipt_save import GLOBAL, USER, inherited as inherited
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


@pytest.mark.parametrize("readiness", ["INCOMPLETA", "PENDIENTE", ""])
def test_existing_pending_receipt_remains_correctable_after_readiness_change(
    inherited, readiness
):
    receipt_id = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    with app.db_connect() as con:
        con.execute(
            "UPDATE admission_attention_projection SET readiness=%s", (readiness,)
        )
    live = app.get_projected_billable_attention(
        372,
        "ORIGIN",
        current_user=USER,
        global_attention_id=GLOBAL,
        receipt_id=receipt_id,
        session_id="A",
    )
    assert live is not None
    cached = inherited.snapshot()
    cached["billing_readiness"] = readiness
    assert (
        save(
            recibo_id=receipt_id,
            admission_attention=cached,
            admission_session_id="A",
            verification_bypass=None,
            dx="CORRECCION SINTETICA",
        )
        == receipt_id
    )
    with app.db_connect() as con:
        row = con.execute(
            "SELECT dx,revision_version FROM recibos WHERE id=%s", (receipt_id,)
        ).fetchone()
        assert row["dx"] == "CORRECCION SINTETICA"
        assert row["revision_version"] == 1
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1


@pytest.mark.parametrize(
    "status,document", [("FACTURADO", "FINAL"), ("PENDIENTE", "FINAL")]
)
def test_final_receipt_is_never_editable_by_readiness_exception(
    inherited, status, document
):
    receipt_id = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    with app.db_connect() as con:
        con.execute("UPDATE admission_attention_projection SET readiness='INCOMPLETA'")
        con.execute(
            "UPDATE recibos SET estado_facturacion=%s,estado_documento=%s",
            (status, document),
        )
    with pytest.raises((ValueError, app.AdmissionAttentionUnavailableError)):
        save(
            recibo_id=receipt_id,
            admission_attention=inherited,
            admission_session_id="A",
            verification_bypass=None,
        )
    with app.db_connect() as con:
        assert con.execute("SELECT revision_version FROM recibos").fetchone()[0] == 0


def test_old_billed_snapshot_is_finalized_once_without_changing_historical_version(
    receipts, monkeypatch
):
    receipt_id = save(authorization_number="")
    with app.db_connect() as con:
        before = receipt_documents.load_current_receipt_snapshot(con, receipt_id)
        con.execute(
            "UPDATE recibos SET estado_facturacion='FACTURADO',estado_documento='FINAL' WHERE id=%s",
            (receipt_id,),
        )
    captured = []
    monkeypatch.setattr(
        app,
        "render_receipt_snapshot_pdf",
        lambda document, **kwargs: captured.append(document) or "synthetic-final.pdf",
    )
    assert app.resolve_receipt_document(receipt_id) == "synthetic-final.pdf"
    assert app.resolve_receipt_document(receipt_id) == "synthetic-final.pdf"
    final = captured[-1]
    assert final["version"] == 2
    assert final["snapshot_hash"] != before["snapshot_hash"]
    assert final["snapshot"]["header"]["document_state"] == "FINAL"
    assert final["snapshot"]["patient"]["admission_patient_id"] is None
    assert app._receipt_snapshot_cache_path(final) != app._receipt_snapshot_cache_path(
        before
    )
    with app.db_connect() as con:
        rows = con.execute(
            "SELECT version,estado_documento FROM recibo_document_versions ORDER BY version"
        ).fetchall()
        assert [(row["version"], row["estado_documento"]) for row in rows] == [
            (1, "PRELIMINAR"),
            (2, "FINAL"),
        ]


def test_snapshot_repair_rolls_back_on_failure(receipts, monkeypatch):
    receipt_id = save(authorization_number="")
    with app.db_connect() as con:
        con.execute("UPDATE recibos SET estado_facturacion='FACTURADO'")
    original = receipt_documents.save_receipt_document_snapshot

    def fail_after_append(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("SYNTHETIC SNAPSHOT FAILURE")

    monkeypatch.setattr(
        receipt_documents, "save_receipt_document_snapshot", fail_after_append
    )
    with pytest.raises(RuntimeError, match="SYNTHETIC SNAPSHOT FAILURE"):
        with app.db_connect() as con:
            receipt = dict(
                con.execute(
                    "SELECT * FROM recibos WHERE id=%s", (receipt_id,)
                ).fetchone()
            )
            current = receipt_documents.load_current_receipt_snapshot(con, receipt_id)
            receipt_documents.refresh_billed_receipt_snapshot(con, receipt, current)
    with app.db_connect() as con:
        assert (
            con.execute("SELECT COUNT(*) FROM recibo_document_versions").fetchone()[0]
            == 1
        )
        assert (
            con.execute("SELECT estado_documento FROM recibos").fetchone()[0]
            == "PRELIMINAR"
        )


def test_legacy_billed_document_is_reconstructed_before_old_binary(
    receipts, monkeypatch
):
    receipt_id = app.add_recibo(
        991000,
        "PACIENTE SINTETICO",
        "2026-10-06",
        "PRUEBA",
        "FUTURO",
        0,
        100,
        "old.pdf",
        "uuid_test",
    )
    app.add_recibo_item(receipt_id, "Procedimientos", "PRUEBA", 100, 1, 100)
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibos SET estado_facturacion='FACTURADO',estado_documento='PRELIMINAR'"
        )
    captured = []
    monkeypatch.setattr(
        app,
        "render_receipt_snapshot_pdf",
        lambda document, **kwargs: captured.append(document) or "synthetic-final.pdf",
    )
    monkeypatch.setattr(
        app,
        "_legacy_receipt_pdf_path",
        lambda *args: pytest.fail("Stale legacy PDF selected"),
    )
    assert app.resolve_receipt_document(receipt_id) == "synthetic-final.pdf"
    assert captured[0]["snapshot"]["header"]["document_state"] == "FINAL"
    assert captured[0]["snapshot"]["header"]["billing_status"] == "FACTURADO"


def test_concurrent_stale_document_opens_append_only_one_final_version(receipts):
    receipt_id = save(authorization_number="")
    with app.db_connect() as con:
        stale = receipt_documents.load_current_receipt_snapshot(con, receipt_id)
        con.execute("UPDATE recibos SET estado_facturacion='FACTURADO'")
    receipt = {"id": receipt_id, "estado_facturacion": "FACTURADO"}

    def repair(_index):
        with app.db_connect() as con:
            return receipt_documents.refresh_billed_receipt_snapshot(
                con, receipt, stale
            )["version"]

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(repair, (1, 2))) == [2, 2]
    with app.db_connect() as con:
        assert (
            con.execute("SELECT COUNT(*) FROM recibo_document_versions").fetchone()[0]
            == 2
        )
        assert (
            con.execute(
                "SELECT COUNT(*) FROM recibo_document_versions WHERE is_current"
            ).fetchone()[0]
            == 1
        )
