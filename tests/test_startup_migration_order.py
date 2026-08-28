import inspect
import unittest

import admission_hybrid
import CALCULOS_QT as app


class StartupMigrationOrderTests(unittest.TestCase):
    def test_legacy_billing_indexes_run_after_columns_are_added(self):
        self.assertNotIn("idx_recibos_billing_status", app.SCHEMA)
        self.assertNotIn("idx_recibos_audit_queue", app.SCHEMA)
        self.assertIn(
            "idx_recibos_billing_status",
            app.POST_MIGRATION_INDEXES,
        )
        self.assertIn(
            "idx_recibos_audit_queue",
            app.POST_MIGRATION_INDEXES,
        )

        source = inspect.getsource(app.db_init)
        add_columns_position = source.index(
            "for column_name, column_definition in billing_columns.items()"
        )
        create_indexes_position = source.index(
            "con.executescript(POST_MIGRATION_INDEXES)"
        )
        self.assertLess(add_columns_position, create_indexes_position)

    def test_admission_projection_index_runs_after_hybrid_columns_are_added(self):
        index_name = "idx_admission_projection_operational_turn"

        self.assertNotIn(index_name, app.SCHEMA)
        hybrid_schema = admission_hybrid.POSTGRES_HYBRID_SCHEMA
        create_index_position = hybrid_schema.index(index_name)
        for column_definition in (
            "turn_id BIGINT",
            "service_time TEXT",
            "service_type TEXT NOT NULL DEFAULT 'EMERGENCIA'",
            "specialty TEXT",
            "admission_username TEXT",
            "authorization_snapshot TEXT",
            "source_status TEXT NOT NULL DEFAULT 'ACTIVA'",
            "has_detail_sheet BOOLEAN NOT NULL DEFAULT FALSE",
            "operational_source_id UUID",
        ):
            add_column_position = hybrid_schema.index(
                "ALTER TABLE admission_attention_projection "
                f"ADD COLUMN IF NOT EXISTS {column_definition}"
            )
            self.assertLess(add_column_position, create_index_position)

    def test_billing_batch_constraint_accepts_pending_before_legacy_rows_migrate(self):
        source = inspect.getsource(app.db_init)
        constraint_position = source.index(
            "ALTER TABLE billing_batches ADD CONSTRAINT billing_batches_status_check"
        )
        legacy_update_position = source.index(
            "UPDATE billing_batches SET status='PENDIENTE' WHERE status='BORRADOR'"
        )

        self.assertLess(constraint_position, legacy_update_position)


if __name__ == "__main__":
    unittest.main()
