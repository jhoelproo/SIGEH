"""Shared changes are committed atomically on a disposable loopback server."""

import pytest
import CALCULOS_QT as app
from patient_directory import CentralPatientDirectoryRepository

from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save
from tests.test_inherited_receipt_save import inherited as inherited
from tests.test_receipt_ars_correction_postgres import (
    linked as linked,
    tariff as tariff,
    change,
)
from tests.test_receipt_list_consistency_postgres import batch

REAL_GET_USER = app.get_user


def edit(patient, changes):
    return CentralPatientDirectoryRepository(app.db_connect).update_patient(
        patient,
        changes,
        actor_user="uuid_test",
        actor_role=app.ROLE_ADMIN,
        propagate_recent=True,
    )


@pytest.mark.parametrize("status", ["PENDIENTE", "FACTURADO"])
def test_nss_name_sync_existing_receipt_and_document_from_other_connection(
    linked, status
):
    identity, attention, patient = linked
    with app.db_connect() as con:
        con.execute(
            "UPDATE recibos SET estado_facturacion=%s WHERE id=%s", (status, identity)
        )
        before = dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (identity,)).fetchone()
        )
        items = [
            dict(row)
            for row in con.execute(
                "SELECT * FROM recibo_items WHERE recibo_id=%s", (identity,)
            ).fetchall()
        ]
    result = edit(patient, {"nss": "000009999", "nombre": "SYNTHETIC CORRECTED"})
    assert result["linked_receipts"][0]["id"] == identity
    # A new connection represents another station; it sees the committed revision.
    with app.db_connect() as con:
        after = dict(
            con.execute("SELECT * FROM recibos WHERE id=%s", (identity,)).fetchone()
        )
        assert after["admission_nss_snapshot"] == "000009999"
        assert after["nombre"] == "SYNTHETIC CORRECTED"
        for field in [
            "id",
            "numero",
            "ars",
            "sala",
            "total",
            "estado_facturacion",
            "admission_global_attention_id",
            "username",
            "created_at",
        ]:
            assert after[field] == before[field]
        assert [
            dict(row)
            for row in con.execute(
                "SELECT * FROM recibo_items WHERE recibo_id=%s", (identity,)
            ).fetchall()
        ] == items
        projection = con.execute(
            "SELECT * FROM admission_attention_projection"
        ).fetchone()
        assert projection["readiness"] == "LISTA"
        assert projection["nss_snapshot"] == "000009999"
        snapshot = app.load_current_receipt_snapshot(con, identity)["snapshot"]
        assert snapshot["patient"]["nss"] == "000009999"
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1
        assert (
            con.execute(
                "SELECT COUNT(*) FROM admission_attention_projection"
            ).fetchone()[0]
            == 1
        )


def test_patient_ars_change_then_controlled_receipt_migration(linked):
    identity, attention, patient = linked
    pending = batch(identity)
    result = edit(patient, {"ars": "HUMANO"})
    assert result["linked_receipts"][0]["ars"] == "FUTURO"
    # The old receipt keeps its amounts until an explicit tariff correction.
    assert app.get_recibo_data(identity)["ars"] == "FUTURO"
    assert (
        change(
            identity,
            admission_attention=attention,
            admission_session_id="A",
            verification_bypass=None,
        )
        == identity
    )
    with app.db_connect() as con:
        row = con.execute("SELECT * FROM recibos WHERE id=%s", (identity,)).fetchone()
        assert row["ars"] == row["admission_ars_snapshot"] == "HUMANO"
        assert float(row["total"]) == 710
        assert (
            con.execute(
                "SELECT included FROM billing_batch_receipts WHERE batch_id=%s",
                (pending,),
            ).fetchone()[0]
            == 0
        )
        assert con.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 1


