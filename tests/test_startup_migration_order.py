import inspect
import unittest

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


if __name__ == "__main__":
    unittest.main()
