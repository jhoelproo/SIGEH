import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import (
    GLOBAL,
    database as database,
    server as server,
)


@pytest.mark.parametrize(
    "authorization_at,expected",
    [
        ("2026-09-07 18:00:00", "AUTH-123"),
        ("2026-09-08 10:00:00", None),
        ("", "AUTH-123"),
    ],
)
def test_receipt_query_links_uuid_and_preserves_closure_cutoff(
    database, authorization_at, expected
):
    with database() as connection:
        for column in (
            "numero_autorizacion",
            "autorizacion_at",
            "autorizacion_por",
            "username",
            "fecha",
            "ars",
            "admission_nss_snapshot",
            "admission_cedula_snapshot",
        ):
            connection.execute(f"ALTER TABLE recibos ADD COLUMN {column} TEXT")
        connection.execute(
            """INSERT INTO recibos(id,numero,admission_global_attention_id,
            admission_atencion_id,admission_source_instance_id,created_at,fecha,
            numero_autorizacion,autorizacion_at) VALUES
            (1,1001,%s,999,'OLD-STATION','2026-09-07 09:00:00','2026-09-07','AUTH-123',%s)""",
            (GLOBAL, authorization_at),
        )
        records = [
            dict(
                source_instance_id="ORIGIN",
                attention_id=329,
                service_date="2026-09-07",
                global_attention_id=GLOBAL,
            )
        ]
        receipts = app._receipt_candidates_for_shift(
            connection, records, "2026-09-08 08:52:12"
        )
        assert len(receipts) == 1
        assert receipts[0]["numero_autorizacion"] == expected
        assert app._link_shift_receipts(records, receipts)[("ORIGIN", 329)]["id"] == 1
