"""Receipt/list corrections against an isolated PostgreSQL database."""

import pytest
from concurrent.futures import ThreadPoolExecutor

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


USER = {"username": "uuid_test", "role": app.ROLE_ADMIN}


def batch(receipt_id, status="PENDIENTE", version=1):
    with app.db_connect() as con:
        identity = con.execute(
            """INSERT INTO billing_batches(period_year,period_month,ars,version,
               status,cutoff_at,created_at,created_by,sent_receipt_count,sent_total,
               sent_invoice_number,sent_ncf,sent_ars,sent_period_year,sent_period_month)
               VALUES(2026,9,'FUTURO',%s,%s,'2026-09-30','2026-09-01','test',
                      1,100,'TEST','TEST','FUTURO',2026,9)
               RETURNING id""",
            (version, status),
        ).fetchone()[0]
        con.execute(
            """INSERT INTO billing_batch_receipts(batch_id,recibo_id,included,
               document_type_snapshot,authorization_snapshot,billing_date_snapshot,
               total_snapshot,added_at,added_by)
               VALUES(%s,%s,1,'NSS','','2026-09-05',100,'2026-09-01','test')""",
            (identity, receipt_id),
        )
    return identity


def correct(batch_id, receipt_id, authorization="00001234"):
    app.update_monthly_batch_receipt_export_data(
        batch_id,
        receipt_id,
        document_type="NSS",
        document_number="001234567",
        authorization=authorization,
        specialty="GENERAL",
        user=USER,
    )


def test_existing_list_reads_current_authorization_without_faking_readiness(receipts):
    identity = save(authorization_number="00001234")
    entry = app.list_monthly_batch_receipts(batch(identity))[0]
    assert entry["authorization_snapshot"] == "00001234"
    assert app.MonthlyBillingListsPage._receipt_problems(entry)


def test_list_correction_updates_receipt_and_reconstructible_document(receipts):
    identity = save(authorization_number="")
    correct(batch(identity), identity)
    data = app.get_recibo_data(identity)
    assert data["numero_autorizacion"] == "00001234"
    assert data["admission_nss_snapshot"] == "001234567"
    with app.db_connect() as con:
        document = app.load_current_receipt_snapshot(con, identity)["snapshot"]
    assert document["header"]["authorization_number"] == "00001234"
    assert document["patient"]["nss"] == "001234567"


def test_receipt_correction_updates_pending_list_and_keeps_sent_snapshot(receipts):
    identity = save(authorization_number="")
    pending = batch(identity)
    sent_identity = save(
        numero=2, nombre="OTRO PACIENTE SINTETICO", authorization_number=""
    )
    sent = batch(sent_identity, "ENVIADO", 2)
    save(recibo_id=identity, authorization_number="00005678")
    save(
        recibo_id=sent_identity,
        numero=2,
        nombre="OTRO PACIENTE SINTETICO",
        authorization_number="00005678",
    )
    assert (
        app.list_monthly_batch_receipts(pending)[0]["authorization_snapshot"]
        == "00005678"
    )
    assert app.list_monthly_batch_receipts(sent)[0]["authorization_snapshot"] == ""


def test_last_saved_correction_wins_in_both_directions(receipts):
    identity = save(authorization_number="")
    pending = batch(identity)
    correct(pending, identity, "00001234")
    save(recibo_id=identity, authorization_number="00005678")
    assert (
        app.list_monthly_batch_receipts(pending)[0]["authorization_snapshot"]
        == "00005678"
    )
    correct(pending, identity, "00009999")
    assert app.get_recibo_data(identity)["numero_autorizacion"] == "00009999"


def quick(identity, authorization="00001234", expected=""):
    return app.update_receipt_authorization(
        identity, authorization, USER, expected_authorization=expected
    )


@pytest.mark.parametrize("operation", ["quick", "full"])
def test_authorization_edit_preserves_legacy_list_only_identity(receipts, operation):
    identity = save(authorization_number="")
    pending = batch(identity)
    with app.db_connect() as con:
        con.execute(
            """UPDATE billing_batch_receipts SET nss_snapshot='000123456',
               document_number_snapshot='000123456',specialty_snapshot='GENERAL',
               last_edited_at='2099-01-01' WHERE batch_id=%s""",
            (pending,),
        )
    quick(identity) if operation == "quick" else save(
        recibo_id=identity, authorization_number="00001234"
    )
    assert app.get_recibo_data(identity)["admission_nss_snapshot"] == "000123456"
    assert (
        app.list_monthly_batch_receipts(pending)[0]["document_number_snapshot"]
        == "000123456"
    )


