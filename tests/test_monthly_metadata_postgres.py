"""Date and insurance corrections use isolated PostgreSQL transactions."""

import pytest
from pathlib import Path

import CALCULOS_QT as app
from monthly_receipt_fields import ensure_receipt_insurance_schema
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_inherited_receipt_save import inherited as inherited
from tests.test_receipt_ars_correction_postgres import linked as linked
from tests.test_receipt_list_consistency_postgres import USER, batch


def correct(identity, pending, **changes):
    values = dict(
        document_type="NSS",
        document_number="001234567",
        authorization="00001234",
        specialty="GENERAL",
        user=USER,
    )
    app.update_monthly_batch_receipt_export_data(
        pending, identity, **{**values, **changes}
    )


def test_insurance_migration_is_repeatable_and_preserves_receipts(receipts):
    identity = save()
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20261008124630_receipt_insurance_identification.sql"
    )
    with app.db_connect() as con:
        before = dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (identity,)).fetchone()
        )
        con.execute(migration.read_text(encoding="utf-8"))
        con.execute(migration.read_text(encoding="utf-8"))
        after = dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (identity,)).fetchone()
        )
    assert after == before


@pytest.mark.parametrize(
    "ars,kind",
    [
        ("RENACER", "NO. PÓLIZA"),
        ("HUMANO", "NO. AFILIADO"),
        ("PRIMERA", "NO. AFILIADO"),
        ("SEMMA", "NO. CARNET"),
    ],
)
def test_alternate_identity_survives_reopen_edit_and_new_list(receipts, ars, kind):
    identity = save(ars=ars)
    pending = batch(identity)
    with app.db_connect() as con:
        ensure_receipt_insurance_schema(con)
        ensure_receipt_insurance_schema(con)
        con.execute(
            "UPDATE recibos SET admission_nss_snapshot='123456789',admission_cedula_snapshot='00000000001' WHERE id=%s",
            (identity,),
        )
    correct(identity, pending, document_type=kind, document_number="000ABC001")
    current = app.get_recibo_data(identity)
    assert current["insurance_document_type"] == kind
    assert current["insurance_document_number"] == "000ABC001"
    assert current["admission_nss_snapshot"] == "123456789"
    assert current["admission_cedula_snapshot"] == "00000000001"
    assert (
        app.list_monthly_batch_receipts(pending)[0]["document_number_snapshot"]
        == "000ABC001"
    )
    with app.db_connect() as con:
        snapshot = app.load_current_receipt_snapshot(con, identity)["snapshot"]
    assert snapshot["patient"]["insurance_document_number"] == "000ABC001"
    save(recibo_id=identity, ars=ars, authorization_number="00009999")
    reopened = app.list_monthly_batch_receipts(pending)[0]
    assert reopened["document_number_snapshot"] == "000ABC001"
    assert reopened["authorization_snapshot"] == "00009999"
    with app.db_connect() as con:
        con.execute(
            "UPDATE billing_batch_receipts SET included=0 WHERE batch_id=%s", (pending,)
        )
    candidates = app.list_available_monthly_batch_candidates(
        pending, batch={"ars": ars}, date_from="2026-09-01", date_to="2026-09-30"
    )
    candidate = next(item for item in candidates if item.get("recibo_id") == identity)
    assert candidate["document_type_snapshot"] == kind
    assert candidate["document_number_snapshot"] == "000ABC001"


def test_service_date_correction_updates_document_and_all_pending_lists(receipts):
    identity = save()
    pending = batch(identity)
    # Reproduce legacy duplicate memberships in this disposable test database.
    with app.db_connect() as con:
        con.execute("DROP INDEX uq_billing_batch_receipts_receipt_global")
    other = batch(identity, version=2)
    sent = batch(identity, "ENVIADO", version=3)
    with app.db_connect() as con:
        con.execute(
            "UPDATE billing_batch_receipts SET service_date_snapshot='2026-09-05'"
        )
    before = app.get_recibo_data(identity)
    correct(
        identity, pending, service_date="06/09/2026", expected_service_date="2026-09-05"
    )
    current = app.get_recibo_data(identity)
    assert current["fecha"] == "2026-09-06"
    for field in ("total", "sala", "items", "created_at", "numero", "ars", "username"):
        assert current[field] == before[field]
    for item in (pending, other):
        assert (
            app.list_monthly_batch_receipts(item)[0]["service_date_snapshot"]
            == "2026-09-06"
        )
    assert (
        app.list_monthly_batch_receipts(sent)[0]["service_date_snapshot"]
        == "2026-09-05"
    )
    with app.db_connect() as con:
        snapshot = app.load_current_receipt_snapshot(con, identity)["snapshot"]
        assert snapshot["header"]["service_date"] == "2026-09-06"


