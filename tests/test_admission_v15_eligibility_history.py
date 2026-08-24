import unittest
from unittest.mock import patch

import CALCULOS_QT as app


class _Cursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        params = tuple(params or ())
        if compact.count("%s") != len(params):
            raise AssertionError(
                f"SQL esperaba {compact.count('%s')} parametros y recibio {len(params)}"
            )
        self.calls.append((compact, params))
        return _Cursor(self.rows)


def _projection_row(attention_id, *, has_sheet=False):
    return {
        "source_instance_id": "V15-CENTRAL",
        "attention_id": attention_id,
        "patient_id": attention_id + 1000,
        "turn_id": 22,
        "patient_name": f"PACIENTE {attention_id}",
        "service_date": "2026-08-08",
        "service_time": "08:00:00",
        "nss_snapshot": "000123456",
        "cedula_snapshot": "00100000011",
        "canonical_ars": "HUMANO",
        "service_type": "EMERGENCIA",
        "source_status": "ACTIVA",
        "coverage_status": "ASEGURADO_VALIDADO",
        "readiness": app.READINESS_READY,
        "readiness_reasons": "[]",
        "has_detail_sheet": has_sheet,
        "turn_scope": "TURNO ACTUAL",
        "processing_turn_id": 22,
    }


class AdmissionV15EligibilityHistoryTests(unittest.TestCase):
    def setUp(self):
        self.central_context = patch.object(
            app,
            "get_central_operational_context",
            return_value={
                "source_instance_id": "V15-CENTRAL",
                "operational_source_id": "V15-CENTRAL",
                "turn_id": 22,
                "generation": 1,
            },
        )
        self.central_context.start()

    def tearDown(self):
        self.central_context.stop()

    def test_eligibility_supports_turns_and_name_cedula_nss_search(self):
        for turn_filter in ("ACTUAL", "HEREDADO", "TODOS"):
            for identifier, expected in (
                ("ANA PEREZ", "%ANA PEREZ%"),
                ("001-0000001-1", "00100000011"),
                (" 000-123-456 ", "000123456"),
            ):
                with self.subTest(turn_filter=turn_filter, identifier=identifier):
                    connection = _Connection()
                    with patch.object(app, "db_connect", return_value=connection):
                        app.list_projected_current_and_previous_billable_attentions(
                            identifier,
                            turn_filter=turn_filter,
                            session_id="session-v15",
                        )
                    sql, params = connection.calls[-1]
                    self.assertIn(turn_filter, params)
                    self.assertIn(expected, params)
                    self.assertIn("p.patient_name ILIKE", sql)
                    self.assertIn("p.nss_snapshot", sql)
                    self.assertIn("p.cedula_snapshot", sql)
                    self.assertNotIn("turn_rank", sql)
                    self.assertIn("inheritance.estado='PENDIENTE'", sql)

    def test_eligibility_excludes_processed_cancelled_claimed_and_excluded_ars(self):
        connection = _Connection()
        with patch.object(app, "db_connect", return_value=connection):
            app.list_projected_current_and_previous_billable_attentions()
        sql, _params = connection.calls[-1]
        self.assertIn("r.admission_atencion_id=p.attention_id", sql)
        self.assertIn("r.admission_source_instance_id", sql)
        self.assertIn("admission_billing_claims", sql)
        self.assertIn("admission_quick_list_dismissals", sql)
        self.assertIn("p.source_status", sql)
        self.assertIn("('ACTIVA','PENDIENTE')", sql)
        for ars_key in ("SENASASUB", "UNIVERSAL", "BANCOCENTRAL"):
            self.assertIn(ars_key, sql)

    def test_with_and_without_detail_sheet_remain_eligible(self):
        rows = [
            _projection_row(101, has_sheet=True),
            _projection_row(102, has_sheet=False),
        ]
        connection = _Connection(rows)
        with patch.object(app, "db_connect", return_value=connection):
            result = app.list_projected_current_and_previous_billable_attentions()
        self.assertEqual([item.attention_id for item in result], [101, 102])
        self.assertEqual([item.has_detail_sheet for item in result], [True, False])
        sql, _params = connection.calls[-1]
        self.assertNotIn("AND p.has_detail_sheet", sql)

    def test_uninsured_visibility_depends_on_billing_role(self):
        for role, allowed in (
            ("facturador", False),
            (app.ROLE_AUDIT, True),
            (app.ROLE_ADMIN, True),
        ):
            connection = _Connection()
            with patch.object(app, "db_connect", return_value=connection):
                app.load_admission_validation_attentions(
                    current_user={"role": role},
                )
            _sql, params = connection.calls[-1]
            readiness_index = params.index(app.READINESS_READY)
            self.assertEqual(params[readiness_index + 1], allowed)

    def test_final_claim_revalidates_all_billing_rules(self):
        connection = _Connection()
        with patch.object(app, "db_connect", return_value=connection):
            result = app.claim_projected_billable_attention(
                901,
                "V15-CENTRAL",
                username="audit",
                session_id="session-v15",
                current_user={"role": app.ROLE_AUDIT},
            )
        self.assertIsNone(result)
        sql, params = connection.calls[-1]
        self.assertIn("p.attention_id=%s AND p.source_instance_id=%s", sql)
        self.assertIn("r.admission_atencion_id=p.attention_id", sql)
        self.assertIn("admission_quick_list_dismissals", sql)
        self.assertIn("p.source_status", sql)
        self.assertIn("p.coverage_status", sql)
        self.assertIn("SENASASUB", sql)
        self.assertIn(True, params)

    def test_billing_history_matches_active_v15_universe_and_left_joins_billing(self):
        connection = _Connection()
        with patch.object(app, "db_connect", return_value=connection):
            app.list_admission_history(
                current_user={"role": app.ROLE_AUDIT},
                receipt_filter="CON_RECIBO",
                sheet_filter="CON_HOJA",
            )
        sql, params = connection.calls[-1]
        self.assertIn("LEFT JOIN LATERAL", sql)
        self.assertIn(
            "p.source_status,'ACTIVA'))) IN ('ACTIVA','PENDIENTE')", sql
        )
        self.assertNotIn("p.readiness=", sql)
        self.assertNotIn("NOT EXISTS ( SELECT 1 FROM recibos", sql)
        self.assertTrue(params[2])


if __name__ == "__main__":
    unittest.main()