def test_quick_edit_preserves_financials_items_origin_and_status(receipts, tmp_path):
    identity = save(authorization_number="")
    pending = batch(identity)
    previous = app.get_recibo_data(identity)
    quick(identity)
    current = app.get_recibo_data(identity)
    for field in (
        "total",
        "sala",
        "ars",
        "numero",
        "username",
        "created_at",
        "fecha",
        "items",
        "estado_facturacion",
    ):
        assert current[field] == previous[field]
    assert current["revision_version"] == previous["revision_version"] + 1
    assert current["numero_autorizacion"] == "00001234"
    assert current["estado_documento"] == app.DOCUMENT_READY
    assert (
        app.list_monthly_batch_receipts(pending)[0]["authorization_snapshot"]
        == "00001234"
    )
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM recibo_facturacion_history WHERE evento_tipo='CORRECCION_DATOS_RECIBO'"
            ).fetchone()[0]
            == 1
        )
        document = app.load_current_receipt_snapshot(con, identity)
    rendered = []

    def renderer(data, path, **_options):
        rendered.append(data["numero_autorizacion"])
        from pathlib import Path

        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"%PDF-1.4 synthetic QA")

    from types import SimpleNamespace

    app.resolve_receipt_document_path(
        identity, renderer=SimpleNamespace(render_pdf=renderer)
    )
    assert rendered == ["00001234"]
    assert document["snapshot"]["header"]["authorization_number"] == "00001234"


def test_noop_does_not_create_duplicate_document_version(receipts):
    identity = save(authorization_number="00001234")
    quick(identity, expected="00001234")
    assert app.get_recibo_data(identity)["revision_version"] == 0
    with app.db_connect() as con:
        assert (
            con.execute("SELECT COUNT(*) FROM recibo_document_versions").fetchone()[0]
            == 1
        )


@pytest.mark.parametrize(
    "alteration", ["deleted", "facturado", "self_pay", "changed", "missing"]
)
def test_invalid_or_stale_quick_edit_rolls_back(receipts, alteration):
    identity = save(authorization_number="")
    with app.db_connect() as con:
        statements = {
            "deleted": "UPDATE recibos SET is_deleted=1 WHERE id=%s",
            "facturado": "UPDATE recibos SET estado_facturacion='FACTURADO' WHERE id=%s",
            "self_pay": "UPDATE recibos SET tipo_cobertura='EXTRANJERO' WHERE id=%s",
            "changed": "UPDATE recibos SET numero_autorizacion='9999' WHERE id=%s",
        }
        if alteration in statements:
            con.execute(statements[alteration], (identity,))
    with pytest.raises(ValueError):
        quick(identity + 1 if alteration == "missing" else identity)
    assert app.get_recibo_data(identity)["revision_version"] == 0


@pytest.mark.parametrize("operation", ["quick", "list"])
def test_document_failure_rolls_back_receipt_list_and_audit(
    receipts, monkeypatch, operation
):
    identity = save(authorization_number="")
    pending = batch(identity)

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated document failure")

    monkeypatch.setattr(app, "save_receipt_document_snapshot", fail)
    with pytest.raises(RuntimeError):
        quick(identity) if operation == "quick" else correct(pending, identity)
    assert app.get_recibo_data(identity)["numero_autorizacion"] == ""
    assert app.list_monthly_batch_receipts(pending)[0]["authorization_snapshot"] == ""
    with app.db_connect() as con:
        assert (
            con.execute("SELECT COUNT(*) FROM billing_batch_events").fetchone()[0] == 0
        )
        assert (
            con.execute(
                "SELECT COUNT(*) FROM recibo_facturacion_history WHERE evento_tipo='CORRECCION_DATOS_RECIBO'"
            ).fetchone()[0]
            == 0
        )


def test_two_stations_cannot_silently_overwrite_new_authorization(receipts):
    identity = save(authorization_number="")

    def attempt(value):
        try:
            quick(identity, value)
            return "saved"
        except ValueError:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(attempt, ["1234", "5678"]))
    assert sorted(results) == ["saved", "stale"]


@pytest.mark.parametrize("kind", ["missing", "removed", "sent"])
def test_invalid_list_membership_does_not_change_receipt(receipts, kind):
    identity = save(authorization_number="")
    pending = batch(identity, "ENVIADO" if kind == "sent" else "PENDIENTE")
    if kind == "removed":
        with app.db_connect() as con:
            con.execute("UPDATE billing_batch_receipts SET included=0")
    with pytest.raises(ValueError):
        correct(pending + 1 if kind == "missing" else pending, identity)
    assert app.get_recibo_data(identity)["numero_autorizacion"] == ""


def test_cedula_updates_shared_document_without_inventing_admission_link(receipts):
    identity = save(authorization_number="")
    pending = batch(identity)
    app.update_monthly_batch_receipt_export_data(
        pending,
        identity,
        document_type="CEDULA",
        document_number="00000000001",
        authorization="1234",
        specialty="",
        user=USER,
    )
    data = app.get_recibo_data(identity)
    assert data["admission_cedula_snapshot"] == "00000000001"
    assert data["admission_atencion_id"] is None
    assert (
        app.list_monthly_batch_receipts(pending)[0]["document_type_snapshot"]
        == "CÉDULA"
    )
