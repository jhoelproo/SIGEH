"""Regression cases for receipt lookup and shared monthly patient metadata."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def candidates():
    return [
        {
            "candidate_key": "R:1",
            "numero": 990834,
            "nombre": "MARÍA PÉREZ",
            "nss_snapshot": "001234567",
            "cedula_snapshot": "402-1111111-1",
        },
        {
            "candidate_key": "R:2",
            "numero": 12,
            "nombre": "JUAN PÉREZ",
            "nss_snapshot": "",
            "cedula_snapshot": "",
        },
        {"candidate_key": "A:SRC:3", "nombre": "SIN RECIBO"},
    ]


@pytest.mark.parametrize(
    "mode,term,expected",
    [
        ("RECEIPT", "990834", ["R:1"]),
        ("ALL", "990834", ["R:1"]),
        ("RECEIPT", "12", ["R:2"]),
        ("ALL", "12", ["R:2"]),
        ("RECEIPT", "990", ["R:1"]),
    ],
)
def test_receipt_number_search_includes_short_and_partial_numbers(
    candidates, mode, term, expected
):
    rows, warning = app.filter_monthly_batch_candidates(candidates, mode, term)
    assert warning is None
    assert [row["candidate_key"] for row in rows] == expected


def test_candidate_dialog_offers_receipt_lookup_without_losing_selection(
    qt, candidates
):
    dialog = app.MonthlyBatchCandidateSelectionDialog(candidates)
    try:
        index = dialog.search_mode.findData("RECEIPT")
        assert index >= 0
        dialog.table.item(1, 1).setCheckState(Qt.Checked)
        dialog.search_mode.setCurrentIndex(index)
        dialog.search_input.setText("990834")
        dialog.search_button.click()
        assert not dialog.table.isRowHidden(0)
        assert dialog.table.isRowHidden(1)
        assert dialog.selected_keys_in_source_order() == ["R:2"]
        dialog._clear_search()
        assert dialog.selected_keys_in_source_order() == ["R:2"]
    finally:
        dialog.close()


def test_editor_can_correct_name_and_select_saved_specialties(qt):
    dialog = app.MonthlyReceiptEditorDialog(
        {
            "patient_snapshot": "NOMBRE ERRONEO",
            "numero": 990834,
            "specialty_snapshot": "PEDIATRIA",
            "document_number_snapshot": "001234567",
            "authorization_snapshot": "0001234",
        }
    )
    try:
        assert hasattr(dialog, "patient_name")
        dialog.patient_name.setText("NOMBRE CORREGIDO")
        assert isinstance(dialog.specialty, QComboBox)
        assert {"EMERGENCIOLOGÍA", "PEDIATRÍA", "GINECOLOGÍA"}.issubset(
            {dialog.specialty.itemText(i) for i in range(dialog.specialty.count())}
        )
        dialog.specialty.setEditText("gineco")
        assert dialog.values()["specialty"] == "GINECOLOGÍA"
        assert dialog.values()["patient_name"] == "NOMBRE CORREGIDO"
    finally:
        dialog.close()


@pytest.fixture
def history(qt, monkeypatch):
    monkeypatch.setattr(
        app.ReceiptHistoryDialog, "load_rows", lambda *args, **kwargs: None
    )
    dialog = app.ReceiptHistoryDialog(
        SimpleNamespace(
            current_user={"role": app.ROLE_ADMIN, "username": "qa"},
            is_dark_mode=True,
        )
    )
    dialog.show()
    qt.processEvents()
    assert QTest.qWaitForWindowActive(dialog)
    yield dialog
    dialog.close()


def test_clear_history_filters_returns_focus_and_accepts_typing(history, qt):
    history.search_edit.setText("990834")
    history.status_combo.setFocus()
    history.btn_clear_filters.click()
    qt.processEvents()
    assert history.search_edit.hasFocus()
    QTest.keyClicks(history.search_edit, "990835")
    assert history.search_edit.text() == "990835"


@pytest.mark.parametrize("control", ["ars_combo", "status_combo", "assignment_combo"])
def test_history_filter_selection_returns_focus_to_search(history, qt, control):
    combo = getattr(history, control)
    if combo.count() < 2:
        combo.addItem("FUTURO", "FUTURO")
    combo.setFocus()
    combo.setCurrentIndex(1)
    combo.activated.emit(1)
    qt.processEvents()
    assert history.search_edit.hasFocus()
    QTest.keyClicks(history.search_edit, "990834")
    assert history.search_edit.text() == "990834"


@pytest.mark.parametrize("control", ["ars_combo", "status_combo", "assignment_combo"])
def test_cancelled_filter_popup_restores_search_across_repeated_queries(
    history, qt, control
):
    combo = getattr(history, control)
    calls = []
    history.load_rows = lambda **kwargs: calls.append(kwargs)
    for number in ("990834", "12", "990835"):
        combo.setFocus()
        combo.showPopup()
        qt.processEvents()
        assert QTest.qWaitForWindowExposed(combo.view().window())
        combo.hidePopup()
        qt.processEvents()
        assert history.search_edit.hasFocus()
        history.search_edit.clear()
        QTest.keyClicks(history.search_edit, number)
        QTest.keyClick(history.search_edit, Qt.Key_Return)
        qt.processEvents()
        assert history.search_edit.text() == number
        assert history.isVisible()
        assert qt.activeWindow() == history
    assert calls == [{"reset": True}] * 3


def test_query_completion_preserves_focus_in_advanced_document_field(history, qt):
    history.history_workspace.advanced.show()
    history.document_edit.show()
    history.document_edit.setFocus()
    history._set_history_query_busy(False)
    qt.processEvents()
    assert history.document_edit.hasFocus()


def test_query_completion_returns_focus_from_search_button(history, qt):
    history.btn_search.setFocus()
    history._set_history_query_busy(True)
    history._set_history_query_busy(False)
    qt.processEvents()
    assert history.search_edit.hasFocus()
    assert history.search_edit.isEnabled()
    assert history.btn_search.isEnabled()


def test_focus_restoration_respects_modal_and_closed_history(history, qt):
    modal = QDialog(history)
    modal.setModal(True)
    modal.show()
    qt.processEvents()
    history.history_workspace.search_focus.restore()
    qt.processEvents()
    assert not history.search_edit.hasFocus()
    modal.close()
    history.close()
    history.history_workspace.search_focus.restore()
    qt.processEvents()
    assert not history.isVisible()


def test_custom_period_keeps_date_focus_but_fixed_period_returns_to_search(history, qt):
    period = history.period_filter
    period.period_combo.setCurrentText("Personalizado")
    period.date_from.setFocus()
    period.period_combo.activated.emit(period.period_combo.currentIndex())
    qt.processEvents()
    assert not history.search_edit.hasFocus()
    period.period_combo.setCurrentText("Hoy")
    period.period_combo.activated.emit(period.period_combo.currentIndex())
    qt.processEvents()
    assert history.search_edit.hasFocus()


def test_open_popup_does_not_lose_its_focus_then_period_close_restores_search(
    history, qt
):
    combo = history.period_filter.period_combo
    combo.setFocus()
    combo.showPopup()
    qt.processEvents()
    assert QTest.qWaitForWindowExposed(combo.view().window())
    history.history_workspace.search_focus.restore()
    qt.processEvents()
    assert not history.search_edit.hasFocus()
    combo.hidePopup()
    qt.processEvents()
    assert history.search_edit.hasFocus()


def test_manual_query_completion_and_error_both_restore_search(
    history, qt, monkeypatch
):
    history.btn_clear_filters.setFocus()
    history.history_workspace.search_focus.query_completed()
    qt.processEvents()
    assert history.search_edit.hasFocus()
    monkeypatch.setattr(app, "write_runtime_log", lambda *_args: None)
    history.btn_search.setFocus()
    history._set_history_query_busy(True)
    history._on_history_query_failed(history._query_generation, "FALLO SINTETICO")
    qt.processEvents()
    assert history.search_edit.hasFocus()
    assert history.btn_search.isEnabled()
    assert "FALLO SINTETICO" in history.lbl_query_error.text()


def test_saved_list_name_refreshes_admission_consumer(qt, monkeypatch):
    from tests.test_monthly_ars_form_state import MonthlyArsFormStateTests

    MonthlyArsFormStateTests.qt_app = qt
    fixture = MonthlyArsFormStateTests()
    fixture.setUp()
    try:
        fixture._select_id(1)
        fixture._load_test_patients()
        page = fixture.page
        page.patients.selectRow(0)
        refresh = Mock()
        page._refresh_admission_billing_views = refresh
        editor = Mock()
        values = {
            "patient_name": "NOMBRE CORREGIDO",
            "expected_patient_name": "NOMBRE ANTERIOR",
        }
        editor.exec.return_value = QDialog.Accepted
        editor.values.return_value = values
        update = Mock()
        monkeypatch.setattr(app, "MonthlyReceiptEditorDialog", lambda *_args: editor)
        monkeypatch.setattr(app, "update_monthly_batch_receipt_export_data", update)
        monkeypatch.setattr(
            app, "FloatingToast", lambda *_args: SimpleNamespace(show=lambda: None)
        )
        monkeypatch.setattr(page, "load_selected_batch", lambda: None)
        page.edit_selected_patient()
        assert update.call_args.kwargs["patient_name"] == "NOMBRE CORREGIDO"
        refresh.assert_called_once()
    finally:
        fixture.tearDown()
