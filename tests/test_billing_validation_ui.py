import inspect
import os
import unittest
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
