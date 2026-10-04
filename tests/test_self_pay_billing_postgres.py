"""Real transactions on a disposable PostgreSQL, without hospital writes."""

import pytest
import psycopg2

import CALCULOS_QT as app
from receipt_documents import load_current_receipt_snapshot
from self_pay_billing import list_receipts, load_summary, save_payment
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


@pytest.fixture
def tariffs(receipts):
    with app.db_connect() as connection:
        ars_id = connection.execute(
            """INSERT INTO ars(nombre,sala_emergencia,consulta_price,is_active)
            VALUES('SENASA CONTRIBUTIVO',460,300,1)
            ON CONFLICT(nombre) DO UPDATE SET sala_emergencia=460,consulta_price=300,is_active=1
            RETURNING id"""
        ).fetchone()[0]
        connection.execute(
            """INSERT INTO ars_items(ars_id,categoria,nombre,precio,is_active)
            VALUES(%s,'Procedimientos','PRUEBA',100,1)
            ON CONFLICT(ars_id,categoria,nombre) DO UPDATE SET precio=100,is_active=1""",
            (ars_id,),
        )
    return ars_id


def direct_save(**changes):
    values = dict(
        coverage="EXTRANJERO",
        payment_status="PAGADO",
        sala=460,
        total=560,
        ars="SENASA CONTRIBUTIVO",
        authorization_number="",
        verification_bypass=None,
    )
    values.update(changes)
    return save(**values)


@pytest.mark.parametrize(
    "coverage,status,reason",
    [
        ("EXTRANJERO", "PAGADO", ""),
        ("NO_ASEGURADO", "PAGADO", ""),
        ("NO_ASEGURADO", "EXONERADO", "Exoneración autorizada"),
    ],
)
def test_save_reopen_document_and_separate_history(tariffs, coverage, status, reason):
    identity = direct_save(
        coverage=coverage, payment_status=status, exemption_reason=reason
    )
    data = app.get_recibo_data(identity)
    assert data["tipo_cobertura"] == coverage and data["ars"] == ""
    assert data["payment_status"] == status
    assert not data["verification_bypassed"] and data["numero_autorizacion"] == ""
    assert data["receipt_origin"] == "SELF_PAY"
    assert data["items"][0]["ars"] == "SENASA CONTRIBUTIVO"
    with app.db_connect() as connection:
        snapshot = load_current_receipt_snapshot(connection, identity)["snapshot"]
        rows = list_receipts(connection, start="2026-09-05", end="2026-09-05")
        summary = load_summary(connection, start="2026-09-05", end="2026-09-05")
    assert snapshot["document"]["payment_status"] == status
    assert snapshot["document"]["exemption_reason"] == reason
    assert snapshot["header"]["document_state"] == app.DOCUMENT_READY
    assert rows[0]["id"] == identity and rows[0]["matched_count"] == 1
    item = next(row for row in summary if row["coverage"] == coverage)
    assert item["collected"] == ("560.00" if status == "PAGADO" else "0.00")
    assert item["exempt_amount"] == ("560.00" if status == "EXONERADO" else "0.00")
    assert app.list_receipts_history_rows() == []
    assert app.get_billing_status_summary()[app.BILLING_NOT_INVOICED]["receipts"] == 0
    with pytest.raises(ValueError, match="historial propio"):
        app.change_receipt_billing_status(
            identity,
            app.BILLING_INVOICED,
            {"username": "uuid_test", "role": app.ROLE_ADMIN},
            reason="No corresponde",
        )


