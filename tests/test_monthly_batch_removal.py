import json
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg2
from psycopg2.extras import RealDictCursor

import CALCULOS_QT as app
from monthly_batch_removal import remove_batch_entries


def entry(identifier, receipt_id=None, source="SRC", attention=9):
    return {
        "id": identifier,
        "recibo_id": receipt_id,
        "admission_source_instance_id": source,
        "admission_attention_id": attention,
    }


class MonthlyBatchRemovalTests(unittest.TestCase):
    def remove(self, connection, entries, batch_id=1, reason="Duplicado"):
        return remove_batch_entries(
            connection,
            batch_id,
            entries,
            username="admin",
            stamp="2026-10-04 12:00:00",
            reason=reason,
            is_editable=lambda status: status == "DRAFT",
        )

    def connection(self, records, rowcount=None, status="DRAFT"):
        connection = MagicMock()
        connection.execute.side_effect = [
            SimpleNamespace(fetchone=lambda: {"status": status}),
            *[SimpleNamespace(fetchone=lambda row=row: row) for row in records],
            SimpleNamespace(rowcount=len(records) if rowcount is None else rowcount),
            *[None for _row in records],
            None,
        ]
        return connection

    def test_mixed_selection_deduplicates_and_records_each_removal(self):
        connection = self.connection([entry(10, 7), entry(11)])
        targets = [
            {"recibo_id": 7},
            {"admission_source_instance_id": "SRC", "admission_attention_id": 9},
            {"recibo_id": 7},
        ]
        self.assertEqual(self.remove(connection, targets, reason="  Duplicado  "), 2)
        calls = connection.execute.call_args_list
        self.assertEqual(calls[3].args[1][-1], [10, 11])
        self.assertEqual(calls[4].args[1][2], "RECIBO_RETIRADO")
        self.assertEqual(calls[4].args[1][-1], "Duplicado")
        self.assertEqual(calls[5].args[1][2], "ADMISION_RETIRADA")
        self.assertEqual(json.loads(calls[5].args[1][-1])["attention_id"], 9)
        for call in calls:
            self.assertNotIn("DELETE", call.args[0])
            self.assertNotIn("UPDATE recibos", call.args[0])

    def test_aliases_of_same_row_are_removed_and_audited_once(self):
        connection = self.connection([entry(10, 7), entry(10, 7)], rowcount=1)
        self.assertEqual(
            self.remove(connection, [{"recibo_id": 7}, {"admission_attention_id": 9}]),
            1,
        )
        self.assertEqual(connection.execute.call_count, 6)

    def test_source_is_parameterized_and_legacy_source_is_supported(self):
        source = "SRC' OR 1=1 --"
        for supplied, expected in ((source, source), (None, "LEGACY")):
            with self.subTest(source=supplied):
                connection = self.connection([entry(1)])
                self.remove(
                    connection,
                    [
                        {
                            "admission_source_instance_id": supplied,
                            "admission_attention_id": 9,
                        }
                    ],
                )
                sql, values = connection.execute.call_args_list[1].args
                self.assertNotIn(source, sql)
                self.assertEqual(values, (1, None, expected, 9))

    def test_invalid_ids_empty_reason_and_empty_selection_do_not_query(self):
        for invalid in (0, -1, None, "", "bad", True, 1.5, float("inf")):
            with self.subTest(invalid=invalid):
                connection = MagicMock()
                with self.assertRaises(ValueError):
                    self.remove(connection, [{"recibo_id": invalid}])
                connection.execute.assert_not_called()
        for entries, batch_id, reason in (
            ([], 1, "x"),
            ([{"recibo_id": 1}], 0, "x"),
            ([{"recibo_id": 1}], 1, "  "),
            (
                [{"admission_attention_id": 1, "admission_source_instance_id": " "}],
                1,
                "x",
            ),
        ):
            with self.subTest(entries=entries, batch_id=batch_id, reason=reason):
                connection = MagicMock()
                with self.assertRaises(ValueError):
                    self.remove(connection, entries, batch_id=batch_id, reason=reason)
                connection.execute.assert_not_called()

    def test_missing_or_closed_batch_is_rejected(self):
        for batch in (None, {"status": "CLOSED"}):
            connection = MagicMock()
            connection.execute.return_value.fetchone.return_value = batch
            with self.assertRaisesRegex(ValueError, "edición"):
                self.remove(connection, [{"recibo_id": 1}])
            self.assertEqual(connection.execute.call_count, 1)

    def test_missing_selection_prevents_every_update(self):
        connection = self.connection([entry(10, 7), None])
        with self.assertRaisesRegex(ValueError, "ya no está incluido"):
            self.remove(connection, [{"recibo_id": 7}, {"recibo_id": 8}])
        self.assertEqual(connection.execute.call_count, 3)

    def test_concurrent_change_prevents_audit_and_batch_update(self):
        connection = self.connection([entry(10, 7)], rowcount=0)
        with self.assertRaisesRegex(ValueError, "Cambió la selección"):
            self.remove(connection, [{"recibo_id": 7}])
        self.assertEqual(connection.execute.call_count, 3)

    def test_wrappers_check_permission_and_use_the_existing_transaction(self):
        with patch.object(app, "db_connect") as connect:
            with self.assertRaises(PermissionError):
                app.remove_selected_entries_from_monthly_batch(
                    1, [{"recibo_id": 7}], {}, "x"
                )
            connect.assert_not_called()
        with (
            patch.object(app, "db_connect") as connect,
            patch.object(app, "remove_batch_entries", return_value=1) as remove,
        ):
            user = {"role": app.ROLE_ADMIN}
            app.remove_receipt_from_monthly_batch(1, 7, user, "x")
            self.assertEqual(remove.call_args.args[2], [{"recibo_id": 7}])
            self.assertEqual(remove.call_args.kwargs["username"], "Sistema")
            app.remove_admission_candidate_from_monthly_batch(1, "SRC", 9, user, "x")
            self.assertEqual(remove.call_args.args[2][0]["admission_attention_id"], 9)
            self.assertEqual(connect.return_value.__exit__.call_count, 2)


class SqlConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, values=()):
        cursor = self.connection.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql, values)
        return cursor


class MonthlyBatchRemovalPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_url = os.environ.get(
            "HOSPITAL_E2E_ADMIN_URL",
            "postgresql://preview_admin@127.0.0.1:55432/postgres",
        )
        cls.database_name = "ars_removal_qa_" + uuid.uuid4().hex[:12]
        cls.admin = psycopg2.connect(cls.admin_url)
        cls.admin.autocommit = True
        with cls.admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{cls.database_name}"')
        cls.connection = psycopg2.connect(cls.admin_url, dbname=cls.database_name)
        cls.sql = SqlConnection(cls.connection)
        with cls.connection:
            cls.sql.execute("""
                CREATE TABLE billing_batches(id INT PRIMARY KEY,status TEXT,
                                             updated_at TEXT,updated_by TEXT);
                CREATE TABLE recibos(id INT PRIMARY KEY,patient TEXT,total NUMERIC);
                CREATE TABLE admissions(id INT PRIMARY KEY,patient TEXT);
                CREATE TABLE billing_batch_receipts(
                    id INT PRIMARY KEY,batch_id INT,recibo_id INT,
                    admission_source_instance_id TEXT,admission_attention_id INT,
                    included INT,removed_at TEXT,removed_by TEXT,removal_reason TEXT);
                CREATE TABLE billing_batch_events(
                    batch_id INT,recibo_id INT,event_type TEXT,performed_at TEXT,
                    performed_by TEXT,details TEXT);
            """)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()
        with cls.admin.cursor() as cursor:
            cursor.execute(f'DROP DATABASE "{cls.database_name}"')
        cls.admin.close()

    def setUp(self):
        with self.connection:
            self.sql.execute("""
                TRUNCATE billing_batch_events,billing_batch_receipts,
                         billing_batches,recibos,admissions;
                INSERT INTO billing_batches(id,status) VALUES(1,'DRAFT'),(2,'CLOSED');
                INSERT INTO recibos VALUES(7,'PACIENTE QA',100),(8,'PACIENTE QA 2',200);
                INSERT INTO admissions VALUES(9,'ATENCION QA');
                INSERT INTO billing_batch_receipts(id,batch_id,recibo_id,included)
                    VALUES(10,1,7,1),(11,1,8,1),(13,2,7,1);
                INSERT INTO billing_batch_receipts VALUES(12,1,NULL,'SRC',9,1,NULL,NULL,NULL);
            """)

    def remove(self, entries, batch_id=1):
        with self.connection:
            return remove_batch_entries(
                self.sql,
                batch_id,
                entries,
                username="admin",
                stamp="2026-10-04",
                reason="Retiro QA",
                is_editable=lambda status: status == "DRAFT",
            )

    def test_mixed_bulk_removal_preserves_receipts_admissions_and_other_batches(self):
        self.assertEqual(
            self.remove(
                [
                    {"recibo_id": 7},
                    {
                        "admission_source_instance_id": "SRC",
                        "admission_attention_id": 9,
                    },
                ]
            ),
            2,
        )
        self.assertEqual(
            self.sql.execute(
                "SELECT included FROM billing_batch_receipts ORDER BY id"
            ).fetchall(),
            [{"included": 0}, {"included": 1}, {"included": 0}, {"included": 1}],
        )
        self.assertEqual(
            self.sql.execute("SELECT * FROM recibos ORDER BY id").fetchall(),
            [
                {"id": 7, "patient": "PACIENTE QA", "total": 100},
                {"id": 8, "patient": "PACIENTE QA 2", "total": 200},
            ],
        )
        self.assertEqual(
            self.sql.execute("SELECT * FROM admissions").fetchall(),
            [{"id": 9, "patient": "ATENCION QA"}],
        )
        self.assertEqual(
            self.sql.execute(
                "SELECT count(*) AS n FROM billing_batch_events"
            ).fetchone()["n"],
            2,
        )

    def test_stale_entry_rolls_back_without_partial_removal(self):
        with self.assertRaises(ValueError):
            self.remove([{"recibo_id": 7}, {"recibo_id": 99}])
        self.assertEqual(
            self.sql.execute(
                "SELECT sum(included) AS n FROM billing_batch_receipts"
            ).fetchone()["n"],
            4,
        )
        self.assertEqual(
            self.sql.execute(
                "SELECT count(*) AS n FROM billing_batch_events"
            ).fetchone()["n"],
            0,
        )

    def test_audit_failure_rolls_back_all_updates(self):
        with self.connection:
            self.sql.execute(
                "ALTER TABLE billing_batch_events ADD CHECK(performed_by<>'admin')"
            )
        try:
            with self.assertRaises(psycopg2.IntegrityError):
                self.remove([{"recibo_id": 7}, {"recibo_id": 8}])
            self.assertEqual(
                self.sql.execute(
                    "SELECT sum(included) AS n FROM billing_batch_receipts"
                ).fetchone()["n"],
                4,
            )
        finally:
            with self.connection:
                self.sql.execute(
                    "ALTER TABLE billing_batch_events DROP CONSTRAINT billing_batch_events_performed_by_check"
                )

    def test_closed_batch_is_unchanged(self):
        with self.assertRaises(ValueError):
            self.remove([{"recibo_id": 7}], batch_id=2)
        self.assertEqual(
            self.sql.execute(
                "SELECT included FROM billing_batch_receipts WHERE id=13"
            ).fetchone()["included"],
            1,
        )
