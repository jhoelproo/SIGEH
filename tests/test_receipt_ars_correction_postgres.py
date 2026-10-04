"""Insurer correction transactions on disposable PostgreSQL only."""

from uuid import uuid4

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_inherited_receipt_save import inherited as inherited


@pytest.fixture
def tariff(receipts):
    with app.db_connect() as con:
        identity = con.execute(
            "INSERT INTO ars(nombre,sala_emergencia,is_active) VALUES('HUMANO',460,1) RETURNING id"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO ars_items(ars_id,categoria,nombre,precio,is_active) VALUES(%s,'Procedimientos','PRUEBA',125,1)",
            (identity,),
        )


def change(identity, **values):
    return save(
        recibo_id=identity,
        ars="HUMANO",
        sala=460,
        total=710,
        grouped=[("Procedimientos", [("PRUEBA", 125, 2, 250, "Procedimientos")])],
        **values,
    )


def test_admin_unlinked_correction_and_immutable_origin(tariff):
    identity = save()
    change(identity)
    data = app.get_recibo_data(identity)
    assert data["ars"] == "HUMANO" and data["total"] == 710
    assert data["sala"] == 460 and data["items"][0]["cantidad"] == 2
    assert data["items"][0]["ars"] == "HUMANO"
    assert (
        data["username"] == "uuid_test" and data["created_at"] == "2026-09-05 10:00:00"
    )
    assert data["fecha"] == "2026-09-05" and data["numero"] == 1


@pytest.mark.parametrize("alteration", ["missing", "inactive", "room", "item"])
def test_bad_catalog_or_stale_price_rolls_back(tariff, alteration):
    identity = save()
    with app.db_connect() as con:
        statements = {
            "missing": "DELETE FROM ars_items WHERE ars_id=(SELECT id FROM ars WHERE nombre='HUMANO')",
            "inactive": "UPDATE ars SET is_active=0 WHERE nombre='HUMANO'",
            "room": "UPDATE ars SET sala_emergencia=500 WHERE nombre='HUMANO'",
            "item": "UPDATE ars_items SET precio=200 WHERE ars_id=(SELECT id FROM ars WHERE nombre='HUMANO')",
        }
        con.execute(statements[alteration])
    with pytest.raises(ValueError):
        change(identity)
    assert app.get_recibo_data(identity)["ars"] == "FUTURO"


def test_non_admin_cannot_correct_ars(tariff, monkeypatch):
    identity = save()
    monkeypatch.setattr(
        app, "get_user", lambda username: {"username": username, "role": app.ROLE_AUDIT}
    )
    with pytest.raises(PermissionError):
        change(identity, verification_bypass=None)
    assert app.get_recibo_data(identity)["ars"] == "FUTURO"


def test_unlinked_consultation_keeps_service_type_and_uses_consultation_price(tariff):
    identity = save()
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibos SET service_type='CONSULTA' WHERE id=%s", (identity,)
        )
        con.execute("UPDATE ars SET consulta_price=200 WHERE nombre='HUMANO'")
    save(
        recibo_id=identity,
        ars="HUMANO",
        sala=200,
        total=450,
        grouped=[("Procedimientos", [("PRUEBA", 125, 2, 250, "Procedimientos")])],
    )
    data = app.get_recibo_data(identity)
    assert data["service_type"] == "CONSULTA" and data["sala"] == 200


@pytest.fixture
def linked(inherited):
    patient = str(uuid4())
    with app.db_connect() as con:
        con.execute(
            "INSERT INTO admission_patient_directory(global_patient_id,patient_name,canonical_ars,nss) VALUES(%s,'PACIENTE SINTETICO','FUTURO','1234567')",
            (patient,),
        )
        con.execute(
            "UPDATE admission_attention_projection SET global_patient_id=%s,latest_payload_json=%s::jsonb,operational_session_id=%s",
            (
                patient,
                '{"name":"PACIENTE SINTETICO","ars":"FUTURO","nss":"1234567","service_date":"2026-09-05","service_type":"EMERGENCIA"}',
                "33333333-3333-4333-8333-333333333333",
            ),
        )
        identity = con.execute(
            "INSERT INTO ars(nombre,sala_emergencia,is_active) VALUES('HUMANO',460,1) RETURNING id"
        ).fetchone()[0]
        con.execute(
            "INSERT INTO ars_items(ars_id,categoria,nombre,precio,is_active) VALUES(%s,'Procedimientos','PRUEBA',125,1)",
            (identity,),
        )
    identity = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    return identity, inherited, patient


@pytest.mark.parametrize("fail", [False, True])
def test_linked_master_attention_receipt_and_events_commit_together(
    linked, monkeypatch, fail
):
    identity, attention, patient = linked
    if fail:

        def reject(*args, **kwargs):
            raise RuntimeError("snapshot unavailable")

        monkeypatch.setattr(app, "save_receipt_document_snapshot", reject)
        with pytest.raises(RuntimeError, match="snapshot unavailable"):
            change(
                identity,
                admission_attention=attention,
                admission_session_id="A",
                verification_bypass=None,
            )
    else:
        change(
            identity,
            admission_attention=attention,
            admission_session_id="A",
            verification_bypass=None,
        )
    with app.db_connect() as con:
        receipt = con.execute(
            "SELECT ars,admission_ars_snapshot,revision_version FROM recibos WHERE id=%s",
            (identity,),
        ).fetchone()
        master = con.execute(
            "SELECT canonical_ars FROM admission_patient_directory WHERE global_patient_id=%s",
            (patient,),
        ).fetchone()[0]
        projection = con.execute(
            "SELECT canonical_ars,latest_payload_json,turn_id FROM admission_attention_projection"
        ).fetchone()
        expected = "FUTURO" if fail else "HUMANO"
        assert (
            receipt["ars"]
            == receipt["admission_ars_snapshot"]
            == master
            == projection["canonical_ars"]
            == expected
        )
        assert projection["latest_payload_json"]["ars"] == expected
        assert projection["turn_id"] == 566
        assert con.execute(
            "SELECT COUNT(*) FROM admission_patient_directory_events"
        ).fetchone()[0] == (0 if fail else 1)
        assert con.execute("SELECT COUNT(*) FROM admission_sync_events").fetchone()[
            0
        ] == (0 if fail else 1)


def test_linked_correction_without_verified_attention_is_rejected(linked):
    identity, _, _ = linked
    with pytest.raises(ValueError, match="atención vinculada"):
        change(identity, verification_bypass=None)
    assert app.get_recibo_data(identity)["ars"] == "FUTURO"


@pytest.mark.parametrize(
    "alteration", ["missing_patient", "deleted", "conflict", "missing_identity"]
)
def test_invalid_or_concurrently_changed_patient_blocks_whole_correction(
    linked, alteration
):
    identity, attention, patient = linked
    with app.db_connect() as con:
        statements = {
            "missing_patient": "DELETE FROM admission_patient_directory",
            "deleted": "UPDATE admission_patient_directory SET is_deleted=TRUE",
            "conflict": "UPDATE admission_patient_directory SET canonical_ars='APS'",
            "missing_identity": "UPDATE admission_attention_projection SET global_patient_id=NULL",
        }
        con.execute(statements[alteration])
    with pytest.raises(ValueError):
        change(
            identity,
            admission_attention=attention,
            admission_session_id="A",
            verification_bypass=None,
        )
    assert app.get_recibo_data(identity)["ars"] == "FUTURO"
    with app.db_connect() as con:
        assert (
            con.execute("SELECT COUNT(*) FROM admission_sync_events").fetchone()[0] == 0
        )
