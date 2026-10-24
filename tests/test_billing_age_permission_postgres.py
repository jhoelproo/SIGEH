"""Persisted 48h exceptions are re-read before isolated receipt writes."""

from datetime import datetime
import json

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


PERMISSION = app.PERMISSION_BILL_OLDER_ATTENTION
REAL_GET_USER = app.get_user
OLD_DATE = "2026-10-01"
USERNAME = "uuid_test"
WRITE_TABLES = (
    "recibos",
    "recibo_items",
    "recibo_facturacion_history",
    "action_history",
    "admission_attention_projection",
    "admission_sync_events",
    "admission_billing_claims",
)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 22, 12, tzinfo=tz)


@pytest.fixture
def permission_database(receipts, monkeypatch):
    monkeypatch.setattr(app, "datetime", FixedDateTime)
    monkeypatch.setattr(app, "get_user", REAL_GET_USER)
    with app.db_connect() as connection:
        connection.execute(
            """INSERT INTO users(
                full_name,username,role,password_hash,password_salt,
                security_question,security_answer_hash,security_answer_salt,created_at)
                VALUES('SYNTHETIC QA',%s,%s,'test-hash','test-salt',
                'test-question','test-answer-hash','test-answer-salt','2026-10-22')""",
            (USERNAME, app.ROLE_ADMIN),
        )
        connection.execute(
            """INSERT INTO ars(nombre,sala_emergencia,is_active,billing_enabled)
                VALUES('FUTURO',0,1,TRUE)
                ON CONFLICT(nombre) DO UPDATE SET sala_emergencia=0,
                is_active=1,billing_enabled=TRUE"""
        )


def set_permissions(role=app.ROLE_ADMIN, *, active=True, **permissions):
    with app.db_connect() as connection:
        connection.execute(
            """UPDATE users SET role=%s,is_active=%s,
                permission_overrides=%s::jsonb WHERE username=%s""",
            (role, int(active), json.dumps(permissions), USERNAME),
        )


def counts():
    with app.db_connect() as connection:
        return {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in WRITE_TABLES
        }


def receipt_state():
    with app.db_connect() as connection:
        return {
            table: [dict(row) for row in connection.execute(f"SELECT * FROM {table}")]
            for table in WRITE_TABLES
        }


def save_old(**changes):
    return save(fecha=OLD_DATE, created_at="2026-10-22 12:00:00", **changes)


def test_grant_is_fresh_and_bypass_keeps_null_admission_ids(permission_database):
    set_permissions(
        app.ROLE_AUX,
        **{PERMISSION: False, app.PERMISSION_BYPASS_VERIFICATION: True},
    )
    stale_user = app.get_user(USERNAME)
    assert not app.can_bill_older_attention(stale_user)
    set_permissions(
        app.ROLE_AUX,
        **{PERMISSION: True, app.PERMISSION_BYPASS_VERIFICATION: True},
    )

    receipt_id = save_old()

    with app.db_connect() as connection:
        receipt = connection.execute(
            "SELECT * FROM recibos WHERE id=%s", (receipt_id,)
        ).fetchone()
        assert receipt["is_backdated"] == 1
        assert receipt["estado_documento"] == app.DOCUMENT_READY
        assert receipt["review_status"] == app.AUTH_REVIEW_CLEAR
        assert receipt["verification_bypassed"]
        for key in (
            "admission_global_attention_id",
            "admission_atencion_id",
            "admission_source_instance_id",
        ):
            assert receipt[key] is None
    persisted = counts()
    assert persisted["recibos"] == persisted["recibo_items"] == 1
    assert persisted["admission_attention_projection"] == 0
    assert persisted["admission_sync_events"] == 0
    assert persisted["admission_billing_claims"] == 0


def test_revoked_admin_permission_rejects_forged_backdated_flag_without_writes(
    permission_database,
):
    stale_user = app.get_user(USERNAME)
    assert app.can_bill_older_attention(stale_user)
    set_permissions(**{PERMISSION: False})
    before = counts()

    with pytest.raises(PermissionError, match="48 horas"):
        save_old(is_backdated=True)

    assert counts() == before


def test_revocation_while_editing_preserves_receipt_items_and_audit(
    permission_database,
):
    receipt_id = save_old()
    set_permissions(**{PERMISSION: False})
    before = receipt_state()

    with pytest.raises(PermissionError, match="48 horas"):
        save_old(recibo_id=receipt_id, total=200)

    assert receipt_state() == before


def test_age_exception_does_not_grant_bypass_permission(permission_database):
    set_permissions(app.ROLE_AUX, **{PERMISSION: True})
    current = app.get_user(USERNAME)
    assert app.can_bill_older_attention(current)
    assert not app.can_bypass_patient_verification(current)
    before = counts()

    with pytest.raises(PermissionError, match="sin verificar"):
        save_old()

    assert counts() == before


def test_age_exception_does_not_override_denied_receipt_edit(permission_database):
    receipt_id = save_old()
    set_permissions(
        **{PERMISSION: True, app.PERMISSION_EDIT_PENDING: False},
    )
    before = receipt_state()

    with pytest.raises(PermissionError, match="editar recibos"):
        save_old(recibo_id=receipt_id, total=200)

    assert receipt_state() == before


def test_inactive_user_cannot_use_persisted_age_grant(permission_database):
    set_permissions(active=False, **{PERMISSION: True})
    before = counts()

    with pytest.raises(PermissionError, match="48 horas"):
        save_old()

    assert counts() == before


def test_denied_old_date_exception_keeps_existing_two_day_window(
    permission_database,
):
    set_permissions(**{PERMISSION: False})

    receipt_id = save(fecha="2026-10-20", created_at="2026-10-22 12:00:00")

    with app.db_connect() as connection:
        receipt = connection.execute(
            "SELECT fecha,is_backdated FROM recibos WHERE id=%s", (receipt_id,)
        ).fetchone()
        assert receipt["fecha"] == "2026-10-20"
        assert receipt["is_backdated"] == 0
