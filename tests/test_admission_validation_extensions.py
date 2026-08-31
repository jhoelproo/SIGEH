import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


class _Cursor:
    def __init__(self, rows=None, rowcount=1):
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, rows=None):
        self.rows = rows or []
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
                f"SQL esperaba {compact.count('%s')} parámetros y recibió {len(params)}"
            )
        self.calls.append((compact, params))
        return _Cursor(self.rows)


class _SaveConnection(_Connection):
    def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        params = tuple(params or ())
        if compact.count("%s") != len(params):
            raise AssertionError(
                f"SQL esperaba {compact.count('%s')} parámetros y recibió {len(params)}"
            )
        self.calls.append((compact, params))
        if compact.startswith("INSERT INTO recibos("):
            return _Cursor([(77,)])
        return _Cursor()


class AdmissionValidationExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

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

    def test_role_capabilities_are_canonical_and_restricted(self):
        allowed = (
            {"username": "admin", "role": app.ROLE_ADMIN},
            {"username": "audit", "role": app.ROLE_AUDIT},
        )
        denied = (
            {"username": "normal", "role": "facturador"},
            {"username": "viewer", "role": app.ROLE_AUX},
            {},
        )
        for user in allowed:
            self.assertTrue(app.can_bypass_patient_verification(user))
            self.assertTrue(app.can_confirm_unlinked_receipt(user))
            self.assertTrue(app.can_view_uninsured_patients(user))
        for user in denied:
            self.assertFalse(app.can_bypass_patient_verification(user))
            self.assertFalse(app.can_confirm_unlinked_receipt(user))
            self.assertFalse(app.can_view_uninsured_patients(user))

    def test_validation_and_history_exclusions_are_distinct(self):
        for value in ("Universal", "BANCO   CENTRAL", "senasa subsidiado"):
            self.assertFalse(app.admission_ars_is_visible(value))
            self.assertFalse(app.admission_ars_is_visible(value, history=True))
        self.assertTrue(app.admission_ars_is_visible("YUNEN"))
        self.assertFalse(app.admission_ars_is_visible("YUNEN", history=True))
        self.assertTrue(app.admission_ars_is_visible("HUMANO"))

    def test_quick_list_filters_in_postgres_and_excludes_dismissals(self):
        connection = _Connection()
        with patch.object(app, "db_connect", return_value=connection):
            result = app.list_projected_current_and_previous_billable_attentions(
                " 001-02 ", turn_filter="HEREDADO", allow_uninsured=False
            )
        self.assertEqual(result, [])
        sql, params = connection.calls[-1]
        self.assertIn("admission_quick_list_dismissals", sql)
        self.assertIn("BANCOCENTRAL", sql)
        self.assertIn("UNIVERSAL", sql)
        self.assertIn("SENASASUB", sql)
        self.assertIn("HEREDADO", params)
        self.assertIn("00102", params)
        self.assertNotIn("turn_rank", sql)
        self.assertIn("inheritance.estado='PENDIENTE'", sql)

    def test_history_rejects_normal_role_before_database_access(self):
        with patch.object(app, "db_connect") as connect:
            with self.assertRaises(PermissionError):
                app.list_admission_history(
                    current_user={"role": "facturador"},
                    coverage_filter="TODOS",
                    identifier="000-1",
                )
        connect.assert_not_called()

    def test_history_allows_uninsured_filter_for_audit_role(self):
        connection = _Connection()
        with patch.object(app, "db_connect", return_value=connection):
            app.list_admission_history(
                current_user={"role": app.ROLE_AUDIT},
                coverage_filter="SIN_SEGURO",
            )
        sql, params = connection.calls[-1]
        self.assertIn("SIN_SEGURO", params)
        self.assertIn(
            "p.operational_source_id::TEXT=cs.operational_source_id", sql
        )
        self.assertNotIn("p.readiness=", sql)

    def test_bypass_is_rejected_for_normal_role_before_database_write(self):
        with patch.object(
            app,
            "get_user",
            return_value={"username": "normal", "role": "facturador"},
        ), patch.object(app, "db_connect") as connect:
            with self.assertRaises(PermissionError):
                app.save_receipt_with_items(
                    None, 1, "Paciente", "2026-08-01", "DX", "HUMANO",
                    0, 10, "", "normal", 0, "2026-08-01", [],
                    verification_bypass={"reason": "motivo válido", "role": "facturador"},
                )
        connect.assert_not_called()

    def test_authorized_bypass_creates_manual_origin_and_audit_events(self):
        connection = _SaveConnection()
        with patch.object(
            app,
            "get_user",
            return_value={"username": "audit", "role": app.ROLE_AUDIT},
        ), patch.object(app, "db_connect", return_value=connection), patch.object(
            app,
            "save_receipt_document_snapshot",
            return_value={"version": 1},
        ):
            saved_id = app.save_receipt_with_items(
                None, 1, "Paciente", "2026-08-01", "DX", "HUMANO",
                0, 10, "", "audit", 0, "2026-08-01", [],
                verification_bypass={"reason": "emergencia administrativa", "role": app.ROLE_AUDIT},
            )
        self.assertEqual(saved_id, 77)
        joined = "\n".join(sql for sql, _params in connection.calls)
        self.assertIn("receipt_origin='MANUAL_PRIVILEGED'", joined)
        self.assertTrue(any(
            "PATIENT_VERIFICATION_BYPASSED" in params
            for _sql, params in connection.calls
        ))
        actions = [
            params for sql, params in connection.calls
            if sql.startswith("INSERT INTO action_history")
        ]
        self.assertTrue(any("BYPASS_RECEIPT_CREATED" in params for params in actions))

    def test_bypass_authorization_controls_document_and_review_without_attention(self):
        scenarios = (
            ("123456", app.DOCUMENT_READY, app.AUTH_REVIEW_CLEAR, False),
            ("ABC-123", app.DOCUMENT_READY, app.AUTH_REVIEW_PENDING, True),
            ("", app.DOCUMENT_PRELIMINARY, app.AUTH_REVIEW_NOT_APPLICABLE, False),
        )
        for authorization, document_state, review_status, flagged in scenarios:
            with self.subTest(authorization=authorization):
                connection = _SaveConnection()
                with patch.object(
                    app,
                    "get_user",
                    return_value={"username": "audit", "role": app.ROLE_AUDIT},
                ), patch.object(
                    app, "db_connect", return_value=connection
                ), patch.object(
                    app,
                    "save_receipt_document_snapshot",
                    return_value={"version": 1},
                ):
                    app.save_receipt_with_items(
                        None, 2, "Paciente", "2026-08-01", "DX", "HUMANO",
                        0, 10, "", "audit", 0, "2026-08-01", [],
                        authorization_number=authorization,
                        verification_bypass={
                            "reason": "emergencia administrativa",
                            "role": app.ROLE_AUDIT,
                        },
                    )
                insert_params = next(
                    params
                    for sql, params in connection.calls
                    if sql.startswith("INSERT INTO recibos(")
                )
                self.assertIn(document_state, insert_params)
                self.assertIn(review_status, insert_params)
                actions = [
                    params
                    for sql, params in connection.calls
                    if sql.startswith("INSERT INTO action_history")
                ]
                self.assertEqual(
                    any("BYPASS_RECEIPT_REVIEW_FLAGGED" in row for row in actions),
                    flagged,
                )
                executed_sql = "\n".join(sql for sql, _params in connection.calls)
                self.assertNotIn("INSERT INTO admission_attention", executed_sql)
                self.assertEqual(app._admission_values(None)[15], "")

    def test_editing_existing_bypass_reclassifies_authorization_without_attention(self):
        current = {
            "estado_facturacion": app.BILLING_PENDING,
            "revision_version": 0,
            "total": 10,
            "sala": 0,
            "ars": "HUMANO",
            "tipo_cobertura": "ASEGURADO",
            "numero_autorizacion": "",
            "estado_documento": app.DOCUMENT_PRELIMINARY,
            "admission_atencion_id": None,
            "admission_nss_snapshot": None,
            "admission_cedula_snapshot": None,
            "admission_source_instance_id": None,
            "verification_bypassed": True,
            "verification_bypass_role": app.ROLE_AUDIT,
            "verification_bypass_device": "PC-2",
            "receipt_origin": "MANUAL_PRIVILEGED",
            "was_invoiced": False,
        }

        class EditingConnection(_SaveConnection):
            def execute(self, sql, params=None):
                compact = " ".join(str(sql).split())
                if compact.startswith("SELECT estado_facturacion"):
                    params = tuple(params or ())
                    self.calls.append((compact, params))
                    return _Cursor([current])
                return super().execute(sql, params)

        connection = EditingConnection()
        with patch.object(
            app, "db_connect", return_value=connection
        ), patch.object(
            app, "save_receipt_document_snapshot", return_value={"version": 2}
        ):
            saved_id = app.save_receipt_with_items(
                77,
                2,
                "Paciente",
                "2026-08-01",
                "DX",
                "HUMANO",
                0,
                10,
                "",
                "audit",
                0,
                "2026-08-01",
                [],
                authorization_number="ABC123",
            )
        self.assertEqual(saved_id, 77)
        update_params = next(
            params
            for sql, params in connection.calls
            if sql.startswith("UPDATE recibos SET nombre=")
        )
        self.assertIn(app.DOCUMENT_READY, update_params)
        self.assertIn(app.AUTH_REVIEW_PENDING, update_params)
        self.assertIn("INVALID_AUTHORIZATION_FORMAT", update_params)
        self.assertTrue(
            any(
                "BYPASS_RECEIPT_REVIEW_FLAGGED" in params
                for sql, params in connection.calls
                if sql.startswith("INSERT INTO action_history")
            )
        )

    def test_bulk_wrapper_requests_one_tolerant_transaction(self):
        with patch.object(
            app,
            "add_receipts_to_monthly_batch",
            return_value={"added": 2, "omitted": 1},
        ) as service:
            result = app.add_all_receipts_to_monthly_batch(
                9, [1, 2, 3], {"username": "audit"},
                date_from="2026-08-01", date_to="2026-08-31",
            )
        self.assertEqual(result, {"added": 2, "omitted": 1})
        service.assert_called_once_with(
            9, [1, 2, 3], {"username": "audit"},
            date_from="2026-08-01", date_to="2026-08-31",
            skip_ineligible=True, return_summary=True,
        )

    def test_validation_dialog_has_turns_history_and_privileged_actions(self):
        with patch.object(app.QTimer, "singleShot", return_value=None):
            dialog = app.AdmissionValidationDialog(
                current_user={"username": "audit", "role": app.ROLE_AUDIT},
                session_id="test",
            )
        try:
            self.assertEqual(dialog.table.columnCount(), 9)
            self.assertEqual(
                [dialog.turn_filter_combo.itemData(i) for i in range(3)],
                ["ACTUAL", "HEREDADO", "TODOS"],
            )
            self.assertEqual(dialog.turn_filter_combo.currentData(), "TODOS")
            self.assertTrue(dialog.history_button.isVisibleTo(dialog))
            self.assertFalse(dialog.bypass_button.isHidden())
            self.assertFalse(dialog.dismiss_button.isHidden())
        finally:
            dialog.close()

    def test_auxiliary_validation_starts_with_current_turn_only(self):
        with patch.object(app.QTimer, "singleShot", return_value=None):
            dialog = app.AdmissionValidationDialog(
                current_user={"username": "aux", "role": app.ROLE_AUX},
                session_id="test",
            )
        try:
            self.assertEqual(dialog.turn_filter_combo.currentData(), "ACTUAL")
        finally:
            dialog.close()

    def test_normal_role_cannot_see_privileged_validation_controls(self):
        with patch.object(app.QTimer, "singleShot", return_value=None):
            dialog = app.AdmissionValidationDialog(
                current_user={"username": "normal", "role": "facturador"},
                session_id="test",
            )
        try:
            self.assertTrue(dialog.bypass_button.isHidden())
            self.assertTrue(dialog.dismiss_button.isHidden())
            self.assertTrue(dialog.history_button.isHidden())
            with self.assertRaises(PermissionError):
                app.AdmissionHistoryDialog(
                    current_user={"username": "normal", "role": "facturador"},
                )
        finally:
            dialog.close()

    def test_available_batch_query_filters_deleted_and_invoiced_receipts(self):
        connection = _Connection()
        rows = app._query_available_receipts_for_batch(
            connection,
            1,
            {"ars": "HUMANO"},
            limit=25,
        )
        self.assertEqual(rows, [])
        sql, _params = connection.calls[-1]
        self.assertIn("r.is_deleted=0", sql)
        self.assertIn("r.estado_facturacion IN ('PENDIENTE','SIN_CLASIFICAR')", sql)
        self.assertIn("NOT EXISTS", sql)

    def test_migration_is_idempotent_and_preserves_clinical_rows(self):
        migration = (
            Path(__file__).parents[1]
            / "migrations"
            / "20260801_admission_validation_history.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("IF NOT EXISTS admission_quick_list_dismissals", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS verification_bypassed", migration)
        self.assertNotIn("DELETE FROM ADMISSION", migration.upper())


if __name__ == "__main__":
    unittest.main()
