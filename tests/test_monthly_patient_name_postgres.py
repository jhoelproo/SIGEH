"""Shared names and specialties, using disposable PostgreSQL only."""

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_inherited_receipt_save import inherited as inherited
from tests.test_receipt_ars_correction_postgres import linked as linked
from tests.test_receipt_list_consistency_postgres import batch

USER = {"username": "uuid_test", "role": app.ROLE_ADMIN}


def correct_name(batch_id, receipt_id, name, **changes):
    values = dict(
        document_type="NSS",
        document_number="001234567",
        authorization="00001234",
        specialty="PEDIATRIA",
        patient_name=name,
        user=USER,
    )
    values.update(changes)
    app.update_monthly_batch_receipt_export_data(batch_id, receipt_id, **values)


def test_list_name_correction_updates_receipt_all_pending_lists_and_document(receipts):
    identity = save()
    pending = batch(identity)
    original = app.get_recibo_data(identity)
    correct_name(pending, identity, "NOMBRE CORREGIDO")
    current = app.get_recibo_data(identity)
    assert current["nombre"] == "NOMBRE CORREGIDO"
    for field in (
        "total",
        "sala",
        "items",
        "fecha",
        "numero",
        "ars",
        "username",
        "created_at",
        "estado_facturacion",
    ):
        assert current[field] == original[field]
    assert (
        app.list_monthly_batch_receipts(pending)[0]["patient_snapshot"]
        == "NOMBRE CORREGIDO"
    )
    with app.db_connect() as con:
        document = app.load_current_receipt_snapshot(con, identity)["snapshot"]
        assert document["patient"]["name"] == "NOMBRE CORREGIDO"
        history = con.execute(
            "SELECT observacion FROM recibo_facturacion_history ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]
        assert "NOMBRE CORREGIDO" in history


def test_name_can_be_corrected_without_fabricating_missing_nss_or_authorization(
    receipts,
):
    identity = save(authorization_number="")
    pending = batch(identity)
    correct_name(
        pending, identity, "NOMBRE CORREGIDO", document_number="", authorization=""
    )
    entry = app.list_monthly_batch_receipts(pending)[0]
    assert entry["patient_snapshot"] == "NOMBRE CORREGIDO"
    assert not entry["document_number_snapshot"]
    assert not entry["authorization_snapshot"]
    assert app.MonthlyBillingListsPage._receipt_problems(entry)
    assert app.get_recibo_data(identity)["estado_documento"] == app.DOCUMENT_PRELIMINARY


def test_receipt_name_edit_updates_list_and_latest_correction_wins(receipts):
    identity = save()
    pending = batch(identity)
    correct_name(pending, identity, "NOMBRE UNO")
    save(recibo_id=identity, nombre="NOMBRE DOS")
    assert (
        app.list_monthly_batch_receipts(pending)[0]["patient_snapshot"] == "NOMBRE DOS"
    )
    correct_name(pending, identity, "NOMBRE TRES")
    assert app.get_recibo_data(identity)["nombre"] == "NOMBRE TRES"


def test_sent_list_keeps_name_even_when_receipt_name_changes(receipts):
    identity = save()
    sent = batch(identity, "ENVIADO")
    with app.db_connect() as con:
        con.execute(
            "UPDATE billing_batch_receipts SET patient_snapshot='NOMBRE EMITIDO' WHERE batch_id=%s",
            (sent,),
        )
    save(recibo_id=identity, nombre="NOMBRE CORREGIDO")
    entry = app.list_monthly_batch_receipts(sent)[0]
    assert entry["patient_snapshot"] == entry["nombre"] == "NOMBRE EMITIDO"


def test_full_name_and_date_edit_uses_final_service_date_for_duplicate_guard(receipts):
    identity = save()
    save(numero=2, nombre="NOMBRE CORREGIDO")
    save(recibo_id=identity, nombre="NOMBRE CORREGIDO", fecha="2026-09-06")
    current = app.get_recibo_data(identity)
    assert current["nombre"] == "NOMBRE CORREGIDO"
    assert current["fecha"] == "2026-09-06"
    with app.db_connect() as con:
        key = con.execute(
            "SELECT dedup_key FROM recibos WHERE id=%s", (identity,)
        ).fetchone()[0]
    assert key == app.build_receipt_dedup_key("NOMBRE CORREGIDO", None, "", "", "")


@pytest.mark.parametrize("direction", ["list", "receipt"])
@pytest.mark.parametrize("fail", [False, True])
def test_linked_name_updates_master_attention_receipt_and_events_atomically(
    linked, monkeypatch, direction, fail
):
    identity, attention, patient = linked
    pending = batch(identity)
    if fail:

        def unavailable(*_args, **_kwargs):
            raise RuntimeError("document unavailable")

        monkeypatch.setattr(app, "save_receipt_document_snapshot", unavailable)

    def change():
        if direction == "list":
            correct_name(pending, identity, "NOMBRE CORREGIDO")
        else:
            save(
                recibo_id=identity,
                nombre="NOMBRE CORREGIDO",
                admission_attention=attention,
                admission_session_id="A",
                verification_bypass=None,
            )

    if fail:
        with pytest.raises(RuntimeError, match="document unavailable"):
            change()
    else:
        change()
    expected = "PACIENTE SINTETICO" if fail else "NOMBRE CORREGIDO"
    with app.db_connect() as con:
        master = con.execute(
            "SELECT patient_name FROM admission_patient_directory WHERE global_patient_id=%s",
            (patient,),
        ).fetchone()[0]
        projected = con.execute(
            "SELECT patient_name,latest_payload_json FROM admission_attention_projection WHERE global_patient_id=%s",
            (patient,),
        ).fetchone()
        assert master == projected["patient_name"] == expected
        if not fail:
            assert projected["latest_payload_json"]["name"] == expected
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM admission_patient_directory_events WHERE payload_json->'audit'->>'operation'='RECEIPT_NAME_CORRECTION'"
                ).fetchone()[0]
                == 1
            )
    assert app.get_recibo_data(identity)["nombre"] == expected
    assert app.list_monthly_batch_receipts(pending)[0]["patient_snapshot"] == expected