def test_service_date_uses_final_duplicate_key_and_rolls_back_conflicts(receipts):
    identity = save()
    save(numero=2, fecha="2026-09-06")
    pending = batch(identity)
    with pytest.raises(app.DuplicateReceiptError):
        correct(
            identity,
            pending,
            service_date="2026-09-06",
            document_number="",
            patient_name="PACIENTE SINTETICO",
        )
    assert app.get_recibo_data(identity)["fecha"] == "2026-09-05"
    correct(
        identity,
        pending,
        service_date="2026-09-07",
        document_number="",
        patient_name="PACIENTE SINTETICO",
    )
    with pytest.raises(ValueError, match="otra pantalla"):
        correct(
            identity,
            pending,
            service_date="2026-09-08",
            expected_service_date="2026-09-05",
        )
    assert app.get_recibo_data(identity)["fecha"] == "2026-09-07"


@pytest.mark.parametrize("fail", [False, True])
def test_linked_service_date_and_specialty_sync_without_reassigning_shift(
    linked, monkeypatch, fail
):
    identity, _attention, _patient = linked
    pending = batch(identity)
    with app.db_connect() as con:
        before = dict(
            con.execute("SELECT * FROM admission_attention_projection").fetchone()
        )
    if fail:

        def reject(*_args, **_kwargs):
            raise RuntimeError("snapshot unavailable")

        monkeypatch.setattr(app, "save_receipt_document_snapshot", reject)
        with pytest.raises(RuntimeError, match="snapshot unavailable"):
            correct(identity, pending, service_date="2026-09-06", specialty="PEDIATRIA")
    else:
        correct(identity, pending, service_date="2026-09-06", specialty="PEDIATRIA")
    expected = "2026-09-05" if fail else "2026-09-06"
    with app.db_connect() as con:
        current = con.execute("SELECT * FROM admission_attention_projection").fetchone()
        assert current["service_date"] == expected
        assert current["turn_id"] == before["turn_id"]
        assert current["created_at_effective_utc"] == before["created_at_effective_utc"]
        if not fail:
            assert current["specialty"] == "PEDIATRÍA"
            assert current["latest_payload_json"]["service_date"] == expected
            event = con.execute(
                "SELECT payload_json FROM admission_sync_events ORDER BY sequence DESC LIMIT 1"
            ).fetchone()[0]
            assert event["service_date"] == expected
            assert event["specialty"] == "PEDIATRÍA"
    assert app.get_recibo_data(identity)["fecha"] == expected


@pytest.mark.parametrize("field", ["date", "authorization"])
def test_ordinary_linked_edit_updates_admission_metadata_without_master_identity(
    inherited, field
):
    identity = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    pending = batch(identity)
    values = (
        {"fecha": "2026-09-06"}
        if field == "date"
        else {"authorization_number": "00001234"}
    )
    with app.db_connect() as con:
        previous = dict(
            con.execute("SELECT * FROM admission_attention_projection").fetchone()
        )
        assert previous["global_patient_id"] is None
    save(
        recibo_id=identity,
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
        **values,
    )
    current_receipt = app.get_recibo_data(identity)
    with app.db_connect() as con:
        current = con.execute("SELECT * FROM admission_attention_projection").fetchone()
        assert (
            current["created_at_effective_utc"] == previous["created_at_effective_utc"]
        )
        assert current["turn_id"] == previous["turn_id"]
        if field == "date":
            assert current["service_date"] == current_receipt["fecha"] == "2026-09-06"
        else:
            assert (
                current["authorization_snapshot"]
                == current_receipt["numero_autorizacion"]
                == "00001234"
            )
    entry = app.list_monthly_batch_receipts(pending)[0]
    assert entry["service_date_snapshot"] == current_receipt["fecha"]
    assert entry["authorization_snapshot"] == current_receipt["numero_autorizacion"]