def test_paid_to_exempt_edit_keeps_creator_date_prices_and_document_history(tariffs):
    identity = direct_save(coverage="NO_ASEGURADO")
    before = app.get_recibo_data(identity)
    with app.db_connect() as connection:
        connection.execute(
            "UPDATE ars SET sala_emergencia=500 WHERE nombre='SENASA CONTRIBUTIVO'"
        )
        connection.execute("UPDATE ars_items SET precio=120 WHERE nombre='PRUEBA'")
    direct_save(
        recibo_id=identity,
        coverage="NO_ASEGURADO",
        payment_status="EXONERADO",
        exemption_reason="Exoneración posterior",
        username="editor",
        created_at="2026-10-03 10:00:00",
    )
    after = app.get_recibo_data(identity)
    assert after["payment_status"] == "EXONERADO"
    for key in (
        "numero",
        "fecha",
        "created_at",
        "username",
        "total",
        "sala",
        "ars",
        "tipo_cobertura",
    ):
        assert after[key] == before[key]
    assert after["revision_version"] == before["revision_version"] + 1
    with app.db_connect() as connection:
        records = connection.execute(
            "SELECT snapshot_jsonb FROM recibo_document_versions WHERE recibo_id=%s ORDER BY version",
            (identity,),
        ).fetchall()
    assert [row[0]["document"]["payment_status"] for row in records] == [
        "PAGADO",
        "EXONERADO",
    ]


@pytest.mark.parametrize(
    "changes",
    [
        {"sala": 459, "total": 559},
        {"total": 999},
        {
            "grouped": [
                ("Procedimientos", [("PRUEBA", 101, 1, 101, "Procedimientos")])
            ],
            "total": 561,
        },
        {"payment_status": "EXONERADO", "exemption_reason": "Motivo correcto"},
        {
            "coverage": "NO_ASEGURADO",
            "payment_status": "EXONERADO",
            "exemption_reason": "corto",
        },
    ],
)
def test_failed_validation_does_not_write_partial_receipt(tariffs, changes):
    with pytest.raises(ValueError):
        direct_save(**changes)
    with app.db_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 0
        assert (
            connection.execute("SELECT COUNT(*) FROM receipt_self_pay").fetchone()[0]
            == 0
        )


def test_database_constraints_and_rollback(tariffs):
    identity = direct_save()
    with pytest.raises(psycopg2.errors.CheckViolation):
        with app.db_connect() as connection:
            connection.execute(
                "UPDATE receipt_self_pay SET payment_status='EXONERADO',exemption_reason='Motivo válido' WHERE receipt_id=%s",
                (identity,),
            )
    with pytest.raises(psycopg2.errors.ForeignKeyViolation):
        with app.db_connect() as connection:
            save_payment(connection, -1, "NO_ASEGURADO", "PAGADO", "", "actor")
    assert app.get_recibo_data(identity)["payment_status"] == "PAGADO"


def test_existing_contributive_flow_remains_unchanged(tariffs):
    identity = save(ars="SENASA CONTRIBUTIVO")
    data = app.get_recibo_data(identity)
    assert (
        data["ars"] == "SENASA CONTRIBUTIVO" and data["tipo_cobertura"] == "ASEGURADO"
    )
    assert data["estado_facturacion"] == app.BILLING_PENDING
    assert data["numero_autorizacion"] == "123456789"
    assert app.list_receipts_history_rows()[0]["id"] == identity
    with app.db_connect() as connection:
        assert list_receipts(connection, start="2026-09-05", end="2026-09-05") == []


def test_unknown_operator_cannot_save_direct_payment(tariffs, monkeypatch):
    monkeypatch.setattr(app, "get_user", lambda _: None)
    with pytest.raises(PermissionError, match="no está autorizado"):
        direct_save()
    with app.db_connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM recibos").fetchone()[0] == 0


def test_auxiliary_can_save_paid_receipt_without_verification(tariffs, monkeypatch):
    monkeypatch.setattr(
        app, "get_user", lambda _: dict(username="uuid_test", role=app.ROLE_AUX)
    )
    identity = direct_save()
    assert app.get_recibo_data(identity)["payment_status"] == "PAGADO"


