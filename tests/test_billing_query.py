import unittest

from report_engine.query import (
    ANALYSIS_CONFIRMED,
    ANALYSIS_HISTORICAL,
    ANALYSIS_NOT_INVOICED,
    ANALYSIS_PENDING,
    ANALYSIS_PRODUCTION,
    medication_ars_sql_exclusion,
    receipt_scope,
)
from report_engine.data_service import PanelDataService


class BillingQueryTests(unittest.TestCase):
    def test_medication_ars_exclusion_is_safe_for_psycopg_parameters(self):
        clause = medication_ars_sql_exclusion("r")
        self.assertNotIn("%", clause)
        self.assertIn("!~ '^SENASASUB'", clause)

    def test_analysis_scopes_use_the_correct_status_and_date(self):
        cases = {
            ANALYSIS_CONFIRMED: ("estado_facturacion_at", "FACTURADO"),
            ANALYSIS_PENDING: ("created_at", "PENDIENTE"),
            ANALYSIS_NOT_INVOICED: ("estado_facturacion_at", "NO_FACTURADO"),
            ANALYSIS_HISTORICAL: ("created_at", "SIN_CLASIFICAR"),
        }
        for analysis, (date_column, status) in cases.items():
            clauses, params, expression, definition = receipt_scope(
                "r", "2026-07-01", "2026-07-31", analysis
            )
            sql = " AND ".join(clauses)
            self.assertIn(date_column, expression)
            self.assertIn("r.is_deleted=0", sql)
            self.assertIn("estado_facturacion = ANY(%s)", sql)
            self.assertEqual(params[-1], [status])
            self.assertEqual(definition["key"], analysis)


    def test_production_scope_includes_every_non_deleted_status(self):
        clauses, params, expression, definition = receipt_scope(
            "r", "2026-07-01", "2026-07-31", ANALYSIS_PRODUCTION
        )
        sql = " AND ".join(clauses)
        self.assertIn("created_at", expression)
        self.assertNotIn("estado_facturacion = ANY", sql)
        self.assertEqual(params, ["2026-07-01", "2026-07-31", "EMERGENCIA"])
        self.assertEqual(definition["statuses"], ())


    def test_panel_filters_keep_include_exclude_and_financial_scope_together(self):
        where, params, date_expr, definition = PanelDataService._receipt_where(
            "2026-07-01", "2026-07-31",
            {"mode": "exclude", "values": ["ARS A", "ARS B"]},
            {"mode": "include", "values": ["usuario1", "usuario2"]},
            "Todos los medicamentos", "Todas las categorías", "Todas",
            ANALYSIS_CONFIRMED,
        )
        self.assertIn("NOT (COALESCE(r.ars, '') = ANY(%s))", where)
        self.assertIn("COALESCE(r.username, '') = ANY(%s)", where)
        self.assertIn("r.estado_facturacion = ANY(%s)", where)
        self.assertEqual(params[-2:], [["ARS A", "ARS B"], ["usuario1", "usuario2"]])
        self.assertIn("estado_facturacion_at", date_expr)
        self.assertEqual(definition["key"], ANALYSIS_CONFIRMED)

    def test_ars_comparison_rows_include_both_percentages(self):
        rows = [
            {"label": "ARS A", "receipts": 3, "total": 300.0},
            {"label": "ARS B", "receipts": 1, "total": 100.0},
        ]
        ars_total = sum(row["total"] for row in rows)
        receipt_total = sum(row["receipts"] for row in rows)
        comparison = [
            {
                **row,
                "average": row["total"] / row["receipts"],
                "money_percentage": row["total"] / ars_total,
                "receipt_percentage": row["receipts"] / receipt_total,
            }
            for row in rows
        ]
        self.assertEqual(comparison[0]["money_percentage"], 0.75)
        self.assertEqual(comparison[0]["receipt_percentage"], 0.75)


if __name__ == "__main__":
    unittest.main()
