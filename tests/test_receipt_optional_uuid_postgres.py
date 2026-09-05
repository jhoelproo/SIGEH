"""Receipt persistence against a disposable PostgreSQL, never production."""

import pytest
from concurrent.futures import ThreadPoolExecutor
from psycopg2.pool import ThreadedConnectionPool

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server


@pytest.fixture
def receipts(server, tmp_path, monkeypatch):
    pool = ThreadedConnectionPool(1, 4, server)
    monkeypatch.setattr(app, "db_pool", pool)
    monkeypatch.setattr(app, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(app, "PDFS_DIR", str(tmp_path / "pdfs"))
    monkeypatch.setattr(app, "write_runtime_log", lambda message: None)
    monkeypatch.setattr(
        app,
        "get_user",
        lambda username: {
            "username": username,
            "role": app.ROLE_ADMIN,
        },
    )
    try:
        with app.db_connect() as con:
            con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        app.db_init()
        yield
    finally:
        pool.closeall()


def save(**changes):
    values = dict(
        recibo_id=None,
        numero=1,
        nombre="PACIENTE SINTETICO",
        fecha="2026-09-05",
        dx="PRUEBA",
        ars="FUTURO",
        sala=0,
        total=100,
        pdf_filename="",
        username="uuid_test",
        is_backdated=False,
        created_at="2026-09-05 10:00:00",
        grouped=[("Procedimientos", [("PRUEBA", 100, 1, 100, "Procedimientos")])],
        authorization_number="123456789",
        admission_attention=None,
        verification_bypass={
            "role": app.ROLE_ADMIN,
            "reason": "Prueba controlada",
            "device": "TEST",
        },
    )
    values.update(changes)
    return app.save_receipt_with_items(**values)


def test_bypass_valid_authorization_without_admission_is_null(receipts):
    receipt_id = save()
    with app.db_connect() as con:
        row = con.execute("SELECT * FROM recibos WHERE id=%s", (receipt_id,)).fetchone()
        assert row["admission_global_attention_id"] is None
        assert row["admission_atencion_id"] is None
        assert row["admission_source_instance_id"] is None
        assert row["estado_documento"] == app.DOCUMENT_READY
        assert row["review_status"] == "CLEAR"
        assert row["verification_bypassed"]
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1
        assert (
            con.execute(
                "SELECT COUNT(*) FROM admission_attention_projection"
            ).fetchone()[0]
            == 0
        )
        assert (
            con.execute("SELECT COUNT(*) FROM admission_sync_events").fetchone()[0] == 0
        )
        assert row["verification_bypass_by"] == "uuid_test"
        assert row["verification_bypass_reason"] == "Prueba controlada"
        assert float(row["total"]) == 100


@pytest.mark.parametrize(
    "authorization,state,review,reason",
    [
        (
            "",
            app.DOCUMENT_PRELIMINARY,
            app.AUTH_REVIEW_NOT_APPLICABLE,
            "AUTHORIZATION_MISSING",
        ),
        (
            "ABC123",
            app.DOCUMENT_READY,
            app.AUTH_REVIEW_PENDING,
            "INVALID_AUTHORIZATION_FORMAT",
        ),
        ("1", app.DOCUMENT_READY, app.AUTH_REVIEW_PENDING, "AUTHORIZATION_TOO_SHORT"),
    ],
)
def test_bypass_authorization_variants(receipts, authorization, state, review, reason):
    receipt_id = save(authorization_number=authorization)
    with app.db_connect() as con:
        row = con.execute("SELECT * FROM recibos WHERE id=%s", (receipt_id,)).fetchone()
        assert (
            row["estado_documento"],
            row["review_status"],
            row["review_reason"],
        ) == (state, review, reason)
        assert row["admission_global_attention_id"] is None
        assert row["numero_autorizacion"] == authorization


def test_edit_bypass_preserves_null_and_updates_items(receipts):
    receipt_id = save(authorization_number="")
    assert save(recibo_id=receipt_id) == receipt_id
    with app.db_connect() as con:
        row = con.execute("SELECT * FROM recibos WHERE id=%s", (receipt_id,)).fetchone()
        assert row["admission_global_attention_id"] is None
        assert row["estado_documento"] == app.DOCUMENT_READY
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1


def test_rollback_after_items_then_retry_one_receipt(receipts, monkeypatch):
    snapshot = app.save_receipt_document_snapshot

    def fail_after_items(con, *args, **kwargs):
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1
        raise RuntimeError("simulated snapshot failure")

    monkeypatch.setattr(app, "save_receipt_document_snapshot", fail_after_items)
    with pytest.raises(RuntimeError, match="simulated snapshot"):
        save()
    with app.db_connect() as con:
        for table in (
            "recibos",
            "recibo_items",
            "recibo_facturacion_history",
            "action_history",
        ):
            assert con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(app, "save_receipt_document_snapshot", snapshot)
    save()
    with pytest.raises(app.DuplicateReceiptError):
        save(numero=2)
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1


def test_concurrent_double_save_one_receipt(receipts):
    def attempt(number):
        try:
            save(numero=number)
            return "saved"
        except app.DuplicateReceiptError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, [1, 2])) == ["duplicate", "saved"]
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1


@pytest.mark.parametrize(
    "identifier", [None, "", "   ", "11111111-1111-4111-8111-111111111111"]
)
def test_legacy_and_valid_uuid_persistence_boundary(receipts, monkeypatch, identifier):
    # Isolate UUID persistence from eligibility, covered by real claim/bridge tests.
    monkeypatch.setattr(
        app,
        "_lock_and_validate_admission_processing",
        lambda *args, **kwargs: {"already_linked": True},
    )
    attention = {
        "attention_id": 10,
        "patient_id": 20,
        "ars": "FUTURO",
        "source_instance_id": "   ",
        "global_attention_id": identifier,
    }
    receipt_id = save(admission_attention=attention, verification_bypass=None)
    with app.db_connect() as con:
        row = con.execute("SELECT * FROM recibos WHERE id=%s", (receipt_id,)).fetchone()
        assert row["admission_global_attention_id"] == (
            (identifier or "").strip() or None
        )
        assert row["admission_source_instance_id"] is None
    assert (
        save(
            recibo_id=receipt_id,
            admission_attention=attention,
            verification_bypass=None,
        )
        == receipt_id
    )
