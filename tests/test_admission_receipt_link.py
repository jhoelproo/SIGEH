import unittest
from pathlib import Path
from unittest.mock import patch

import CALCULOS_QT as app


class Cursor:
    def __init__(self, row=None, rowcount=1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


class StrictConnection:
    def __init__(self):
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(sql.split())
        params = tuple(params or ())
        self.calls.append((compact, params))
        self.assert_placeholder_count(compact, params)
        if compact.startswith("INSERT INTO recibos("):
            return Cursor((77,))
        return Cursor()

    @staticmethod
    def assert_placeholder_count(sql, params):
        expected = sql.count("%s")
        if expected != len(params):
            raise AssertionError(
                f"SQL esperaba {expected} parámetros y recibió {len(params)}"
            )


class SequentialConnection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        params = tuple(params or ())
        StrictConnection.assert_placeholder_count(compact, params)
        self.calls.append((compact, params))
        row = self.rows.pop(0) if self.rows else None
        return Cursor(row)


class AdmissionReceiptLinkTests(unittest.TestCase):
    def attention(self):
        return {
            "attention_id": 901,
            "patient_id": 81,
            "name": "PACIENTE PRUEBA",
            "service_date": "2026-07-17",
            "nss_clean": "123456789",
            "cedula_clean": "00100000011",
            "ars": "SENASA CONTRIBUTIVO",
            "source_updated_at": "2026-07-17 10:00:00",
            "source_instance_id": "source-e2e",
            "global_attention_id": "11111111-1111-4111-8111-111111111111",
            "snapshot_hash": "a" * 64,
            "coverage_status": "ASEGURADO_VALIDADO",
            "canonical_ars": "SENASA CONTRIBUTIVO",
            "billing_readiness": "LISTA",
            "readiness_reasons": (),
            "uninsured": False,
        }

    def test_new_receipt_persists_admission_snapshot_atomically(self):
        connection = StrictConnection()
        processing = {
            "turno_origen_id": 10,
            "turno_procesamiento_id": 11,
            "herencia_estado": "HEREDADA_PROCESADA",
            "already_linked": False,
        }
        with patch.object(app, "db_connect", return_value=connection), patch.object(
            app,
            "_lock_and_validate_admission_processing",
            return_value=processing,
        ), patch.object(
            app,
            "save_receipt_document_snapshot",
            return_value={"version": 1},
        ) as save_snapshot:
            receipt_id = app.save_receipt_with_items(
                None,
                55,
                "PACIENTE PRUEBA",
                "2026-07-17",
                "DX",
                "SENASA CONTRIBUTIVO",
                500,
                900,
                "recibo_55.pdf",
                "facturador",
                0,
                "2026-07-17 10:05:00",
                [],
                "ASEGURADO",
                "AUT-1",
                self.attention(),
                "session-test",
            )

        self.assertEqual(receipt_id, 77)
        save_snapshot.assert_called_once_with(
            connection,
            77,
            "facturador",
            document_context={},
            target_storage_mode="SNAPSHOT",
        )
        receipt_insert = next(
            call for call in connection.calls if call[0].startswith("INSERT INTO recibos(")
        )
        self.assertIn("admission_atencion_id", receipt_insert[0])
        self.assertIn("admission_source_instance_id", receipt_insert[0])
        self.assertIn("turno_origen_id", receipt_insert[0])
        self.assertIn("turno_procesamiento_id", receipt_insert[0])
        self.assertIn(901, receipt_insert[1])
        self.assertIn("source-e2e", receipt_insert[1])
        self.assertIn("123456789", receipt_insert[1])
        source_position = receipt_insert[1].index("source-e2e")
        self.assertEqual(
            receipt_insert[1][source_position:source_position + 5],
            (
                "source-e2e",
                "a" * 64,
                "11111111-1111-4111-8111-111111111111",
                "ASEGURADO_VALIDADO",
                "LISTA",
            ),
        )

    def test_schema_has_unique_attention_link_and_monthly_snapshots(self):
        migration = (
            Path(__file__).parents[1]
            / "migrations"
            / "20260717_admission_monthly_lists.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("uq_recibos_admission_attention_active", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS billing_batches", app.SCHEMA)
        self.assertIn("billing_date_snapshot TEXT NOT NULL", app.SCHEMA)
        self.assertIn("comprobante_snapshot TEXT", app.SCHEMA)
        self.assertIn("billing_batch_events", app.SCHEMA)
        self.assertIn("admission_attention_projection", app.SCHEMA)
        self.assertIn("admission_source_instance_id", app.SCHEMA)

    def test_month_bounds_are_calendar_months(self):
        self.assertEqual(
            app._billing_month_bounds(2026, 7),
            ("2026-07-01 00:00:00", "2026-08-01 00:00:00"),
        )
        self.assertEqual(
            app._billing_month_bounds(2026, 12),
            ("2026-12-01 00:00:00", "2027-01-01 00:00:00"),
        )

    def test_final_save_validates_pc_source_against_shared_operational_source(self):
        connection = SequentialConnection(
            [
                None,
                None,
                {
                    "turno_origen_id": 3942,
                    "turno_procesamiento_id": 3942,
                    "is_inherited": False,
                },
                {"session_id": "login-session", "expires_at": "future"},
            ]
        )
        attention = {
            "attention_id": 1,
            "source_instance_id": "PC-PRIVATE-SOURCE",
        }
        central_shift = {
            "operational_source_id": "SHARED-OPERATIONAL-SOURCE",
            "source_instance_id": "SHARED-OPERATIONAL-SOURCE",
            "turn_id": 3942,
        }

        with patch.object(
            app.BillingAdmissionQueryService,
            "current_shift",
            return_value=central_shift,
        ):
            result = app._lock_and_validate_admission_processing(
                connection,
                attention,
                session_id="login-session",
            )

        eligible_sql, eligible_params = next(
            call
            for call in connection.calls
            if "FROM admission_attention_projection p" in call[0]
        )
        self.assertIn(
            "p.operational_source_id::TEXT=cs.operational_source_id",
            eligible_sql,
        )
        self.assertNotIn("cs.source_instance_id=p.source_instance_id", eligible_sql)
        self.assertEqual(eligible_params[0], "SHARED-OPERATIONAL-SOURCE")
        self.assertEqual(result["turno_origen_id"], 3942)
        self.assertEqual(result["turno_procesamiento_id"], 3942)


if __name__ == "__main__":
    unittest.main()
