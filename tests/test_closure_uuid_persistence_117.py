from dataclasses import replace
from uuid import uuid4

import CALCULOS_QT as app
from admission_bridge import AdmissionShiftClosure
from tests import test_integral_emergency_to_monthly_list as integration


def test_uuid_survives_pending_carryforward_and_later_authorization():
    fixture = integration.IntegralEmergencyToMonthlyListTests(
        "test_emergency_to_audit_to_monthly_ars_list"
    )
    fixture.setUp()
    try:
        global_id = str(uuid4())
        event = AdmissionShiftClosure(
            1,
            str(uuid4()),
            "synthetic-origin",
            901,
            1,
            "2026-09-07",
            "2026-09-07 08:00:00",
            "2026-09-07 20:00:00",
            "2026-09-07 20:00:00",
            "SYNTHETIC",
            "8AM_8PM",
            "admin",
            "administrador",
            "qa-session",
            "2026-09-07 20:00:00",
        )
        row = dict(
            attention_id=42,
            turn_id=901,
            source_instance_id="synthetic-origin",
            global_attention_id=global_id,
            service_date="2026-09-07",
            name="SYNTHETIC",
            canonical_ars="HUMANO",
            attention_type="EMERGENCIA",
        )
        first = app.capture_shift_closure_snapshot(event, [row])
        assert first["new_pending"] == 1
        with app.db_connect() as con:
            con.execute(
                """INSERT INTO recibos(numero,fecha,created_at,numero_autorizacion,
                autorizacion_at,admission_global_attention_id,admission_atencion_id,
                admission_source_instance_id) VALUES
                (991001,'2026-09-07','2026-09-07 21:00:00','AUTH-1234','2026-09-07 21:00:00',%s,999,'different-station')""",
                (global_id,),
            )
        second = app.capture_shift_closure_snapshot(
            replace(
                event,
                event_uuid=str(uuid4()),
                turn_id=902,
                closed_at="2026-09-08 08:00:00",
            ),
            [],
        )
        assert second["authorized_applicable"] == 1
        assert app.build_shift_closure_report_data(second)["historical_authorized"] == 1
        assert second["pending_next"] == 0
        with app.db_connect() as con:
            saved = con.execute("""SELECT global_attention_id FROM billing_shift_closure_details
                WHERE closure_source_instance_id='synthetic-origin' AND closure_turn_id=902""").fetchone()
        assert str(saved[0]) == global_id
    finally:
        fixture.tearDown()
