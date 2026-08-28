import os
import unittest
import uuid
from unittest.mock import patch

import psycopg2
from PySide6.QtCore import Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog, QMainWindow, QMessageBox

import CALCULOS_QT as app


class MainStub(QMainWindow):
    def __init__(self, user):
        super().__init__()
        self.current_user = user
        self.logout_sources = []
        self.is_dark_mode = False

    def request_logout(self, source):
        self.logout_sources.append(source)

    def open_trash_dialog(self):
        return None


class LogoutHarness(QMainWindow):
    logout_requested = Signal()
    request_logout = app.MainWindow.request_logout
    force_logout = app.MainWindow.force_logout
    _stop_secondary_workers = app.MainWindow._stop_secondary_workers
    _close_secondary_windows_for_logout = app.MainWindow._close_secondary_windows_for_logout
    _clear_sensitive_session_references = app.MainWindow._clear_sensitive_session_references
    _shutdown_admission_session = app.MainWindow._shutdown_admission_session
    _cancel_session_work_without_waiting = app.MainWindow._cancel_session_work_without_waiting
    _complete_remote_logout = staticmethod(app.MainWindow._complete_remote_logout)
    _request_session_health = app.MainWindow._request_session_health
    _on_session_health_completed = app.MainWindow._on_session_health_completed
    _on_session_health_failed = app.MainWindow._on_session_health_failed
    _session_health_finished = app.MainWindow._session_health_finished
    _handle_inactive_login_session = app.MainWindow._handle_inactive_login_session
    check_remote_logout = app.MainWindow.check_remote_logout

    def __init__(self, user, session_id):
        super().__init__()
        self.current_user = dict(user)
        self.session_id = session_id
        self.session_started_at = app.now_str()
        self._logout_prompt_active = False
        self._logout_finalizing = False
        self.offline_login = False
        self._session_health_worker = None
        self._session_health_requested_again = False
        self._operational_context = {}
        self.current_admission_attention = object()
        self.editing_recibo_id = 1
        self.editing_recibo_numero = 1
        self.preferences = {"theme": "claro"}
        self.catalog_favorites = {"x"}
        self.monthly_lists_page = None
        self.emergency_workspace = None


