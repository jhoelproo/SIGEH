import unittest
import os
import tempfile
from unittest.mock import patch

import CALCULOS_QT as app


class Cursor:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, receipt):
        self.receipt = receipt
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.calls.append((" ".join(sql.split()), params))
        if "FROM recibos WHERE id=%s FOR UPDATE" in sql:
            return Cursor(self.receipt)
        return Cursor()


class BillingTransitionTests(unittest.TestCase):
    def test_audit_assignment_filters_distinguish_my_queue(self):
        self.assertTrue(app.audit_assignment_matches(
            "auditoria_prueba", app.AUDIT_ASSIGNMENT_MINE, "auditoria_prueba"
        ))
        self.assertFalse(app.audit_assignment_matches(
            "otro_auditor", app.AUDIT_ASSIGNMENT_MINE, "auditoria_prueba"
        ))
        self.assertTrue(app.audit_assignment_matches(
            "", app.AUDIT_ASSIGNMENT_UNASSIGNED, "auditoria_prueba"
        ))
        self.assertTrue(app.audit_assignment_matches(
            "otro_auditor", app.AUDIT_ASSIGNMENT_OTHERS, "auditoria_prueba"
        ))

    def test_database_url_accepts_remote_postgresql(self):
        value = "postgresql://user:secret@db.example.com:5432/hospital"
        self.assertEqual(app.validate_database_url(value), value)

    def test_database_url_accepts_local_postgresql(self):
        value = "postgresql://user:secret@127.0.0.1:5432/hospital"
        self.assertEqual(app.validate_database_url(value), value)

    def test_database_url_missing_has_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL no está configurada"):
            app.validate_database_url("")

    def test_database_url_invalid_has_clear_error(self):
        with self.assertRaisesRegex(RuntimeError, "DATABASE_URL no es válida"):
            app.validate_database_url("https://example.com/not-postgres")

    def test_database_url_environment_overrides_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = os.path.join(directory, ".env")
            with open(env_path, "w", encoding="utf-8") as env_file:
                env_file.write(
                    "DATABASE_URL=postgresql://file:secret@localhost:5432/from_file\n"
                )
            process_value = "postgresql://process:secret@localhost:5432/from_process"
            with patch.dict(app.os.environ, {"DATABASE_URL": process_value}, clear=False):
                self.assertEqual(app.load_database_url(directory), process_value)

    def test_pool_uses_remote_database_url_without_host_filter(self):
        value = "postgresql://user:secret@db.example.com:5432/hospital"
        fake_pool = object()
        with patch.object(app, "DB_URL", value), patch.object(app, "db_pool", None), patch.object(
            app.pool, "ThreadedConnectionPool", return_value=fake_pool
        ) as create_pool:
            app.init_pool()
        create_pool.assert_called_once_with(
            1,
            20,
            value,
            connect_timeout=8,
            options=(
                "-c statement_timeout=15000 -c lock_timeout=3000 "
                "-c idle_in_transaction_session_timeout=15000"
            ),
            application_name="hospital_main_app",
        )

    def test_bad_credentials_error_does_not_expose_password(self):
        message = app.database_connection_error_message(
            RuntimeError("password authentication failed for user billing")
        )
        self.assertIn("rechazó las credenciales", message)
        self.assertNotIn("secret", message)

    def test_schema_prepares_history_states_role_and_indexes(self):
        schema = app.SCHEMA
        self.assertIn("estado_facturacion TEXT NOT NULL DEFAULT 'SIN_CLASIFICAR'", schema)
        self.assertIn("recibo_facturacion_history", schema)
        self.assertIn(
            "idx_recibos_billing_status_date",
            app.POST_MIGRATION_INDEXES,
        )
        self.assertIn("auditoria medica y cuentas", schema)
        self.assertIn("auditoria_checklist_json", schema)
        self.assertIn("evento_tipo", schema)
        self.assertIn("numero_autorizacion", schema)
        self.assertIn("estado_documento", schema)

    def receipt(self, status=app.BILLING_PENDING, pdf_synced=1, total=2500):
        return {
            "id": 7, "numero": 10452, "ars": "SENASA", "total": total,
            "estado_facturacion": status, "revision_version": 0,
            "pdf_synced": pdf_synced, "is_deleted": 0, "is_backdated": 0,
            "auditoria_asignada_a": None,
            "numero_autorizacion": "AUT-001",
            "estado_documento": app.DOCUMENT_READY,
            "tipo_cobertura": "ASEGURADO",
        }

    def run_change(self, receipt, target, **kwargs):
        unlinked = bool(kwargs.pop("_unlinked", False))
        connection = FakeConnection(receipt)
        actor = {"username": "admin", "role": app.ROLE_ADMIN}
        blockers = []
        if target == app.BILLING_INVOICED and not receipt.get("pdf_synced"):
            blockers.append("El PDF todavía no está sincronizado.")
        if target == app.BILLING_INVOICED and float(receipt.get("total") or 0) <= 0:
            blockers.append("El total del recibo debe ser mayor que cero.")
        preflight = {
            "blockers": blockers,
            "warnings": [],
            "risk_score": 20 if blockers else 0,
            "ready": not blockers,
            # These legacy transition tests exercise the ordinary linked
            # receipt path.  Unlinked confirmation has its own privileged
            # reason/audit contract.
            "admission_attention_id": None if unlinked else 9001,
        }
        if target == app.BILLING_INVOICED:
            kwargs.setdefault("checklist", {key: True for key in app.AUDIT_CHECKLIST_ITEMS})
        with patch.object(app, "db_connect", return_value=connection), patch.object(
            app, "get_recibo_data", return_value={"id": 7, "estado_facturacion": target}
        ), patch.object(
            app, "_audit_preflight_from_connection", return_value=preflight
        ), patch.object(
            app,
            "get_projected_billable_attention",
            return_value={"attention_id": 9001, "status": "ACTIVA"},
        ), patch.object(
            app,
            "save_receipt_document_snapshot",
            return_value={"version": 2},
        ):
            result = app.change_receipt_billing_status(7, target, actor, **kwargs)
        return connection, result

    def test_pending_can_be_confirmed_and_writes_both_histories(self):
        connection, result = self.run_change(
            self.receipt(), app.BILLING_INVOICED, reference="LOTE-001"
        )
        sql = "\n".join(call[0] for call in connection.calls)
        self.assertIn("UPDATE recibos SET estado_facturacion=%s", sql)
        self.assertIn("INSERT INTO recibo_facturacion_history", sql)
        self.assertIn("INSERT INTO action_history", sql)
        self.assertEqual(result["estado_facturacion"], app.BILLING_INVOICED)

    def test_admin_bulk_validation_reuses_individual_transition_and_continues(self):
        calls = []

        def validate(receipt_id, status, actor, **kwargs):
            calls.append((receipt_id, status, actor, kwargs))
            if receipt_id == 2:
                raise ValueError("El recibo ya está en estado Facturado.")
            if receipt_id == 3:
                raise ValueError("El recibo no está listo para facturarse")
            return {"id": receipt_id, "estado_facturacion": status}

        with patch.object(
            app, "change_receipt_billing_status", side_effect=validate
        ), patch.object(app, "log_action") as audit:
            result = app.validate_receipts_as_invoiced_bulk(
                [1, 2, 3, 4, 4],
                {"username": "admin", "role": app.ROLE_ADMIN},
            )

        self.assertEqual(result["requested"], 4)
        self.assertEqual(result["completed_ids"], [1, 4])
        self.assertEqual(result["already_invoiced_ids"], [2])
        self.assertEqual(result["failed"], 1)
        self.assertTrue(all(call[1] == app.BILLING_INVOICED for call in calls))
        self.assertTrue(all(all(call[3]["checklist"].values()) for call in calls))
        audit.assert_called_once()

    def test_bulk_validation_is_admin_only(self):
        with self.assertRaisesRegex(PermissionError, "Administrador"):
            app.validate_receipts_as_invoiced_bulk(
                [1], {"username": "auditor", "role": app.ROLE_AUDIT}
            )

    def test_confirmation_requires_complete_audit_checklist(self):
        with self.assertRaisesRegex(ValueError, "lista de verificación"):
            self.run_change(
                self.receipt(), app.BILLING_INVOICED,
                checklist={key: False for key in app.AUDIT_CHECKLIST_ITEMS},
            )

    def test_privileged_unlinked_confirmation_requires_reason_and_is_audited(self):
        with self.assertRaisesRegex(ValueError, "motivo adicional"):
            self.run_change(
                self.receipt(), app.BILLING_INVOICED,
                reference="LOTE-UNLINKED", _unlinked=True,
            )

        connection, result = self.run_change(
            self.receipt(), app.BILLING_INVOICED,
            reference="LOTE-UNLINKED", _unlinked=True,
            reason="Confirmación administrativa autorizada",
        )
        self.assertEqual(result["estado_facturacion"], app.BILLING_INVOICED)
        actions = [
            params for statement, params in connection.calls
            if "INSERT INTO action_history" in statement
        ]
        self.assertTrue(any(
            "UNLINKED_RECEIPT_CONFIRMED_BILLED" in params
            for params in actions
        ))

    def test_not_invoiced_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "motivo"):
            self.run_change(self.receipt(), app.BILLING_NOT_INVOICED)

    def test_invoiced_cannot_change_directly_to_not_invoiced(self):
        with self.assertRaisesRegex(ValueError, "No se permite"):
            self.run_change(
                self.receipt(status=app.BILLING_INVOICED),
                app.BILLING_NOT_INVOICED,
                reason="Corrección",
            )

    def test_reopening_invalidates_pdf_and_records_financial_reversal(self):
        connection, result = self.run_change(
            self.receipt(status=app.BILLING_INVOICED),
            app.BILLING_PENDING,
            reason="Corrección de monto",
        )
        sql = "\n".join(call[0] for call in connection.calls)
        self.assertIn(
            "pdf_synced=CASE WHEN %s='PENDIENTE' "
            "AND document_storage_mode='LEGACY_PDF' THEN 0",
            sql,
        )
        history_calls = [
            params for statement, params in connection.calls
            if "INSERT INTO recibo_facturacion_history" in statement
        ]
        self.assertEqual(history_calls[-1][8], "REAPERTURA_FINANCIERA")
        self.assertEqual(result["estado_facturacion"], app.BILLING_PENDING)

    def test_confirmation_requires_synced_pdf_and_positive_total(self):
        with self.assertRaisesRegex(ValueError, "sincronizado"):
            self.run_change(
                self.receipt(pdf_synced=0), app.BILLING_INVOICED
            )
        with self.assertRaisesRegex(ValueError, "total"):
            self.run_change(
                self.receipt(total=0), app.BILLING_INVOICED
            )


if __name__ == "__main__":
    unittest.main()
