import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


class _Cursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchall(self):
        return self._rows


class _Connection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=()):
        compact = " ".join(str(sql).split())
        params = tuple(params or ())
        if compact.count("%s") != len(params):
            raise AssertionError(
                f"SQL expected {compact.count('%s')} params; got {len(params)}"
            )
        self.calls.append((compact, params))
        return _Cursor(self.rows)


class _Repository:
    def get_current_shift_context(self):
        return {"source_instance_id": "V15-CENTRAL", "turn_id": 278}


def _history_row(attention_id, status="PENDIENTE"):
    return {
        "source_instance_id": "V15-CENTRAL",
        "attention_id": attention_id,
        "patient_id": 1000 + attention_id,
        "patient_name": f"PACIENTE {attention_id}",
        "cedula_snapshot": "00100000011",
        "nss_snapshot": "000123456",
        "service_date": "2026-08-08",
        "service_time": f"08:{attention_id % 60:02d}:00",
        "turn_id": 278,
        "processing_turn_id": 278,
        "turn_scope": "TURNO ACTUAL",
        "service_type": "EMERGENCIA",
        "canonical_ars": "HUMANO",
        "admission_username": "OPERADOR ADMISION",
        "has_detail_sheet": True,
        "receipt_id": attention_id,
        "receipt_number": 990000 + attention_id,
        "estado_facturacion": status,
        "estado_documento": "FINAL",
    }


class BillingAdmissionTurnHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        app.invalidate_admission_validation_cache()
        self.projection_reconcile_patch = patch.object(
            app,
            "ensure_admission_history_projection",
            return_value={"synced": 0, "already_current": True},
        )
        self.projection_reconcile = self.projection_reconcile_patch.start()
        self.central_context = patch.object(
            app,
            "get_central_operational_context",
            return_value={
                "source_instance_id": "OPERATIONAL-SOURCE",
                "operational_source_id": "OPERATIONAL-SOURCE",
                "turn_id": 316,
                "generation": 5,
            },
        )
        self.central_context.start()

    def tearDown(self):
        self.central_context.stop()
        self.projection_reconcile_patch.stop()

    def test_operational_query_uses_one_central_shift_query_and_explicit_inheritance(
        self,
    ):
        connection = _Connection()
        service = app.BillingAdmissionQueryService(_Repository())
        with patch.object(app, "db_connect", return_value=connection):
            service.get_operational_candidates(turn_filter="TODOS")
        sql, params = connection.calls[-1]
        self.assertEqual(params[0:3], ("TODOS", "TODOS", "TODOS"))
        self.assertIn("FROM admission_operational_sessions", sql)
        self.assertIn("JOIN sigeh_product_state product", sql)
        self.assertIn("product.production_epoch_id=session.production_epoch_id", sql)
        self.assertIn("p.operational_source_id=cs.operational_source_id", sql)
        self.assertNotIn("SELECT p.*", sql)
        self.assertNotIn("local_shift", sql)
        self.assertIn("inheritance.estado='PENDIENTE'", sql)
        self.assertIn("inheritance.attention_id IS NOT NULL", sql)
        self.assertNotIn("turn_rank", sql)
        self.assertNotIn("MAX(p2.turn_id)", sql)

    def test_selector_does_not_invoke_local_projection_reconciliation(
        self,
    ):
        connection = _Connection()
        repository = _Repository()
        service = app.BillingAdmissionQueryService(repository)
        self.projection_reconcile.side_effect = AssertionError(
            "the selector must be central-only"
        )
        with patch.object(app, "db_connect", return_value=connection):
            service.get_operational_candidates()
        self.projection_reconcile.assert_not_called()
        self.assertTrue(connection.calls)

    def test_selector_returns_attention_already_materialized_centrally(self):
        repository = _Repository()
        row = _history_row(17)
        row.update(
            {
                "readiness": app.READINESS_READY,
                "coverage_status": "ASEGURADO",
                "source_status": "ACTIVA",
                "global_attention_id": "11111111-1111-4111-8111-111111111111",
                "operational_source_id": "OPERATIONAL-SOURCE",
            }
        )

        connection = _Connection([row])
        self.projection_reconcile.side_effect = AssertionError(
            "the selector must not materialize local data"
        )
        service = app.BillingAdmissionQueryService(repository)
        with patch.object(app, "db_connect", return_value=connection):
            result = service.get_operational_candidates()

        self.assertEqual([attention.attention_id for attention in result], [17])
        self.projection_reconcile.assert_not_called()

    def test_history_fetches_only_fifty_and_returns_keyset_cursor(self):
        rows = [_history_row(value) for value in range(100, 49, -1)]
        connection = _Connection(rows)
        service = app.BillingAdmissionQueryService(_Repository())
        with patch.object(app, "db_connect", return_value=connection):
            result = service.load_admission_history_batch(
                current_user={"role": app.ROLE_ADMIN}, limit=50
            )
        self.assertEqual(len(result["rows"]), 50)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_cursor"]["attention_id"], 51)
        sql, params = connection.calls[-1]
        self.assertNotIn(" OFFSET ", f" {sql} ")
        self.assertNotIn("COUNT(*) OVER", sql)
        self.assertIn(
            "COALESCE(p.created_at_effective_utc,TO_TIMESTAMP(0))", sql
        )
        self.assertNotIn("p.synced_at,'')::TIMESTAMPTZ", sql)
        self.assertEqual(params[-1], 51)

    def test_history_does_not_invoke_local_projection_reconciliation(
        self,
    ):
        connection = _Connection()
        repository = _Repository()
        service = app.BillingAdmissionQueryService(repository)
        self.projection_reconcile.side_effect = AssertionError(
            "the history must be central-only"
        )
        with patch.object(app, "db_connect", return_value=connection):
            result = service.load_admission_history_batch(
                current_user={"role": app.ROLE_ADMIN}, limit=50
            )
        self.projection_reconcile.assert_not_called()
        self.assertEqual(result["rows"], [])
        self.assertTrue(connection.calls)

    def test_privileged_queue_requires_explicit_previous_turn_inheritance(self):
        connection = _Connection()
        service = app.BillingAdmissionQueryService(_Repository())
        with patch.object(app, "db_connect", return_value=connection):
            service.get_operational_candidates(
                turn_filter="TODOS", allow_all_unbilled=True
            )
        sql, params = connection.calls[-1]
        self.assertNotIn("OR (%s AND p.turn_id<>cs.turn_id)", sql)
        self.assertIn("p.turn_id=cs.turn_id OR inheritance.attention_id IS NOT NULL", sql)
        self.assertNotIn(True, params)
        self.assertIn("ELSE 'HEREDADA' END AS turn_scope", sql)

    def test_typed_validation_search_reuses_the_short_lived_queue_snapshot(self):
        row = _history_row(17)
        row.update(
            {
                "readiness": app.READINESS_READY,
                "coverage_status": "ASEGURADO",
                "source_status": "ACTIVA",
                "global_attention_id": "11111111-1111-4111-8111-111111111111",
                "operational_source_id": "OPERATIONAL-SOURCE",
            }
        )
        connection = _Connection([row])
        with patch.object(app, "db_connect", return_value=connection):
            first = app.load_admission_validation_attentions(
                current_user={"role": app.ROLE_ADMIN}
            )
            typed = app.load_admission_validation_attentions(
                identifier="PACIENTE",
                current_user={"role": app.ROLE_ADMIN},
            )
        self.assertEqual(len(first), 1)
        self.assertEqual(len(typed), 1)
        self.assertEqual(len(connection.calls), 1)

    def test_history_is_visible_to_billing_roles_but_access_matrix_decides_use(self):
        for role in (app.ROLE_AUX, app.ROLE_ADMIN, app.ROLE_AUDIT):
            connection = _Connection()
            service = app.BillingAdmissionQueryService(_Repository())
            with patch.object(app, "db_connect", return_value=connection):
                result = service.load_admission_history_batch(
                    current_user={"role": role}
                )
            self.assertEqual(
                result["full_history"],
                role in {app.ROLE_ADMIN, app.ROLE_AUDIT},
            )

        service = app.BillingAdmissionQueryService(_Repository())
        with (
            patch.object(app, "db_connect") as connect,
            self.assertRaises(PermissionError),
        ):
            service.load_admission_history_batch(current_user={"role": "invitado"})
        connect.assert_not_called()

    def test_history_columns_and_billing_status_badges(self):
        with patch.object(app.QTimer, "singleShot", return_value=None):
            dialog = app.AdmissionHistoryDialog(current_user={"role": app.ROLE_ADMIN})
        try:
            headers = [
                dialog.table.horizontalHeaderItem(i).text()
                for i in range(dialog.table.columnCount())
            ]
            self.assertEqual(headers, list(app.AdmissionHistoryDialog.HEADERS))
            self.assertEqual(dialog.table.columnCount(), 13)
            self.assertNotIn("Estado", headers)
            self.assertEqual(dialog._page_size, 50)
            dialog._append_row(_history_row(1, app.BILLING_INVOICED))
            dialog._append_row(_history_row(2, app.BILLING_PENDING))
            self.assertEqual(dialog.table.item(0, 11).text(), "COMPLETO")
            self.assertEqual(dialog.table.item(1, 11).text(), "PENDIENTE")
        finally:
            dialog.close()

    def test_old_generation_cannot_contaminate_new_search(self):
        with patch.object(app.QTimer, "singleShot", return_value=None):
            dialog = app.AdmissionHistoryDialog(current_user={"role": app.ROLE_ADMIN})
        try:
            dialog._generation = 3
            dialog._apply_result(
                {
                    "generation": 2,
                    "rows": [_history_row(1)],
                    "has_more": False,
                }
            )
            self.assertEqual(dialog.table.rowCount(), 0)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
