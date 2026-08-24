import copy
import os
import time
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QAbstractItemView, QDialog

import CALCULOS_QT as app


class _Toast:
    def __init__(self, *_args, **_kwargs):
        pass

    def show(self):
        pass


class MonthlyArsFormStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.rows = {
            1: {
                "id": 1, "period_year": 2026, "period_month": 7,
                "ars": "FUTURO", "ars_display_name": "FUTURO",
                "status": app.BATCH_DRAFT, "version": 1,
                "invoice_date": "2026-07-20", "invoice_number": "505",
                "ncf": "B0100000505", "receipts": 0,
            },
            2: {
                "id": 2, "period_year": 2026, "period_month": 6,
                "ars": "MAPFRE", "ars_display_name": "MAPFRE",
                "status": app.BATCH_CLOSED, "version": 1,
                "invoice_date": "2026-06-30", "invoice_number": "404",
                "ncf": "B0100000404", "receipts": 0,
            },
        }
        self.patchers = [
            patch.object(app, "list_monthly_billing_batches", side_effect=self._list),
            patch.object(app, "get_monthly_billing_batch", side_effect=self._get),
            patch.object(app, "list_monthly_batch_receipts", return_value=[]),
            patch.object(app, "list_available_receipts_for_batch", return_value=[]),
            patch.object(app, "load_monthly_batch_workspace", side_effect=self._workspace),
            patch.object(app, "get_ars_billing_profile", return_value={}),
            patch.object(app, "update_monthly_batch_configuration", side_effect=self._save),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.page = app.MonthlyBillingListsPage(
            {"username": "admin", "role": app.ROLE_ADMIN}
        )
        self._wait_loaded()

    def tearDown(self):
        self.page.close()
        for patcher in reversed(self.patchers):
            patcher.stop()

    def _list(self):
        return [copy.deepcopy(self.rows[key]) for key in sorted(self.rows)]

    def _get(self, batch_id):
        row = self.rows.get(int(batch_id))
        return copy.deepcopy(row) if row else None

    def _save(self, batch_id, values, _user, *, save_as_profile=False):
        self.rows[int(batch_id)].update(copy.deepcopy(values))

    def _workspace(self, batch_id, **_filters):
        row = self._get(batch_id)
        if row is None:
            return None
        return {"batch": row, "receipts": [], "available": []}

    def _wait_loaded(self, batch_id=None):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if self.page.current_batch is not None and (
                batch_id is None
                or int(self.page.current_batch.get("id") or 0) == int(batch_id)
            ):
                return
            time.sleep(0.005)
        self.fail("La carga asíncrona del expediente no terminó")

    def _select_id(self, batch_id):
        for row in range(self.page.batches.rowCount()):
            if int(self.page.batches.item(row, 0).text()) == int(batch_id):
                self.page.batches.selectRow(row)
                self._wait_loaded(batch_id)
                return
        self.fail(f"No se encontró expediente {batch_id}")

    def test_existing_draft_is_editable_with_valid_date(self):
        self._select_id(1)
        self.assertTrue(self.page.invoice_date.isEnabled())
        self.assertEqual(self.page.invoice_date.date().toString("dd-MM-yyyy"), "20-07-2026")
        self.assertTrue(self.page.save_configuration_button.isEnabled())

    def test_new_batch_becomes_selected_and_editable(self):
        original_dialog = app.CreateMonthlyBatchDialog

        class FakeDialog:
            MONTHS = original_dialog.MONTHS

            class Ars:
                @staticmethod
                def currentText():
                    return "FUTURO"

            ars = Ars()

            def __init__(self, _parent=None):
                pass

            @staticmethod
            def exec():
                return QDialog.Accepted

            @staticmethod
            def values():
                return {"year": 2026, "month": 8, "ars": "FUTURO", "notes": ""}

        def create_batch(**_kwargs):
            self.rows[3] = {
                "id": 3, "period_year": 2026, "period_month": 8,
                "ars": "FUTURO", "ars_display_name": "FUTURO",
                "status": app.BATCH_DRAFT, "version": 1,
                "invoice_date": "2026-07-21", "receipts": 0,
            }
            return 3

        with patch.object(app, "CreateMonthlyBatchDialog", FakeDialog), patch.object(
            app, "create_monthly_billing_batch", side_effect=create_batch
        ), patch.object(app, "FloatingToast", _Toast):
            self.page.create_batch()
        self._wait_loaded(3)
        self.assertEqual(self.page.current_batch_id, 3)
        self.assertTrue(self.page.invoice_date.isEnabled())

    def test_switch_save_and_reselect_preserves_each_batch(self):
        self._select_id(1)
        self.page.invoice_number.setText("NUEVO-505")
        with patch.object(app, "FloatingToast", _Toast):
            self.assertTrue(self.page.save_configuration())
        self._select_id(2)
        self.assertEqual(self.page.invoice_number.text(), "404")
        self._select_id(1)
        self.assertEqual(self.page.invoice_number.text(), "NUEVO-505")

    def test_no_selection_locks_form(self):
        self.page._reset_configuration_form()
        self.assertFalse(self.page.steps.isEnabled())
        self.assertFalse(self.page.invoice_date.isEnabled())
        self.assertFalse(self.page.save_configuration_button.isEnabled())
        self.assertNotEqual(self.page.invoice_date.date().toString("dd-MM-yyyy"), "01-01-2000")

    def test_closed_batch_remains_read_only(self):
        self._select_id(2)
        self.assertFalse(self.page.invoice_date.isEnabled())
        self.assertFalse(self.page.invoice_number.isEnabled())
        self.assertFalse(self.page.save_configuration_button.isEnabled())

    def test_save_button_tracks_required_fields_without_duplicate_loading(self):
        self._select_id(1)
        self.page.ars_display_name.clear()
        self.assertFalse(self.page.save_configuration_button.isEnabled())
        self.page.ars_display_name.setText("FUTURO")
        self.assertTrue(self.page.save_configuration_button.isEnabled())

    def test_candidate_date_filter_modes_and_custom_validation(self):
        self.assertEqual(self.page.candidate_date_filter.currentData(), "CUSTOM")
        self.assertEqual(
            self.page._candidate_date_bounds(),
            ("2026-07-01", "2026-07-31"),
        )
        self.assertIn("Carga transitoria julio 2026", self.page.candidate_date_label.text())
        for date_edit in (
            self.page.candidate_date_from,
            self.page.candidate_date_to,
        ):
            self.assertEqual(date_edit.displayFormat(), "dd-MM-yyyy")
            self.assertGreaterEqual(date_edit.minimumWidth(), 145)
        today = app.QDate.currentDate()
        for days in (15, 30, 60, 90):
            self.page.candidate_date_filter.blockSignals(True)
            self.page.candidate_date_filter.setCurrentIndex(
                self.page.candidate_date_filter.findData(days)
            )
            self.page.candidate_date_filter.blockSignals(False)
            date_from, date_to = self.page._candidate_date_bounds()
            self.assertEqual(
                date_from, today.addDays(-(days - 1)).toString("yyyy-MM-dd")
            )
            self.assertEqual(date_to, today.toString("yyyy-MM-dd"))

        self.page.candidate_date_filter.blockSignals(True)
        self.page.candidate_date_filter.setCurrentIndex(
            self.page.candidate_date_filter.findData("NONE")
        )
        self.page.candidate_date_filter.blockSignals(False)
        self.assertEqual(self.page._candidate_date_bounds(), (None, None))

        self.page.candidate_date_filter.blockSignals(True)
        self.page.candidate_date_filter.setCurrentIndex(
            self.page.candidate_date_filter.findData("CUSTOM")
        )
        self.page.candidate_date_filter.blockSignals(False)
        self.page.candidate_date_from.setDate(today)
        self.page.candidate_date_to.setDate(today.addDays(-1))
        with self.assertRaises(ValueError):
            self.page._candidate_date_bounds()

    def test_date_changes_do_not_search_until_button_is_used(self):
        self._select_id(1)
        with patch.object(self.page, "_load_candidates_async") as search:
            self.page.candidate_date_from.setDate(app.QDate(2026, 7, 2))
            self.qt_app.processEvents()
            search.assert_not_called()
            self.page.search_candidates_button.click()
            search.assert_called_once_with()

    def test_create_dialog_excludes_only_normalized_senasa_subsidiado(self):
        ars = [
            "SENASA CONTRIBUTIVO",
            "  SeNaSa   SuBsIdIaDo  ",
            "SENASA PENSIONADOS",
            "SENASA CONT VIEJO",
            "FUTURO",
        ]
        with patch.object(app, "list_monthly_ars_options", return_value=ars):
            dialog = app.CreateMonthlyBatchDialog()
        visible = [dialog.ars.itemText(index) for index in range(dialog.ars.count())]
        self.assertNotIn("  SeNaSa   SuBsIdIaDo  ", visible)
        self.assertEqual(
            visible,
            ["SENASA CONTRIBUTIVO", "SENASA PENSIONADOS", "SENASA CONT VIEJO", "FUTURO"],
        )
        dialog.close()

    def test_saved_lists_view_filters_states_and_actions(self):
        self.assertEqual(self.page.section_tabs.count(), 2)
        self.assertEqual(self.page.section_tabs.tabText(0), "Preparar listado")
        self.assertEqual(self.page.section_tabs.tabText(1), "Listados guardados")
        self.assertFalse(self.page.section_tabs.tabIcon(0).isNull())
        self.assertFalse(self.page.section_tabs.tabIcon(1).isNull())
        self.assertEqual(
            self.page.saved_batches.selectionMode(),
            QAbstractItemView.ExtendedSelection,
        )
        self.assertEqual(self.page.saved_batches.rowCount(), 2)

        self.page.saved_status_filter.setCurrentIndex(
            self.page.saved_status_filter.findData(app.BATCH_SENT)
        )
        self.qt_app.processEvents()
        visible = [
            self.page.saved_batches.item(row, 6).text()
            for row in range(self.page.saved_batches.rowCount())
            if not self.page.saved_batches.isRowHidden(row)
        ]
        self.assertEqual(visible, [app.BATCH_SENT])

        sent_row = next(
            row for row in range(self.page.saved_batches.rowCount())
            if self.page.saved_batches.item(row, 6).text() == app.BATCH_SENT
        )
        self.page.saved_batches.selectRow(sent_row)
        self.assertFalse(self.page.saved_edit_button.isEnabled())
        self.assertFalse(self.page.saved_delete_button.isEnabled())
        self.assertFalse(self.page.saved_confirm_button.isEnabled())
        self.assertTrue(self.page.saved_report_button.isEnabled())

        self.page.resize(640, 480)
        self.page.show()
        self.qt_app.processEvents()
        tab_bar = self.page.section_tabs.tabBar()
        self.assertLessEqual(
            tab_bar.tabRect(0).width() + tab_bar.tabRect(1).width(),
            tab_bar.width() + 2,
        )


if __name__ == "__main__":
    unittest.main()
