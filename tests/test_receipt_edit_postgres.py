from unittest.mock import patch

import pytest
import CALCULOS_QT as app
from receipt_documents import load_current_receipt_snapshot
from tests import test_integral_emergency_to_monthly_list as integration


@pytest.fixture
def database():
    fixture = integration.IntegralEmergencyToMonthlyListTests(
        "test_emergency_to_audit_to_monthly_ars_list"
    )
    fixture.setUp()
    try:
        yield
    finally:
        fixture.tearDown()


def test_legacy_edit_preserves_creator_timestamp_and_insurance(database):
    with app.db_connect() as connection:
        receipt_id = connection.execute(
            """INSERT INTO recibos(numero,nombre,fecha,dx,ars,sala,total,username,created_at,
                tipo_cobertura,estado_facturacion,receipt_origin)
                VALUES(999001,'SINTETICO','2026-09-12','DX','APS',100,100,'original',
                '2026-09-12 10:00:00','ASEGURADO','SIN_CLASIFICAR','LEGACY') RETURNING id"""
        ).fetchone()[0]
    with patch.object(
        app, "get_user", return_value={"username": "editor", "role": app.ROLE_ADMIN}
    ):
        for authorization in ("19", "123", "1234", "3901505"):
            app.save_receipt_with_items(
                receipt_id,
                999001,
                "SINTETICO CORREGIDO",
                "13/09/2026",
                "DX NUEVO",
                "APS",
                100,
                100,
                "",
                "editor",
                0,
                "2026-09-14 15:00:00",
                [],
                authorization_number=authorization,
                document_context={"visible_user": "Editor"},
            )
            with app.db_connect() as connection:
                row = connection.execute(
                    "SELECT * FROM recibos WHERE id=%s", (receipt_id,)
                ).fetchone()
                snapshot = load_current_receipt_snapshot(connection, receipt_id)
            assert row["fecha"] == "2026-09-13"
            assert row["username"] == "original"
            assert row["created_at"] == "2026-09-12 10:00:00"
            assert row["receipt_origin"] == "LEGACY"
            assert not row["verification_bypassed"]
            assert snapshot["snapshot"]["document"]["visible_user"] == "original"
        with pytest.raises(PermissionError):
            app.save_receipt_with_items(
                receipt_id,
                999001,
                "SINTETICO",
                "2026-09-13",
                "DX",
                "FUTURO",
                100,
                100,
                "",
                "editor",
                0,
                "",
                [],
            )
    with app.db_connect() as connection:
        row = connection.execute(
            "SELECT ars,revision_version FROM recibos WHERE id=%s", (receipt_id,)
        ).fetchone()
        audit = connection.execute(
            "SELECT realizado_por FROM recibo_facturacion_history WHERE recibo_id=%s",
            (receipt_id,),
        ).fetchall()
    assert row["ars"] == "APS" and row["revision_version"] == 4
    assert len(audit) == 4 and all(item["realizado_por"] == "editor" for item in audit)