def test_receipt_sync_failure_rolls_back_patient_and_attention(linked, monkeypatch):
    identity, attention, patient = linked
    monkeypatch.setattr(
        "receipt_documents.refresh_administrative_snapshot",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("SYNTHETIC SNAPSHOT ERROR")),
    )
    with pytest.raises(RuntimeError, match="SYNTHETIC"):
        edit(patient, {"nss": "000001111"})
    with app.db_connect() as con:
        assert (
            con.execute("SELECT nss FROM admission_patient_directory").fetchone()[0]
            == "1234567"
        )
        assert (
            con.execute("SELECT admission_nss_snapshot FROM recibos").fetchone()[0]
            != "000001111"
        )
        assert (
            con.execute(
                "SELECT COUNT(*) FROM admission_patient_directory_events"
            ).fetchone()[0]
            == 0
        )


def test_individual_permissions_persist_and_revocation_is_live(receipts, monkeypatch):
    # Use real users for authorization; never trust a role supplied by the caller.
    app.create_user(
        "ADMIN", "permission_admin", "safe-test-password", "Q", "A", app.ROLE_ADMIN
    )
    app.create_user(
        "OPERATOR", "permission_operator", "safe-test-password", "Q", "A", app.ROLE_AUX
    )
    monkeypatch.setattr(app, "get_user", REAL_GET_USER)
    admin = app.get_user("permission_admin")
    operator = app.get_user("permission_operator")
    permission = app.PERMISSION_VALIDATE_RECEIPT
    assert not app.user_has_permission(operator, permission)
    app.save_user_permission_overrides(
        "permission_operator", {permission: True}, admin, expected={}
    )
    assert app.get_user("permission_operator")["role"] == app.ROLE_AUX
    assert app._require_receipt_permission(operator, permission, "denied")[0][
        "permission_overrides"
    ][permission]
    app.save_user_permission_overrides(
        "permission_operator", {permission: False}, admin, expected={permission: True}
    )
    with pytest.raises(PermissionError):
        app._require_receipt_permission(
            {"username": "permission_operator", "role": app.ROLE_ADMIN},
            permission,
            "denied",
        )
    with pytest.raises(PermissionError):
        app.save_user_permission_overrides(
            "permission_admin",
            {},
            {"username": "permission_operator", "role": app.ROLE_ADMIN},
        )
    app.save_user_permission_overrides(
        "permission_admin", {app.PERMISSION_EDIT_PENDING: False}, admin, expected={}
    )
    with pytest.raises(PermissionError):
        save(recibo_id=123, username="permission_admin")
    with app.db_connect() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM action_history WHERE action='Cambiar permisos individuales'"
            ).fetchone()[0]
            == 3
        )


def test_noop_administrative_sync_does_not_reissue_receipt(linked):
    from admission_receipt_sync import synchronize_patient_receipts

    identity, attention, patient = linked
    with app.db_connect() as con:
        receipt = con.execute(
            "SELECT * FROM recibos WHERE id=%s", (identity,)
        ).fetchone()
        result = synchronize_patient_receipts(
            con,
            {
                "global_patient_id": patient,
                "nss": receipt["admission_nss_snapshot"],
                "cedula": receipt["admission_cedula_snapshot"],
            },
            [],
            "uuid_test",
            "2026-10-15",
        )
        assert result[0]["id"] == identity
        assert (
            con.execute(
                "SELECT revision_version FROM recibos WHERE id=%s", (identity,)
            ).fetchone()[0]
            == receipt["revision_version"]
        )


def test_non_admin_with_explicit_permission_can_correct_receipt_ars(
    tariff, monkeypatch
):
    identity = save()
    monkeypatch.setattr(
        app,
        "get_user",
        lambda username: {
            "username": username,
            "role": app.ROLE_AUDIT,
            "permission_overrides": {app.PERMISSION_CHANGE_RECEIPT_ARS: True},
        },
    )
    assert change(identity, verification_bypass=None) == identity
    data = app.get_recibo_data(identity)
    assert data["ars"] == "HUMANO" and data["total"] == 710
