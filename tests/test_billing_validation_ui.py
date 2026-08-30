import inspect
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
from display_layout import should_expand_main_module_tabs


class BillingValidationUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_patient_validation_and_authorization_are_independent(self):
        self.assertFalse(
            app.billing_is_ready_for_audit(
                patient_validated=False,
                authorization="AUT-123",
            )
        )
        self.assertTrue(
            app.billing_is_ready_for_audit(
                patient_validated=True,
                authorization="AUT-123",
            )
        )
        state, ready, text = app.billing_readiness_presentation(
            patient_validated=False,
            authorization="",
            privileged_unlinked=False,
        )
        self.assertEqual(state, "preliminary")
        self.assertFalse(ready)
        self.assertNotIn("LISTO PARA AUDITORÍA", text)

        state, ready, text = app.billing_readiness_presentation(
            patient_validated=True,
            authorization="  ",
            privileged_unlinked=False,
        )
        self.assertEqual(state, "validated")
        self.assertFalse(ready)
        self.assertEqual(
            text,
            "PACIENTE VALIDADO · Falta ingresar el número de autorización.",
        )

        state, ready, text = app.billing_readiness_presentation(
            patient_validated=True,
            authorization="AUT-123",
            privileged_unlinked=False,
        )
        self.assertEqual(state, "ready")
        self.assertTrue(ready)
        self.assertEqual(
            text,
            "LISTO PARA AUDITORÍA · Se generará el documento completo.",
        )

        state, ready, text = app.billing_readiness_presentation(
            patient_validated=True,
            authorization="",
            privileged_unlinked=False,
        )
        self.assertEqual(state, "validated")
        self.assertFalse(ready)

    def test_privileged_role_message_has_no_mojibake_and_is_not_ready(self):
        state, ready, text = app.billing_readiness_presentation(
            patient_validated=False,
            authorization="",
            privileged_unlinked=True,
        )
        self.assertEqual(state, "validated")
        self.assertFalse(ready)
        self.assertEqual(
            text,
            "ROL AUTORIZADO · Puede crear el recibo sin validar previamente al paciente.",
        )
        self.assertNotIn("Â·", text)

    def test_privileged_bypass_ui_marks_valid_and_suspicious_auth_complete(self):
        state, ready, text = app.billing_readiness_presentation(
            patient_validated=False,
            authorization="123456",
            privileged_unlinked=True,
        )
        self.assertEqual((state, ready), ("ready", True))
        self.assertIn("LISTO PARA AUDITORÍA", text)

        state, ready, text = app.billing_readiness_presentation(
            patient_validated=False,
            authorization="ABC123",
            privileged_unlinked=True,
        )
        self.assertEqual((state, ready), ("ready", True))
        self.assertIn("REVISIÓN PENDIENTE", text)
        self.assertIn("INVALID_AUTHORIZATION_FORMAT", text)

    def test_privileged_bypass_document_and_review_classification(self):
        self.assertEqual(
            app.classify_privileged_bypass_authorization(""),
            (
                app.DOCUMENT_PRELIMINARY,
                app.AUTH_REVIEW_NOT_APPLICABLE,
                "AUTHORIZATION_MISSING",
            ),
        )
        self.assertEqual(
            app.classify_privileged_bypass_authorization("123456"),
            (app.DOCUMENT_READY, app.AUTH_REVIEW_CLEAR, ""),
        )
        self.assertEqual(
            app.classify_privileged_bypass_authorization("12345"),
            (
                app.DOCUMENT_READY,
                app.AUTH_REVIEW_PENDING,
                "AUTHORIZATION_TOO_SHORT",
            ),
        )
        self.assertEqual(
            app.classify_privileged_bypass_authorization("AUT-123456"),
            (
                app.DOCUMENT_READY,
                app.AUTH_REVIEW_PENDING,
                "INVALID_AUTHORIZATION_FORMAT",
            ),
        )
        self.assertEqual(
            app.classify_privileged_bypass_authorization("١٢٣٤٥٦"),
            (
                app.DOCUMENT_READY,
                app.AUTH_REVIEW_PENDING,
                "INVALID_AUTHORIZATION_FORMAT",
            ),
        )

    def test_authorization_minimum_is_configurable_and_safely_bounded(self):
        with patch.dict(os.environ, {"SIGEH_AUTHORIZATION_MIN_DIGITS": "7"}):
            self.assertEqual(app.authorization_min_digits(), 7)
        with patch.dict(os.environ, {"SIGEH_AUTHORIZATION_MIN_DIGITS": "invalid"}):
            self.assertEqual(app.authorization_min_digits(), 6)

    def test_bypass_review_migration_is_idempotent_and_non_destructive(self):
        sql = (
            Path(__file__).parents[1]
            / "migrations"
            / "20260830_billing_bypass_authorization_review.sql"
        ).read_text(encoding="utf-8")
        normalized = sql.upper()
        self.assertIn("ADD COLUMN IF NOT EXISTS REVIEW_STATUS", normalized)
        self.assertIn("ADD COLUMN IF NOT EXISTS REVIEW_REASON", normalized)
        self.assertIn("ESTADO_DOCUMENTO='LISTO_AUDITORIA'", normalized)
        self.assertIn("VERIFICATION_BYPASSED", normalized)
        self.assertNotIn("DELETE FROM", normalized)
        self.assertNotIn("DROP TABLE", normalized)
        self.assertNotIn("TRUNCATE", normalized)
        build_spec = (Path(__file__).parents[1] / "build_app.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "20260830_billing_bypass_authorization_review.sql", build_spec
        )

    def test_bypass_review_migration_loader_supports_source_and_frozen_paths(self):
        class Connection:
            def __init__(self):
                self.executed = []
                self.scripts = []

            def execute(self, *args):
                self.executed.append(args)

            def executescript(self, sql):
                self.scripts.append(sql)

        source_connection = Connection()
        with patch.object(app.sys, "frozen", False, create=True):
            app._apply_billing_bypass_authorization_review_migration(
                source_connection
            )
        self.assertIn("ADD COLUMN IF NOT EXISTS review_status", source_connection.scripts[0])

        frozen_connection = Connection()
        with (
            patch.object(app.sys, "frozen", True, create=True),
            patch.object(app, "BUNDLE_DIR", str(Path(__file__).parents[1])),
        ):
            app._apply_billing_bypass_authorization_review_migration(
                frozen_connection
            )
        self.assertEqual(source_connection.scripts, frozen_connection.scripts)

        with (
            patch.object(app.sys, "frozen", True, create=True),
            patch.object(app, "BUNDLE_DIR", str(Path(__file__).parent)),
            self.assertRaises(RuntimeError),
        ):
            app._apply_billing_bypass_authorization_review_migration(Connection())

    def test_claim_worker_performs_one_reservation_call(self):
        result = object()
        completed = []
        with patch.object(app, "claim_projected_billable_attention", return_value=result) as claim:
            worker = app.AdmissionValidationClaimWorker(
                7,
                "CENTRAL",
                username="tester",
                session_id="session",
                current_user={"username": "tester"},
            )
            worker.completed.connect(lambda value, elapsed: completed.append((value, elapsed)))
            worker.run()
        claim.assert_called_once()
        self.assertIs(completed[0][0], result)
        self.assertGreaterEqual(completed[0][1], 0.0)

    def test_button_flow_no_longer_requeries_receipt_after_claim(self):
        source = inspect.getsource(app.MainWindow._on_admission_claim_completed)
        source += inspect.getsource(app.MainWindow._complete_verified_admission)
        self.assertNotIn("get_receipt_for_admission_attention", source)

    def test_main_navigation_expands_at_supported_logical_widths(self):
        for width in (910, 1024, 1366, 1600, 1920):
            self.assertTrue(should_expand_main_module_tabs(width), width)
        self.assertFalse(should_expand_main_module_tabs(719))


if __name__ == "__main__":
    unittest.main()
