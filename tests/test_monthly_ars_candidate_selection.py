import inspect
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


class MonthlyArsCandidateSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.candidates = [
            {
                "candidate_key": "R:101", "numero": "101", "nombre": "MARÍA PÉREZ",
                "ars_snapshot": "FUTURO", "service_date_snapshot": "2026-08-23",
                "nss_snapshot": "001-234-567", "cedula_snapshot": "402-1111111-1",
                "authorization_snapshot": "AUT-101", "specialty_snapshot": "GENERAL",
                "total_snapshot": 100.0,
            },
            {
                "candidate_key": "R:102", "numero": "102", "nombre": "JUAN DEL RÍO",
                "ars_snapshot": "FUTURO", "service_date_snapshot": "2026-08-23",
                "nss_snapshot": "002-999-000", "cedula_snapshot": "402-2222222-2",
                "authorization_snapshot": "AUT-102", "specialty_snapshot": "PEDIATRÍA",
                "total_snapshot": 200.0,
            },
            {
                "candidate_key": "A:SRC:77", "nombre": "PACIENTE SIN DOCUMENTO",
                "ars_snapshot": "FUTURO", "service_date_snapshot": "2026-08-23",
                "nss_snapshot": "", "cedula_snapshot": "", "total_snapshot": 300.0,
            },
        ]
        self.dialog = app.MonthlyBatchCandidateSelectionDialog(self.candidates)

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()

    def _row_for_key(self, candidate_key):
        for row in range(self.dialog.table.rowCount()):
            if self.dialog.table.item(row, 0).text() == candidate_key:
                return row
        self.fail(f"No existe la fila {candidate_key}")

    def _select(self, candidate_key):
        self.dialog.table.item(
            self._row_for_key(candidate_key), 1
        ).setCheckState(Qt.Checked)

    def test_name_search_is_partial_and_accent_insensitive(self):
        rows, warning = app.filter_monthly_batch_candidates(
            self.candidates, "NAME", "maria perez"
        )
        self.assertIsNone(warning)
        self.assertEqual([row["candidate_key"] for row in rows], ["R:101"])
        rows, _warning = app.filter_monthly_batch_candidates(
            self.candidates, "NAME", "juan rio"
        )
        self.assertEqual([row["candidate_key"] for row in rows], ["R:102"])

    def test_document_search_normalizes_and_requires_four_digits_for_partial(self):
        rows, warning = app.filter_monthly_batch_candidates(
            self.candidates, "NSS", "001234567"
        )
        self.assertIsNone(warning)
        self.assertEqual([row["candidate_key"] for row in rows], ["R:101"])
        rows, warning = app.filter_monthly_batch_candidates(
            self.candidates, "CEDULA", "2222"
        )
        self.assertIsNone(warning)
        self.assertEqual([row["candidate_key"] for row in rows], ["R:102"])
        rows, warning = app.filter_monthly_batch_candidates(
            self.candidates, "NSS", "123"
        )
        self.assertEqual(rows, [])
        self.assertEqual(warning, "Escriba al menos 4 dígitos.")

    def test_selection_survives_search_clear_mode_and_selected_view(self):
        self.dialog.search_input.setText("maria")
        self.dialog._apply_filter()
        self._select("R:101")
        self.dialog.search_input.setText("juan")
        self.dialog._apply_filter()
        self._select("R:102")
        self.dialog.search_mode.setCurrentIndex(2)
        self.dialog.search_input.setText("001234567")
        self.dialog._apply_filter()
        self._select("A:SRC:77")
        self.assertEqual(
            self.dialog.selected_keys_in_source_order(),
            ["R:101", "R:102", "A:SRC:77"],
        )
        self.dialog._clear_search()
        self.assertEqual(len(self.dialog.selected_candidate_keys), 3)
        self.dialog._toggle_selected_view()
        visible_keys = {
            self.dialog.table.item(row, 0).text()
            for row in range(self.dialog.table.rowCount())
            if not self.dialog.table.isRowHidden(row)
        }
        self.assertEqual(visible_keys, {"R:101", "R:102", "A:SRC:77"})
        self.dialog.table.item(
            self._row_for_key("R:102"), 1
        ).setCheckState(Qt.Unchecked)
        self.assertEqual(
            self.dialog.selected_keys_in_source_order(), ["R:101", "A:SRC:77"]
        )

    def test_checkbox_columns_and_no_initial_functional_selection(self):
        self.assertEqual(self.dialog.table.columnCount(), 11)
        self.assertTrue(self.dialog.table.isColumnHidden(0))
        self.assertEqual(self.dialog.selected_candidate_keys, set())
        self.assertFalse(self.dialog.add_selected_button.isEnabled())
        self.assertIn("Añadir seleccionados (0)", self.dialog.add_selected_button.text())
        self.assertTrue(
            self.dialog.table.item(self._row_for_key("R:101"), 1).flags()
            & Qt.ItemIsUserCheckable
        )

    def test_clear_selection_and_placeholder_do_not_change_candidate_identity(self):
        self._select("R:101")
        self.dialog.search_mode.setCurrentIndex(1)
        self.assertEqual(self.dialog.search_input.placeholderText(), "Nombre del paciente")
        self.dialog.search_mode.setCurrentIndex(2)
        self.assertEqual(self.dialog.search_input.placeholderText(), "Número de NSS")
        self.dialog.search_mode.setCurrentIndex(3)
        self.assertEqual(self.dialog.search_input.placeholderText(), "Número de cédula")
        self.assertEqual(self.dialog.selected_keys_in_source_order(), ["R:101"])
        with patch.object(app.QMessageBox, "question", return_value=app.QMessageBox.Yes):
            self.dialog._clear_selection()
        self.assertEqual(self.dialog.selected_candidate_keys, set())
        self.assertFalse(self.dialog.add_selected_button.isEnabled())

    def test_all_mode_searches_documents_without_document_number_snapshot(self):
        rows, warning = app.filter_monthly_batch_candidates(
            self.candidates, "ALL", "4021111111"
        )
        self.assertIsNone(warning)
        self.assertEqual([row["candidate_key"] for row in rows], ["R:101"])
        all_rows, _warning = app.filter_monthly_batch_candidates(
            self.candidates, "ALL", ""
        )
        self.assertIn("A:SRC:77", {row["candidate_key"] for row in all_rows})

    def test_dialog_actions_remain_visible_at_supported_widths(self):
        self.dialog.show()
        for width, height in ((1366, 768), (1920, 1080)):
            self.dialog.resize(width, height)
            self.qt_app.processEvents()
            self.assertTrue(self.dialog.add_selected_button.isVisible())
            self.assertTrue(self.dialog.search_button.isVisible())
            self.assertLessEqual(
                self.dialog.add_selected_button.geometry().right(),
                self.dialog.contentsRect().right(),
            )

    def test_candidate_key_not_row_index_drives_confirmation(self):
        source = inspect.getsource(app.MonthlyBillingListsPage.add_available_receipt)
        self.assertIn("selected_keys_in_source_order", source)
        self.assertNotIn("selectionModel()", source)
        self.assertEqual(app.monthly_batch_candidate_key({"recibo_id": 5}), "R:5")
        self.assertEqual(
            app.monthly_batch_candidate_key({"source_instance_id": "SRC", "attention_id": 9}),
            "A:SRC:9",
        )

    def test_confirmation_revalidates_selected_keys_and_reports_omitted(self):
        calls = []

        class FakeDialog:
            def __init__(self, _candidates, _parent):
                pass

            @staticmethod
            def exec():
                return app.QDialog.Accepted

            @staticmethod
            def selected_keys_in_source_order():
                return ["R:101", "A:SRC:77"]

        page = SimpleNamespace(
            available_receipts=list(self.candidates),
            current_batch_id=9,
            current_batch={"id": 9},
            current_user={"username": "admin", "role": app.ROLE_ADMIN},
            _batch_load_token=0,
            _candidate_date_bounds=lambda: ("2026-08-01", "2026-08-31"),
            _set_patients_loading=lambda *_args: None,
            _apply_batch_workspace=lambda _workspace: None,
            _is_current_batch_editable=lambda: True,
            add_receipt_button=SimpleNamespace(setEnabled=lambda _value: None),
            add_all_receipts_button=SimpleNamespace(setEnabled=lambda _value: None),
        )

        def start_worker(operation, completed, _failed):
            completed(operation())

        page._start_monthly_worker = start_worker

        def add_receipts(*args, **kwargs):
            calls.append(("R", args, kwargs))
            return {"added": 1, "omitted": 0}

        def add_admission(*args, **kwargs):
            calls.append(("A", args, kwargs))
            return {"added": 0, "omitted": 1}

        with (
            patch.object(app, "MonthlyBatchCandidateSelectionDialog", FakeDialog),
            patch.object(app, "add_receipts_to_monthly_batch", side_effect=add_receipts),
            patch.object(app, "add_admission_candidates_to_monthly_batch", side_effect=add_admission),
            patch.object(app, "load_monthly_batch_workspace", return_value={"batch": {}}),
            patch.object(app.QMessageBox, "information") as information,
        ):
            app.MonthlyBillingListsPage.add_available_receipt(page)

        self.assertEqual([call[0] for call in calls], ["R", "A"])
        self.assertEqual(calls[0][1][1], [101])
        self.assertEqual(calls[1][1][1], ["A:SRC:77"])
        self.assertTrue(calls[0][2]["skip_ineligible"])
        self.assertTrue(calls[1][2]["return_summary"])
        self.assertIn("Agregados: 1", information.call_args.args[2])
        self.assertIn("Omitidos", information.call_args.args[2])
