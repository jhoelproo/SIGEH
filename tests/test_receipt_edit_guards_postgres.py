"""Write guards on the isolated PostgreSQL fixture, never hospital data."""

from concurrent.futures import ThreadPoolExecutor

import pytest

import CALCULOS_QT as app
from tests.test_inherited_receipt_save import GLOBAL, USER, inherited as inherited
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_billing_consistency_postgres import server as server


def linked_save(attention, **changes):
    return save(
        admission_attention=attention,
        admission_session_id="A",
        verification_bypass=None,
        **changes,
    )


def test_admin_corrects_receipt_header_without_changing_admission(inherited):
    receipt_id = linked_save(inherited)
    with app.db_connect() as con:
        original = dict(
            con.execute(
                "SELECT patient_name,service_date FROM admission_attention_projection LIMIT 1"
            ).fetchone()
        )
    linked_save(
        inherited,
        recibo_id=receipt_id,
        nombre="CORRECCION SINTETICA",
        fecha="2026-09-13",
    )
    with app.db_connect() as con:
        receipt = con.execute(
            "SELECT nombre,fecha FROM recibos WHERE id=%s", (receipt_id,)
        ).fetchone()
        admission = dict(
            con.execute(
                "SELECT patient_name,service_date FROM admission_attention_projection LIMIT 1"
            ).fetchone()
        )
    assert receipt["nombre"] == "CORRECCION SINTETICA"
    assert receipt["fecha"] == "2026-09-13"
    assert admission == original


def test_foreign_claim_blocks_edit_but_does_not_delete_receipt(inherited):
    receipt_id = linked_save(inherited)
    with app.db_connect() as con:
        con.execute("""UPDATE admission_billing_claims SET receipt_id=NULL,processed_at=NULL,
            session_id='FOREIGN',station_id='OTHER',claimed_by='OTHER',expires_at=NOW()+INTERVAL '5 minutes'""")
    with pytest.raises(app.AdmissionAttentionUnavailableError) as caught:
        linked_save(inherited, recibo_id=receipt_id)
    assert caught.value.reason_code == "CLAIMED_OTHER_SESSION"
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert con.execute("SELECT revision_version FROM recibos").fetchone()[0] == 0


def test_concurrent_new_receipts_create_only_one(inherited):
    def attempt(number):
        try:
            linked_save(inherited, numero=number)
            return "saved"
        except app.AdmissionAttentionUnavailableError:
            return "excluded"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, (1, 2))) == ["excluded", "saved"]
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM recibo_items").fetchone()[0] == 1


def test_ui_recheck_distinguishes_query_failure(inherited, monkeypatch):
    def offline(*args, **kwargs):
        raise ConnectionError("TEST OFFLINE")

    monkeypatch.setattr(app.CentralAdmissionReader, "fetch_all", offline)
    with pytest.raises(ConnectionError):
        app.get_projected_billable_attention(
            372, "ORIGIN", current_user=USER, global_attention_id=GLOBAL
        )


@pytest.mark.parametrize("role", [app.ROLE_AUX, app.ROLE_AUDIT, app.ROLE_MEDICAL_AUDIT])
def test_non_admin_room_price_is_enforced_during_save(inherited, monkeypatch, role):
    with app.db_connect() as con:
        con.execute("""INSERT INTO ars(nombre,sala_emergencia,is_active,billing_enabled)
            VALUES('FUTURO',460,1,TRUE) ON CONFLICT(nombre) DO UPDATE
            SET sala_emergencia=460,is_active=1,billing_enabled=TRUE""")
    monkeypatch.setattr(
        app, "get_user", lambda username: {"username": username, "role": role}
    )
    with pytest.raises(PermissionError, match="ADMIN"):
        linked_save(inherited, sala=461, total=561)
    receipt_id = linked_save(inherited, sala=460, total=560)
    with pytest.raises(PermissionError, match="ADMIN"):
        linked_save(inherited, recibo_id=receipt_id, sala=0, total=100)
    assert (
        linked_save(inherited, recibo_id=receipt_id, sala=460, total=560) == receipt_id
    )


def test_admission_date_cannot_be_overridden_in_billing(inherited):
    from billing_admission_edit import AdmissionDataChanged

    with pytest.raises(AdmissionDataChanged, match="fecha"):
        linked_save(inherited, fecha="2026-09-06")
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 0


def test_auxiliary_cannot_edit_unverified_header(receipts, monkeypatch):
    receipt_id = save()
    monkeypatch.setattr(
        app, "get_user", lambda username: {"username": username, "role": app.ROLE_AUX}
    )
    with pytest.raises(PermissionError, match="validar"):
        save(recibo_id=receipt_id, verification_bypass=None, dx="CAMBIADO")
    with app.db_connect() as con:
        assert con.execute("SELECT dx FROM recibos").fetchone()[0] == "PRUEBA"