def test_stale_name_correction_does_not_overwrite_newer_receipt(receipts):
    identity = save()
    pending = batch(identity)
    correct_name(pending, identity, "NOMBRE NUEVO")
    with pytest.raises(ValueError, match="otra pantalla"):
        correct_name(
            pending,
            identity,
            "NOMBRE OBSOLETO",
            expected_patient_name="PACIENTE SINTETICO",
        )
    assert app.get_recibo_data(identity)["nombre"] == "NOMBRE NUEVO"


@pytest.mark.parametrize("specialty", ["PEDIATRIA", "GINECOLOGIA", "GENERAL"])
def test_missing_receipt_specialty_recovers_linked_admission_value(linked, specialty):
    identity, _attention, _patient = linked
    pending = batch(identity)
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibos SET specialty_snapshot=NULL WHERE id=%s", (identity,)
        )
        con.execute(
            "UPDATE admission_attention_projection SET specialty=%s", (specialty,)
        )
    expected = {
        "PEDIATRIA": "PEDIATRÍA",
        "GINECOLOGIA": "GINECOLOGÍA",
        "GENERAL": "EMERGENCIOLOGÍA",
    }[specialty]
    assert app.list_monthly_batch_receipts(pending)[0]["specialty_snapshot"] == expected


def test_corrected_specialty_survives_full_linked_receipt_edit(linked):
    identity, attention, _patient = linked
    pending = batch(identity)
    correct_name(pending, identity, "PACIENTE SINTETICO", specialty="gineco")
    save(
        recibo_id=identity,
        admission_attention=attention,
        admission_session_id="A",
        verification_bypass=None,
    )
    assert app.get_recibo_data(identity)["specialty_snapshot"] == "GINECOLOGÍA"
    assert (
        app.list_monthly_batch_receipts(pending)[0]["specialty_snapshot"]
        == "GINECOLOGÍA"
    )
