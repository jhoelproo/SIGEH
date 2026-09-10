"""Inherited receipt editing and final guards on isolated PostgreSQL."""

import pytest
import CALCULOS_QT as app
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_billing_consistency_postgres import server as server

SOURCE = "11111111-1111-4111-8111-111111111111"
GLOBAL = "22222222-2222-4222-8222-222222222222"
USER = {"username": "uuid_test", "role": app.ROLE_ADMIN}


@pytest.fixture
def inherited(receipts):
    with app.db_connect() as con:
        con.execute(
            """INSERT INTO sigeh_product_state(singleton,product_id,bootstrap_version,production_epoch_id,bootstrap_status,bootstrap_completed_at,production_initialized_at)
            VALUES(1,'SIGEH','TEST',%s,'PRODUCTION_ACTIVE',NOW(),NOW())""",
            (GLOBAL,),
        )
        con.execute(
            """INSERT INTO admission_operational_sessions(operational_session_id,active_user_id,active_username,
            active_user_display_name,primary_device_id,primary_login_session_id,turn_id,turn_started_at,
            operational_source_id,status,generation,production_epoch_id)
            VALUES('33333333-3333-4333-8333-333333333333','1','uuid_test','TEST','TEST','A',3950,NOW(),%s,'ACTIVE',1,%s)""",
            (SOURCE, GLOBAL),
        )
        con.execute(
            """INSERT INTO admission_attention_projection(source_instance_id,attention_id,patient_id,turn_id,
            service_date,patient_name,coverage_status,canonical_ars,source_status,service_type,readiness,snapshot_hash,
            contract_version,synced_at,operational_source_id,global_attention_id,
            created_at_effective_utc)
            VALUES('ORIGIN',372,1,566,'2026-09-05','PACIENTE SINTETICO','ASEGURADO_VALIDADO','FUTURO',
            'ACTIVA','EMERGENCIA','LISTA','test',1,'2026-09-05',%s,%s,
            '2026-09-08 10:00:00-04')""",
            (SOURCE, GLOBAL),
        )
        con.execute("""INSERT INTO admission_shift_inheritances(source_instance_id,attention_id,turno_origen_id,estado)
            VALUES('ORIGIN',372,566,'PENDIENTE')""")
    attention = app.claim_projected_billable_attention(
        372,
        "ORIGIN",
        username="uuid_test",
        session_id="A",
        current_user=USER,
        global_attention_id=GLOBAL,
    )
    assert attention is not None
    return attention


def test_edit_inherited_receipt_keeps_central_eligibility(inherited):
    receipt_id = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    # The receipt legitimately consumes the pending inheritance.
    with app.db_connect() as con:
        assert (
            con.execute("SELECT estado FROM admission_shift_inheritances").fetchone()[0]
            == "COMPLETADA"
        )
    live = app.get_projected_billable_attention(
        372,
        "ORIGIN",
        current_user=USER,
        global_attention_id=GLOBAL,
        receipt_id=receipt_id,
        session_id="A",
    )
    assert live is not None, (
        "Current UI loses the inherited record after its first save"
    )
    assert (
        save(
            recibo_id=receipt_id,
            admission_attention=inherited,
            admission_session_id="A",
            verification_bypass=None,
        )
        == receipt_id
    )


def test_current_turn_receipt_can_add_authorization(inherited):
    with app.db_connect() as con:
        con.execute("UPDATE admission_attention_projection SET turn_id=3950")
    current = app.get_projected_billable_attention(
        372, "ORIGIN", current_user=USER, global_attention_id=GLOBAL, session_id="A"
    )
    receipt_id = save(
        admission_attention=current,
        admission_session_id="A",
        verification_bypass=None,
        authorization_number="",
    )
    live = app.get_projected_billable_attention(
        372,
        "OLD_ALIAS",
        current_user=USER,
        global_attention_id=GLOBAL,
        receipt_id=receipt_id,
        session_id="A",
    )
    assert live is not None
    assert (
        save(
            recibo_id=receipt_id,
            admission_attention=live,
            admission_session_id="A",
            verification_bypass=None,
        )
        == receipt_id
    )
    with app.db_connect() as con:
        row = con.execute(
            "SELECT numero_autorizacion,estado_documento FROM recibos WHERE id=%s",
            (receipt_id,),
        ).fetchone()
        assert row["numero_autorizacion"] == "123456789"
        assert row["estado_documento"] == app.DOCUMENT_READY
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE admission_attention_projection SET source_status='ANULADA'",
        "UPDATE admission_attention_projection SET service_type='URGENCIA'",
        "UPDATE admission_attention_projection SET is_deleted=TRUE",
        "UPDATE admission_attention_projection SET readiness='INCOMPLETA'",
        "UPDATE admission_attention_projection SET canonical_ars='SENASA SUBSIDIADO'",
    ],
)
def test_own_receipt_never_skips_live_exclusions(inherited, change):
    receipt_id = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    with app.db_connect() as con:
        con.execute(change)
    with pytest.raises(app.AdmissionAttentionUnavailableError):
        save(
            recibo_id=receipt_id,
            admission_attention=inherited,
            admission_session_id="A",
            verification_bypass=None,
        )
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT revision_version FROM recibos WHERE id=%s", (receipt_id,)
            ).fetchone()[0]
            == 0
        )


def test_changed_admission_date_rejects_stale_receipt(inherited):
    from billing_admission_edit import AdmissionDataChanged

    receipt_id = save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    with app.db_connect() as con:
        con.execute(
            "UPDATE admission_attention_projection SET service_date='2026-09-06'"
        )
    with pytest.raises(AdmissionDataChanged, match="fecha"):
        save(
            recibo_id=receipt_id,
            admission_attention=inherited,
            admission_session_id="A",
            verification_bypass=None,
        )


def test_cannot_create_second_receipt_for_consumed_inheritance(inherited):
    save(
        admission_attention=inherited,
        admission_session_id="A",
        verification_bypass=None,
    )
    with pytest.raises(app.AdmissionAttentionUnavailableError):
        save(
            numero=2,
            admission_attention=inherited,
            admission_session_id="A",
            verification_bypass=None,
        )
