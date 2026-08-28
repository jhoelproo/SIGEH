import os
import unittest
import uuid
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import psycopg2

import CALCULOS_QT as app
from PySide6.QtWidgets import QApplication


class ReceiptTrashAndDeduplicationTests(unittest.TestCase):
    def setUp(self):
        self.admin_url = os.environ.get(
            "HOSPITAL_E2E_ADMIN_URL",
            "postgresql://preview_admin@127.0.0.1:55432/postgres",
        )
        self.database_name = "hospital_receipts_" + uuid.uuid4().hex[:12]
        try:
            admin = psycopg2.connect(self.admin_url)
        except psycopg2.Error as exc:
            self.skipTest(f"PostgreSQL local para pruebas no disponible: {exc}")
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{self.database_name}"')
        admin.close()
        self.database_url = (
            "postgresql://preview_admin@127.0.0.1:55432/" + self.database_name
        )
        self.original_url = app.DB_URL
        self.original_pool = app.db_pool
        app.DB_URL = self.database_url
        app.db_pool = None
        app.db_init()
        self.admin = {"username": "admin.test", "role": app.ROLE_ADMIN}
        self.auditor = {"username": "audit.test", "role": app.ROLE_AUDIT}

    def tearDown(self):
        if app.db_pool is not None:
            app.db_pool.closeall()
        app.db_pool = self.original_pool
        app.DB_URL = self.original_url
        admin = psycopg2.connect(self.admin_url)
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s",
                (self.database_name,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{self.database_name}"')
        admin.close()

    def add_receipt(self, number, name, date="2026-07-21"):
        return app.add_recibo(
            number, name, date, "DX", "FUTURO", 373.75, 373.75,
            f"recibo_{number}.pdf", "aux.test",
        )

    def test_move_restore_and_permanent_delete(self):
        receipt_id = self.add_receipt(910001, "Paciente Papelera")
        app.delete_recibo(receipt_id, self.auditor, "Prueba")
        with app.db_connect() as con:
            self.assertEqual(
                int(con.execute("SELECT is_deleted FROM recibos WHERE id=%s", (receipt_id,)).fetchone()[0]),
                1,
            )
        app.restore_recibo(receipt_id, self.admin)
        app.delete_recibo(receipt_id, self.admin, "Prueba definitiva")
        with self.assertRaises(PermissionError):
            app.permanently_delete_recibo(receipt_id, self.auditor)
        app.permanently_delete_recibo(receipt_id, self.admin)
        with app.db_connect() as con:
            self.assertEqual(
                int(con.execute("SELECT COUNT(*) FROM recibos WHERE id=%s", (receipt_id,)).fetchone()[0]),
                0,
            )
            actions = {
                row[0]
                for row in con.execute(
                    "SELECT action FROM action_history WHERE username IN (%s,%s)",
                    ("admin.test", "audit.test"),
                ).fetchall()
            }
        self.assertIn("Mover recibo a papelera", actions)
        self.assertIn("Restaurar recibo", actions)
        self.assertIn("Eliminar recibo definitivamente", actions)

    def test_pending_and_sent_batch_block_trash(self):
        receipt_id = self.add_receipt(910002, "Paciente Listado")
        with app.db_connect() as con:
            batch_id = con.execute(
                """INSERT INTO billing_batches(
                       period_year,period_month,ars,version,status,cutoff_at,
                       created_at,created_by
                   ) VALUES(2026,7,'FUTURO',1,'PENDIENTE',%s,%s,%s)
                   RETURNING id""",
                (app.now_str(), app.now_str(), "admin.test"),
            ).fetchone()[0]
            con.execute(
                """INSERT INTO billing_batch_receipts(
                       batch_id,recibo_id,included,billing_date_snapshot,
                       total_snapshot,added_at,added_by
                   ) VALUES(%s,%s,1,%s,%s,%s,%s)""",
                (batch_id, receipt_id, "2026-07-21", 373.75, app.now_str(), "admin.test"),
            )
        with self.assertRaisesRegex(ValueError, "listado pendiente"):
            app.delete_recibo(receipt_id, self.admin)
        with app.db_connect() as con:
            con.execute(
                """UPDATE billing_batches
                   SET status='ENVIADO',sent_receipt_count=1,sent_total=373.75,
                       sent_invoice_number='',sent_ncf='',sent_ars=ars,
                       sent_period_year=period_year,sent_period_month=period_month
                   WHERE id=%s""",
                (batch_id,),
            )
        with self.assertRaisesRegex(ValueError, "listado enviado"):
            app.delete_recibo(receipt_id, self.admin)

    def test_create_edit_and_restore_block_duplicates(self):
        first_id = self.add_receipt(910003, "  María   Pérez  ")
        with app.db_connect() as con:
            stored_key = con.execute(
                "SELECT dedup_key FROM recibos WHERE id=%s", (first_id,)
            ).fetchone()[0]
        self.assertEqual(stored_key, app.build_receipt_dedup_key("MARÍA PÉREZ"))
        with self.assertRaises(app.DuplicateReceiptError):
            self.add_receipt(910004, "MARÍA PÉREZ")

        app.delete_recibo(first_id, self.admin)
        active_id = self.add_receipt(910004, "MARÍA PÉREZ")
        with self.assertRaises(app.DuplicateReceiptError):
            app.restore_recibo(first_id, self.admin)

        other_id = self.add_receipt(910005, "OTRO PACIENTE", "2026-07-20")
        with self.assertRaises(app.DuplicateReceiptError):
            app.update_recibo_db(
                other_id, "MARÍA PÉREZ", "2026-07-21", "DX", "FUTURO",
                373.75, 373.75, "recibo_910005.pdf", "admin.test",
            )
        with app.db_connect() as con:
            self.assertEqual(
                int(con.execute("SELECT COUNT(*) FROM recibos WHERE id=%s AND is_deleted=0", (active_id,)).fetchone()[0]),
                1,
            )
            self.assertGreaterEqual(
                int(con.execute("SELECT COUNT(*) FROM action_history WHERE action=%s", ("Intento bloqueado por duplicidad",)).fetchone()[0]),
                3,
            )

    def test_migration_is_idempotent_and_preserves_historical_duplicates(self):
        with app.db_connect() as con:
            app._apply_receipt_deduplication_migration(con)
            app._apply_receipt_deduplication_migration(con)
            con.execute("DROP INDEX IF EXISTS uq_recibos_active_patient_service_date")
            for number in (910006, 910007):
                con.execute(
                    """INSERT INTO recibos(
                           numero,nombre,fecha,estado_facturacion,created_at,is_deleted
                       ) VALUES(%s,%s,%s,'PENDIENTE',%s,0)""",
                    (number, "DUPLICADO HISTÓRICO", "2026-07-19", app.now_str()),
                )
            app._apply_receipt_deduplication_migration(con)
            count = int(
                con.execute(
                    "SELECT COUNT(*) FROM recibos WHERE nombre=%s",
                    ("DUPLICADO HISTÓRICO",),
                ).fetchone()[0]
            )
            index_exists = con.execute(
                "SELECT to_regclass('uq_recibos_active_patient_service_date')"
            ).fetchone()[0]
        self.assertEqual(count, 2)
        self.assertIsNone(index_exists)

    def test_concurrent_creation_allows_only_one_active_receipt(self):
        barrier = Barrier(2)

        def create(number):
            barrier.wait()
            try:
                return ("saved", self.add_receipt(number, "PACIENTE CONCURRENTE"))
            except app.DuplicateReceiptError as exc:
                return ("duplicate", str(exc))

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(create, (910008, 910009)))
        self.assertEqual(sorted(result[0] for result in results), ["duplicate", "saved"])
        with app.db_connect() as con:
            count = int(
                con.execute(
                    """SELECT COUNT(*) FROM recibos
                       WHERE is_deleted=0 AND fecha=%s AND dedup_key=%s""",
                    (
                        "2026-07-21",
                        app.build_receipt_dedup_key("PACIENTE CONCURRENTE"),
                    ),
                ).fetchone()[0]
            )
        self.assertEqual(count, 1)

    def test_history_enables_trash_for_authorized_auditor(self):
        receipt_id = self.add_receipt(910010, "PACIENTE HISTORIAL")
        with app.db_connect() as con:
            con.execute(
                "UPDATE recibos SET estado_facturacion='FACTURADO' WHERE id=%s",
                (receipt_id,),
            )

        class MainWindowStub:
            current_user = {"username": "audit.test", "role": app.ROLE_AUDIT}
            is_dark_mode = False

            def open_trash_dialog(self):
                return None

        qt_app = QApplication.instance() or QApplication([])
        dialog = app.ReceiptHistoryDialog(MainWindowStub())
        deadline = time.monotonic() + 5
        while dialog.table.rowCount() == 0 and time.monotonic() < deadline:
            qt_app.processEvents()
            time.sleep(0.02)
        dialog.table.selectRow(0)
        qt_app.processEvents()
        self.assertFalse(dialog.btn_delete_receipt.isHidden())
        self.assertTrue(dialog.btn_delete_receipt.isEnabled())
        dialog.close()


if __name__ == "__main__":
    unittest.main()