class AdvancedOptionsActionHistoryLogoutTests(unittest.TestCase):
    def setUp(self):
        self.qt_app = QApplication.instance() or QApplication([])
        self.admin_url = os.environ.get(
            "HOSPITAL_E2E_ADMIN_URL",
            "postgresql://preview_admin@127.0.0.1:55432/postgres",
        )
        self.database_name = "hospital_audit_" + uuid.uuid4().hex[:12]
        try:
            admin = psycopg2.connect(self.admin_url)
        except psycopg2.Error as exc:
            self.skipTest(f"PostgreSQL local para pruebas no disponible: {exc}")
        admin.autocommit = True
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{self.database_name}"')
        admin.close()
        self.original_url = app.DB_URL
        self.original_pool = app.db_pool
        app.DB_URL = (
            "postgresql://preview_admin@127.0.0.1:55432/" + self.database_name
        )
        app.db_pool = None
        app.db_init()
        self.admin = {
            "username": "admin",
            "full_name": "Administrador del sistema",
            "role": app.ROLE_ADMIN,
        }

    def wait_for_history_load(self, dialog):
        for _ in range(500):
            self.qt_app.processEvents()
            if dialog._load_worker is None:
                return
            QTest.qWait(10)
        self.fail("La carga asíncrona del historial no terminó.")

    def wait_for_remote_logout(self, session_id, *, require_audit=False):
        for _ in range(500):
            self.qt_app.processEvents()
            with app.db_connect() as con:
                active = con.execute(
                    "SELECT is_active FROM active_sessions WHERE session_id=%s",
                    (session_id,),
                ).fetchone()[0]
                audit = con.execute(
                    """SELECT module,entity_type,entity_id,details
                       FROM action_history WHERE action='Cerrar sesión'
                       ORDER BY id DESC LIMIT 1"""
                ).fetchone()
            if int(active) == 0 and (audit is not None or not require_audit):
                return audit
            QTest.qWait(10)
        self.fail("El cierre remoto de la sesión no terminó.")

    def tearDown(self):
        for widget in list(QApplication.topLevelWidgets()):
            widget.close()
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

    def test_existing_action_history_is_extended_filtered_and_sanitized(self):
        with app.db_connect() as con:
            con.execute(
                """INSERT INTO action_history(username,action,details,created_at)
                   VALUES(%s,%s,%s,%s)""",
                ("admin", "Generar recibo pendiente", "Recibo 8123", app.now_str()),
            )
            app._apply_action_history_migration(con)
            app._apply_action_history_migration(con)
        app.log_action(
            "admin", "Crear usuario",
            "usuario.demo password=secreto postgresql://user:secret@host/db",
            module="Seguridad", entity_type="usuario", entity_id="usuario.demo",
            role=app.ROLE_ADMIN,
        )
        receipt_rows = app.list_action_history(
            module="Facturación", action="Generar recibo pendiente"
        )
        self.assertEqual(len(receipt_rows), 1)
        self.assertEqual(receipt_rows[0]["entity_type"], "recibo")
        self.assertEqual(receipt_rows[0]["entity_id"], "8123")
        security_rows = app.list_action_history(
            username="admin", module="Seguridad", action="Crear usuario"
        )
        self.assertEqual(len(security_rows), 1)
        self.assertEqual(security_rows[0]["role"], app.ROLE_ADMIN)
        self.assertNotIn("secreto", security_rows[0]["details"])
        self.assertNotIn("postgresql://", security_rows[0]["details"])
        with app.db_connect() as con:
            columns = {
                row[0] for row in con.execute(
                    """SELECT column_name FROM information_schema.columns
                       WHERE table_name='action_history'"""
                ).fetchall()
            }
            indexes = {
                row[0] for row in con.execute(
                    "SELECT indexname FROM pg_indexes WHERE tablename='action_history'"
                ).fetchall()
            }
        self.assertTrue({"role_snapshot", "module", "entity_type", "entity_id"} <= columns)
        self.assertTrue({
            "idx_action_history_created_at",
            "idx_action_history_username_created",
            "idx_action_history_module_created",
            "idx_action_history_action_created",
        } <= indexes)

    def test_history_dialog_pages_filters_and_is_read_only(self):
        with app.db_connect() as con:
            for index in range(225):
                con.execute(
                    """INSERT INTO action_history(username,action,details,created_at)
                       VALUES(%s,%s,%s,%s)""",
                    ("admin", "Acción paginada", f"Evento {index}", app.now_str()),
                )
        main = MainStub(self.admin)
        dialog = app.HistoryDialog(main)
        self.wait_for_history_load(dialog)
        self.assertEqual(dialog.table.rowCount(), 200)
        self.assertEqual(dialog.table.editTriggers(), QAbstractItemView.NoEditTriggers)
        dialog.load_rows(reset=False)
        self.wait_for_history_load(dialog)
        self.assertEqual(dialog.table.rowCount(), 225)
        action_index = dialog.action_filter.findData("Acción paginada")
        dialog.action_filter.setCurrentIndex(action_index)
        dialog.load_rows(reset=True)
        self.wait_for_history_load(dialog)
        self.assertEqual(dialog.table.rowCount(), 200)
        dialog.btn_logout.click()
        self.assertEqual(main.logout_sources, ["Historial de acciones"])
        dialog.close()

    def test_secondary_windows_expose_central_logout_callback(self):
        main = MainStub(self.admin)
        users = app.UsersAdminDialog(self.admin, main)
        history = app.HistoryDialog(main)
        self.assertIsNotNone(users.btn_logout)
        self.assertIsNotNone(history.btn_logout)
        users.btn_logout.click()
        history.btn_logout.click()
        self.assertEqual(
            main.logout_sources,
            ["Gestión de Usuarios", "Historial de acciones"],
        )
        users.close()
        history.close()

    def test_manual_logout_can_cancel_or_close_every_window(self):
        session_id = "session-manual"
        app.register_active_session("admin", session_id)
        window = LogoutHarness(self.admin, session_id)
        secondary = QDialog()
        secondary.shutdown_called = False
        secondary.shutdown_for_logout = lambda: setattr(
            secondary, "shutdown_called", True
        )
        secondary.show()
        with patch.object(QMessageBox, "question", return_value=QMessageBox.No):
            window.request_logout("Gestión de Usuarios")
        self.assertTrue(window.current_user)
        with app.db_connect() as con:
            active = con.execute(
                "SELECT is_active FROM active_sessions WHERE session_id=%s",
                (session_id,),
            ).fetchone()[0]
        self.assertEqual(int(active), 1)

        emitted = []
        window.logout_requested.connect(lambda: emitted.append(True))
        with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
            window.request_logout("Gestión de Usuarios")
        self.qt_app.processEvents()
        self.assertEqual(emitted, [True])
        self.assertFalse(secondary.shutdown_called)
        self.assertFalse(secondary.isEnabled())
        self.assertFalse(secondary.isVisible())
        self.assertEqual(window.current_user, {})
        audit = self.wait_for_remote_logout(session_id, require_audit=True)
        self.assertEqual(audit["module"], "Seguridad")
        self.assertEqual(audit["entity_type"], "sesion")
        self.assertEqual(audit["entity_id"], session_id)
        self.assertIn("Gestión de Usuarios", audit["details"])

    def test_remote_logout_uses_same_flow_without_confirmation(self):
        session_id = "session-remote"
        app.register_active_session("admin", session_id)
        window = LogoutHarness(self.admin, session_id)
        window.session_started_at = "2026-07-21 00:00:00"
        app.request_remote_logout("admin", "supervisor", "Sesión cerrada")
        emitted = []
        window.logout_requested.connect(lambda: emitted.append(True))
        with patch.object(QMessageBox, "question") as question:
            window.check_remote_logout()
            for _ in range(500):
                self.qt_app.processEvents()
                if emitted:
                    break
                QTest.qWait(10)
            else:
                self.fail("La verificación remota de sesión no terminó.")
        self.assertEqual(emitted, [True])
        question.assert_not_called()
        self.wait_for_remote_logout(session_id)


if __name__ == "__main__":
    unittest.main()