@pytest.mark.parametrize("coverage", ["EXTRANJERO", "NO_ASEGURADO"])
def test_pending_save_reopen_and_pay_without_double_revenue(tariffs, coverage):
    identity = direct_save(coverage=coverage, payment_status="PENDIENTE_PAGO")
    assert app.get_recibo_data(identity)["payment_status"] == "PENDIENTE_PAGO"
    with app.db_connect() as connection:
        assert (
            connection.execute(
                "SELECT paid_at FROM receipt_self_pay WHERE receipt_id=%s", (identity,)
            ).fetchone()[0]
            is None
        )
        summary = next(
            row
            for row in load_summary(connection, start="2026-09-05", end="2026-09-05")
            if row["coverage"] == coverage
        )
        assert (
            summary["pending_count"] == 1
            and summary["pending_amount"] == "560.00"
            and summary["collected"] == "0.00"
        )
    direct_save(recibo_id=identity, coverage=coverage, payment_status="PAGADO")
    with app.db_connect() as connection:
        paid_at = connection.execute(
            "SELECT paid_at FROM receipt_self_pay WHERE receipt_id=%s", (identity,)
        ).fetchone()[0]
        assert paid_at is not None
        summary = next(
            row
            for row in load_summary(connection, start="2026-09-05", end="2026-09-05")
            if row["coverage"] == coverage
        )
        assert summary["pending_count"] == 0 and summary["collected"] == "560.00"
    direct_save(recibo_id=identity, coverage=coverage, payment_status="PAGADO")
    with app.db_connect() as connection:
        assert (
            connection.execute(
                "SELECT paid_at FROM receipt_self_pay WHERE receipt_id=%s", (identity,)
            ).fetchone()[0]
            == paid_at
        )
    assert app.get_recibo_data(identity)["fecha"] == "2026-09-05"


def test_pending_old_schema_upgrade_is_idempotent_and_preserves_paid_receipt(tariffs):
    from self_pay_billing import install_schema

    identity = direct_save()
    with app.db_connect() as connection:
        connection.execute("ALTER TABLE receipt_self_pay DROP COLUMN paid_at")
        connection.execute(
            "ALTER TABLE receipt_self_pay DROP CONSTRAINT receipt_self_pay_payment_status_check"
        )
        connection.execute(
            "ALTER TABLE receipt_self_pay ADD CONSTRAINT receipt_self_pay_payment_status_check CHECK(payment_status IN ('PAGADO','EXONERADO'))"
        )
        connection.execute(
            "ALTER TABLE receipt_self_pay DROP CONSTRAINT receipt_self_pay_check"
        )
        connection.execute(
            "ALTER TABLE receipt_self_pay ADD CONSTRAINT receipt_self_pay_check CHECK(coverage='NO_ASEGURADO' OR payment_status='PAGADO')"
        )
        install_schema(connection)
        install_schema(connection)
        row = connection.execute(
            "SELECT payment_status,paid_at FROM receipt_self_pay WHERE receipt_id=%s",
            (identity,),
        ).fetchone()
        assert (
            row["payment_status"] == "PAGADO"
            and row["paid_at"].strftime("%Y-%m-%d") == "2026-09-05"
        )
    direct_save(recibo_id=identity, payment_status="PENDIENTE_PAGO")
    assert app.get_recibo_data(identity)["payment_status"] == "PENDIENTE_PAGO"


def test_uninsured_pending_can_be_exempted_and_pending_has_no_payment_time(tariffs):
    identity = direct_save(coverage="NO_ASEGURADO", payment_status="PENDIENTE_PAGO")
    direct_save(
        recibo_id=identity,
        coverage="NO_ASEGURADO",
        payment_status="EXONERADO",
        exemption_reason="Exoneracion autorizada",
    )
    with app.db_connect() as connection:
        assert (
            connection.execute(
                "SELECT paid_at FROM receipt_self_pay WHERE receipt_id=%s", (identity,)
            ).fetchone()[0]
            is None
        )
    assert app.get_recibo_data(identity)["payment_status"] == "EXONERADO"
