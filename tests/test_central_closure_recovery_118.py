import json
from uuid import uuid4

import CALCULOS_QT as app
from admission_authorship import repair_admission_authorship
from admission_urgency_repair import repair_urgency_projection
from billing_closure_recovery import (
    pending_central_closures,
    closure_from_interval,
    closed_turn_attentions,
)
from tests import test_integral_emergency_to_monthly_list as integration


def test_recovery_carries_pending_across_multiple_days_and_is_idempotent():
    fixture = integration.IntegralEmergencyToMonthlyListTests(
        "test_emergency_to_audit_to_monthly_ars_list"
    )
    fixture.setUp()
    try:
        epoch, session, source = (str(uuid4()) for _ in range(3))
        with app.db_connect() as con:
            con.execute(
                "UPDATE billing_closure_dispatch_policy SET enabled_at='2026-09-07 00:00:00-04'"
            )
            con.execute(
                """INSERT INTO sigeh_product_state(singleton,product_id,
                bootstrap_version,production_epoch_id,bootstrap_status,bootstrap_completed_at)
                VALUES(1,'SIGEH','1.1.8',%s,'COMPLETED',NOW())""",
                (epoch,),
            )
            con.execute(
                """INSERT INTO admission_operational_sessions(
                operational_session_id,active_username,primary_device_id,
                primary_login_session_id,operational_source_id,production_epoch_id,turn_id)
                VALUES(%s,'SYNTHETIC','PC','LOGIN',%s,%s,104)""",
                (session, source, epoch),
            )
            for turn, day, end in [(101, 7, 8), (102, 8, 9), (103, 9, 10)]:
                con.execute(
                    """INSERT INTO admission_operational_audit(
                        operational_session_id,event_type,username,details_json)
                        VALUES(%s,'TURN_HANDOFF_TRANSITION','SYNTHETIC',%s::jsonb)""",
                    (
                        session,
                        json.dumps(
                            {
                                "status": "COMMITTED",
                                "request": {"transition_type": "PRIMARY_USER_HANDOFF"},
                                "result": {"old_turn_id": turn},
                            }
                        ),
                    ),
                )
                con.execute(
                    """INSERT INTO admission_operational_turn_intervals(
                    operational_session_id,generation,turn_id,active_username,
                    started_at,ended_at,production_epoch_id)
                    VALUES(%s,%s,%s,'SYNTHETIC',%s,%s,%s)""",
                    (
                        session,
                        turn,
                        turn,
                        f"2026-09-{day:02} 08:00:00.123456-04",
                        f"2026-09-{end:02} 08:00:00.123456-04",
                        epoch,
                    ),
                )
            for number, kind in [(1, "EMERGENCIA"), (2, "URGENCIA")]:
                con.execute(
                    """INSERT INTO admission_attention_projection(
                    source_instance_id,attention_id,patient_id,turn_id,service_date,
                    patient_name,coverage_status,canonical_ars,readiness,snapshot_hash,
                    contract_version,synced_at,global_attention_id,operational_source_id,
                    service_type,latest_payload_json)
                    VALUES('station',%s,%s,101,'2026-09-07','SYNTHETIC',
                    'ASEGURADO_VALIDADO','HUMANO','LISTA','hash',2,'2026-09-07',%s,%s,
                    'EMERGENCIA',%s::jsonb)""",
                    (
                        number,
                        number,
                        str(uuid4()),
                        source,
                        json.dumps({"service_type": kind}),
                    ),
                )
            assert repair_urgency_projection(con) == 1
            assert repair_urgency_projection(con) == 0
            global_id = str(
                con.execute(
                    "SELECT global_attention_id FROM admission_attention_projection WHERE attention_id=1"
                ).fetchone()[0]
            )
            con.execute(
                """INSERT INTO admission_sync_events(
                event_uuid,entity_type,entity_uuid,operation,payload_json,
                operational_session_id,generation,origin_device_id)
                VALUES(%s,'attention',%s,'CREATE',%s::jsonb,%s,1,'PC')""",
                (
                    str(uuid4()),
                    global_id,
                    json.dumps({"admission_username": "ORIGINAL"}),
                    session,
                ),
            )
            con.execute(
                "UPDATE admission_attention_projection SET admission_username='EDITOR' WHERE attention_id=1"
            )
            assert repair_admission_authorship(con) == 1
            assert repair_admission_authorship(con) == 0
            assert (
                con.execute(
                    "SELECT admission_username FROM admission_attention_projection WHERE attention_id=1"
                ).fetchone()[0]
                == "ORIGINAL"
            )
            pending = pending_central_closures(con)
            next_event = closure_from_interval(dict(pending[1]))
            assert len(closed_turn_attentions(con, next_event, previous=True)) == 2
            con.execute("SAVEPOINT boundaries")
            con.execute(
                "UPDATE billing_closure_dispatch_policy SET enabled_at='2026-09-10 08:00:01-04'"
            )
            assert pending_central_closures(con) == []
            con.execute(
                "UPDATE billing_closure_dispatch_policy SET enabled_at='2026-09-10 08:00:00-04'"
            )
            assert len(pending_central_closures(con)) == 1
            con.execute("ROLLBACK TO SAVEPOINT boundaries")
            con.execute(
                "UPDATE admission_operational_turn_intervals SET ended_at='2026-09-07 00:00:00-04',started_at='2026-09-06 08:00:00-04'"
            )
            assert len(pending_central_closures(con)) == 3
            con.execute(
                "UPDATE admission_operational_turn_intervals SET ended_at='2026-09-06 23:59:59-04'"
            )
            assert pending_central_closures(con) == []
            con.execute("ROLLBACK TO SAVEPOINT boundaries")
            con.execute("UPDATE admission_operational_turn_intervals SET ended_at=NULL")
            assert pending_central_closures(con) == []
            con.execute("ROLLBACK TO SAVEPOINT boundaries")
            for path, value in [
                ("{status}", "PENDING"),
                ("{request,transition_type}", "ADMIN_OVERRIDE"),
            ]:
                con.execute(
                    "UPDATE admission_operational_audit SET details_json=jsonb_set(details_json,%s,%s::jsonb)",
                    (path, json.dumps(value)),
                )
                assert pending_central_closures(con) == []
                con.execute("ROLLBACK TO SAVEPOINT boundaries")
            event = closure_from_interval(dict(pending[0]))
            assert len(closed_turn_attentions(con, event)) == 2
            con.execute("UPDATE admission_attention_projection SET is_deleted=TRUE")
            assert closed_turn_attentions(con, event) == []
            con.execute("ROLLBACK TO SAVEPOINT boundaries")
            con.execute(
                "UPDATE admission_attention_projection SET source_status='ANULADA'"
            )
            assert closed_turn_attentions(con, event) == []
            con.execute("ROLLBACK TO SAVEPOINT boundaries")
            con.execute("RELEASE SAVEPOINT boundaries")
        assert len(pending) == 3
        # A future close must include pending prior admissions even when the
        # operator deliberately does not regenerate the missing older reports.
        newest = closure_from_interval(dict(pending[-1]))
        snapshot = app.capture_shift_closure_snapshot(newest, [])
        assert snapshot["inherited_received"] == 1
        assert snapshot["pending_next"] == 1
        for authorization_at, received, authorized, remaining in [
            ("2026-09-09 09:00:00", 1, 1, 0),
            ("2026-09-10 09:00:00", 1, 0, 1),
            ("2026-09-08 09:00:00", 0, 0, 0),
        ]:
            with app.db_connect() as con:
                con.execute("DELETE FROM billing_shift_closure_details")
                con.execute("DELETE FROM billing_shift_closures")
                con.execute("DELETE FROM admission_shift_inheritances")
                con.execute("DELETE FROM recibos WHERE numero=999123")
                con.execute(
                    """INSERT INTO recibos(numero,fecha,created_at,numero_autorizacion,
                    autorizacion_at,admission_global_attention_id,admission_atencion_id,
                    admission_source_instance_id) VALUES
                    (999123,'2026-09-07','2026-09-07 12:00:00','AUTH-123',%s,%s,1,'station')""",
                    (authorization_at, global_id),
                )
            snapshot = app.capture_shift_closure_snapshot(newest, [])
            assert snapshot["inherited_received"] == received
            assert snapshot["inherited_authorized"] == authorized
            assert snapshot["pending_next"] == remaining
        with app.db_connect() as con:
            con.execute("DELETE FROM billing_shift_closure_details")
            con.execute("DELETE FROM billing_shift_closures")
            con.execute("DELETE FROM admission_shift_inheritances")
            con.execute("DELETE FROM recibos WHERE numero=999123")
        for index, interval in enumerate(pending):
            event = closure_from_interval(dict(interval))
            with app.db_connect() as con:
                rows = closed_turn_attentions(con, event)
            snapshot = app.capture_shift_closure_snapshot(event, rows)
            assert snapshot["pending_next"] == 1
            assert snapshot["inherited_received"] == (0 if index == 0 else 1)
            assert not app.capture_shift_closure_snapshot(event, rows)[
                "snapshot_created"
            ]
        with app.db_connect() as con:
            con.execute("SAVEPOINT delivery_states")
            con.execute(
                "UPDATE billing_shift_closures SET pdf_status='GENERADO', print_requested_at=NULL"
            )
            assert len(pending_central_closures(con)) == 3
            con.execute("UPDATE billing_shift_closures SET print_requested_at=NOW()")
            assert pending_central_closures(con) == []
            con.execute("UPDATE billing_shift_closures SET pdf_status='OMITIDO_VACIO'")
            assert len(pending_central_closures(con)) == 3
            con.execute("ROLLBACK TO SAVEPOINT delivery_states")
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM admission_shift_inheritances WHERE estado='PENDIENTE'"
                ).fetchone()[0]
                == 1
            )
        inherited = app.BillingAdmissionQueryService().get_operational_candidates(
            turn_filter="HEREDADO",
            current_user={"username": "billing", "role": app.ROLE_ADMIN},
        )
        assert len(inherited) == 1
        assert inherited[0].attention_id == 1
        with app.db_connect() as con:
            con.execute(
                "UPDATE billing_shift_closures SET status='COMPLETED', pdf_status='OMITIDO_VACIO'"
            )
        recovered = app.claim_shift_closure("SYNTHETIC", source, 101)
        assert recovered["status"] == "GENERATING"
        assert app.claim_shift_closure("SYNTHETIC", source, 101) is None
    finally:
        fixture.tearDown()
