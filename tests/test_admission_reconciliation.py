import unittest
from unittest.mock import patch

import CALCULOS_QT as app
from admission_bridge import AdmissionAttention


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        return Cursor(self.rows)


class FakeRepository:
    def __init__(self, attentions):
        self.attentions = attentions

    def list_eligible(self, start, end, limit=10000):
        return list(self.attentions)


def attention(attention_id, name, ars, uninsured=False):
    return AdmissionAttention(
        attention_id=attention_id,
        patient_id=attention_id + 100,
        name=name,
        service_date="2026-07-17",
        service_time="10:00",
        nss="123",
        nss_clean="123",
        cedula="001",
        cedula_clean="001",
        ars=ars,
        attention_type="EMERGENCIA",
        source_updated_at="2026-07-17 10:00:00",
        uninsured=uninsured,
        source_instance_id="source-test",
        coverage_status=(
            "SIN_SEGURO_DECLARADO" if uninsured else "ASEGURADO_VALIDADO"
        ),
        canonical_ars=("SIN SEGURO" if uninsured else ars),
        billing_readiness="LISTA",
        snapshot_hash=str(attention_id) * 64,
    )


class AdmissionReconciliationTests(unittest.TestCase):
    def test_reconciliation_is_auxiliary_and_groups_by_billing_user(self):
        repository = FakeRepository(
            [
                attention(1, "PACIENTE UNO", "SENASA"),
                attention(2, "PACIENTE DOS", "HUMANO"),
                attention(3, "PACIENTE TRES", "SIN SEGURO", True),
            ]
        )
        rows = [
            {
                "id": 10,
                "numero": 100,
                "nombre": "PACIENTE UNO",
                "fecha": "2026-07-17",
                "username": "facturador_a",
                "created_at": "2026-07-17 10:30:00",
                "ars": "SENASA",
                "tipo_cobertura": "ASEGURADO",
                "total": 2000,
                "estado_facturacion": app.BILLING_INVOICED,
                "estado_facturacion_at": "2026-07-17 11:00:00",
                "estado_documento": app.DOCUMENT_READY,
                "admission_atencion_id": 1,
                "admission_source_instance_id": "source-test",
            },
            {
                "id": 11,
                "numero": 101,
                "nombre": "PACIENTE CAMBIADO",
                "fecha": "2026-07-18",
                "username": "facturador_a",
                "created_at": "2026-07-17 10:35:00",
                "ars": "OTRA ARS",
                "tipo_cobertura": "ASEGURADO",
                "total": 500,
                "estado_facturacion": app.BILLING_PENDING,
                "estado_facturacion_at": None,
                "estado_documento": app.DOCUMENT_PRELIMINARY,
                "admission_atencion_id": 2,
                "admission_source_instance_id": "source-test",
            },
        ]

        with patch.object(
            app,
            "db_connect",
            return_value=FakeConnection(rows),
        ), patch.object(app, "sync_admission_projection", return_value=3):
            result = app.load_admission_reconciliation(
                "2026-07-17",
                "2026-07-17",
                repository=repository,
            )

        self.assertEqual(
            result["authoritative_report"],
            "REPORTE_GENERAL_DE_MEDICAMENTOS",
        )
        self.assertEqual(result["report_scope"], "ESTADISTICA_DE_CONCILIACION")
        self.assertEqual(result["summary"]["admissions"], 3)
        self.assertEqual(result["summary"]["linked"], 2)
        self.assertEqual(result["summary"]["missing"], 1)
        self.assertEqual(result["summary"]["invoiced"], 1)
        self.assertEqual(result["summary"]["pending"], 1)
        self.assertEqual(result["summary"]["discrepancies"], 1)
        self.assertEqual(result["summary"]["ready"], 3)
        self.assertEqual(result["daily"][0]["invoiced_total"], 2000)
        self.assertEqual(result["by_user"][0]["username"], "facturador_a")
        self.assertEqual(result["by_user"][0]["receipts"], 2)
        self.assertEqual(
            result["records"][1]["differences"],
            ["Nombre", "Fecha", "ARS"],
        )
        self.assertEqual(result["records"][2]["validation_state"], "Sin recibo")

    def test_reconciliation_rejects_ranges_over_63_days(self):
        with self.assertRaisesRegex(ValueError, "hasta 63 días"):
            app.load_admission_reconciliation(
                "2026-01-01",
                "2026-04-01",
                repository=FakeRepository([]),
            )


if __name__ == "__main__":
    unittest.main()
