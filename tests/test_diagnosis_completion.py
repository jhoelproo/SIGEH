"""Diagnosis selection edits one segment and keeps manual editing available."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QToolButton, QVBoxLayout, QWidget

import diagnosis_recommendation_ui as ui
from diagnosis_recommendations import DiagnosisCatalog, normalize_config


@pytest.fixture(scope="module")
def qt_application():
    application = QApplication.instance() or QApplication([])
    yield application


@pytest.fixture
def editor(qt_application):
    window = QWidget()
    layout = QVBoxLayout(window)
    field = QLineEdit(window)
    field.setClearButtonEnabled(True)
    layout.addWidget(field)
    catalog = DiagnosisCatalog(
        normalize_config(
            {
                "diagnoses": [
                    {"name": "CEFALEA MIGRAÑOSA"},
                    {"name": "CEFALEA TENSIONAL"},
                    {"name": "CEFALEA INTENSA"},
                    {"name": "CEFALEA DE ORIGEN A INVESTIGAR"},
                ]
            }
        )
    )
    provider = Mock(return_value=catalog)
    completion = ui.DiagnosisCompletion(field, provider, window)
    window.show()
    window.activateWindow()
    field.setFocus()
    QApplication.processEvents()
    yield SimpleNamespace(
        window=window, field=field, completion=completion, provider=provider
    )
    completion.stop()
    window.close()
    window.deleteLater()
    QApplication.processEvents()


@pytest.mark.parametrize(
    "text,cursor,expected",
    [
        ("CEFALEA", 7, (0, 7, "CEFALEA")),
        ("ABORTO; CEFA", 12, (8, 12, "CEFA")),
        ("ABORTO / CEFA", 13, (9, 13, "CEFA")),
        ("ABORTO // CEFA", 14, (10, 14, "CEFA")),
        ("ABORTO // CEFA", 8, (8, 8, "")),
        ("ABORTO | CEFA", 13, (9, 13, "CEFA")),
        ("ABORTO\nCEFA", 11, (7, 11, "CEFA")),
        (" CEFALEA ; ABORTO", 5, (1, 8, "CEFA")),
        ("CEFALEA DE O/I", 14, (0, 14, "CEFALEA DE O/I")),
        ("CEFALEA DE O / I", 16, (0, 16, "CEFALEA DE O / I")),
        ("ABORTO; ", 8, (8, 8, "")),
        ("", 5, (0, 0, "")),
        ("CEFALEA", -1, (0, 7, "")),
    ],
)
def test_active_segment_preserves_diagnosis_separators(text, cursor, expected):
    assert ui.active_diagnosis_segment(text, cursor) == expected


def test_selection_replaces_only_the_diagnosis_under_the_cursor():
    text = "ABORTO; CEFA; ARTRALGIA DE RODILLA IZQUIERDA"
    value, cursor = ui.replace_active_diagnosis(text, 12, "CEFALEA TENSIONAL.")
    assert value == "ABORTO; CEFALEA TENSIONAL; ARTRALGIA DE RODILLA IZQUIERDA"
    assert cursor == len("ABORTO; CEFALEA TENSIONAL")


def test_typing_shows_local_clinically_distinct_choices(editor):
    QTest.keyClicks(editor.field, "CEFALEA")
    QApplication.processEvents()
    values = editor.completion.model.stringList()
    assert set(values) >= {
        "CEFALEA MIGRAÑOSA",
        "CEFALEA TENSIONAL",
        "CEFALEA INTENSA",
        "CEFALEA DE ORIGEN A INVESTIGAR",
    }
    assert editor.completion.completer.popup().isVisible()
    editor.provider.assert_called()


def test_programmatic_text_does_not_open_a_popup(editor):
    editor.field.setText("CEFALEA")
    QApplication.processEvents()
    assert not editor.completion.completer.popup().isVisible()
    editor.provider.assert_not_called()


def test_keyboard_can_select_a_diagnosis_without_replacing_previous_text(editor):
    editor.field.setText("ABORTO; ")
    QTest.keyClicks(editor.field, "CEFALEA")
    QApplication.processEvents()
    popup = editor.completion.completer.popup()
    model = editor.completion.completer.completionModel()
    row = next(
        row
        for row in range(model.rowCount())
        if model.index(row, 0).data() == "CEFALEA TENSIONAL"
    )
    index = model.index(row, 0)
    popup.setCurrentIndex(index)
    expected = index.data()
    QTest.keyClick(editor.field, Qt.Key_Return)
    QApplication.processEvents()
    assert editor.field.text() == f"ABORTO; {expected}"
    assert editor.field.hasFocus()
    assert not popup.isVisible()


def test_arrow_keys_move_selection_and_escape_does_not_change_text(editor):
    QTest.keyClicks(editor.field, "CEFALEA")
    popup = editor.completion.completer.popup()
    assert popup.currentIndex().row() == 0
    QTest.keyClick(editor.field, Qt.Key_Down)
    assert popup.currentIndex().row() == 1
    QTest.keyClick(editor.field, Qt.Key_Up)
    assert popup.currentIndex().row() == 0
    QTest.keyClick(editor.field, Qt.Key_Escape)
    assert not popup.isVisible()
    assert editor.field.text() == "CEFALEA"


def test_return_without_a_selected_choice_keeps_normal_editing(editor):
    QTest.keyClicks(editor.field, "CEFALEA")
    popup = editor.completion.completer.popup()
    popup.setCurrentIndex(editor.completion.completer.completionModel().index(-1, 0))
    entered = QSignalSpy(editor.field.returnPressed)
    QTest.keyClick(editor.field, Qt.Key_Return)
    assert entered.count() == 1
    assert editor.field.text() == "CEFALEA"


def test_clear_button_allows_a_new_diagnosis_without_resetting_the_form(editor):
    QTest.keyClicks(editor.field, "CEFALEA")
    editor.completion.select("CEFALEA TENSIONAL")
    button = editor.field.findChild(QToolButton)
    assert button is not None and button.isVisible()
    QTest.mouseClick(button, Qt.LeftButton)
    assert editor.field.text() == ""
    QTest.keyClicks(editor.field, "CEFALEA")
    assert editor.completion.completer.popup().isVisible()


def test_mouse_can_select_a_diagnosis(editor):
    editor.field.setText("ABORTO; ")
    QTest.keyClicks(editor.field, "CEFALEA")
    popup = editor.completion.completer.popup()
    index = editor.completion.completer.completionModel().index(1, 0)
    selected = index.data()
    QTest.mouseClick(
        popup.viewport(), Qt.LeftButton, pos=popup.visualRect(index).center()
    )
    assert editor.field.text() == f"ABORTO; {selected}"
    assert editor.field.hasFocus()


def test_manual_edit_and_clear_remain_available_after_completion(editor):
    QTest.keyClicks(editor.field, "CEFALEA")
    editor.completion.select("CEFALEA INTENSA")
    QTest.keyClick(editor.field, Qt.Key_End)
    QTest.keyClicks(editor.field, " DE PRUEBA")
    assert editor.field.text() == "CEFALEA INTENSA DE PRUEBA"
    editor.field.selectAll()
    QTest.keyClick(editor.field, Qt.Key_Backspace)
    assert editor.field.text() == ""
    assert not editor.completion.completer.popup().isVisible()
    QTest.keyClicks(editor.field, "CEFALEA")
    assert editor.completion.model.stringList()


def test_unmatched_short_and_stopped_edits_do_not_show_stale_choices(editor):
    QTest.keyClicks(editor.field, "C")
    editor.provider.assert_not_called()
    QTest.keyClicks(editor.field, "EFALEA")
    assert editor.completion.model.stringList()
    editor.field.selectAll()
    QTest.keyClicks(editor.field, "ZXQ IMPOSIBLE")
    assert not editor.completion.completer.popup().isVisible()
    editor.completion.stop()
    editor.completion.select("CEFALEA INTENSA")
    assert editor.field.text() == "ZXQ IMPOSIBLE"
    editor.field.selectAll()
    QTest.keyClicks(editor.field, "CEFALEA")
    assert not editor.completion.completer.popup().isVisible()


def test_current_segment_updates_after_cursor_moves(editor):
    editor.field.setText("CEFA; CEFALEA")
    editor.field.setCursorPosition(4)
    editor.completion.update()
    assert editor.completion.model.stringList()
    editor.field.setCursorPosition(len(editor.field.text()))
    editor.completion.select("CEFALEA TENSIONAL")
    assert editor.field.text() == "CEFA; CEFALEA TENSIONAL"


def test_blurred_editor_does_not_open_a_popup(editor):
    editor.field.clearFocus()
    editor.field.setText("CEFALEA")
    editor.completion.update()
    editor.provider.assert_not_called()
    assert not editor.completion.completer.popup().isVisible()


@pytest.fixture
def receipt_controller(qt_application, monkeypatch):
    memory = SimpleNamespace(cached=lambda _key, default: default)
    monkeypatch.setattr(ui, "local_receipt_store", lambda: memory)
    backend = SimpleNamespace(write_runtime_log=Mock(), db_pool=None)
    window = QWidget()
    window.dx_edit = QLineEdit(window)
    window.universal = {}
    window.ars_cache = {}
    window.refresh_picker = Mock()
    controller = ui.RecommendationController(window, backend)
    yield controller, window
    controller.stop()
    window.close()
    window.deleteLater()
    QApplication.processEvents()


@pytest.mark.parametrize(
    "entered,expected",
    [
        ("cefalea de oi", "CEFALEA DE ORIGEN A INVESTIGAR."),
        ("ABORTO IMCOMPLETO...", "ABORTO INCOMPLETO."),
        ("ALTRALGIA DE BRAZO IZQUIERDO.", "ARTRALGIA DE BRAZO IZQUIERDO."),
        ("NUEVO DIAGNOSTICO", "NUEVO DIAGNOSTICO."),
        (
            "ABORTO INCOMPLETO; CEFALEA DE O/I.",
            "ABORTO INCOMPLETO; CEFALEA DE ORIGEN A INVESTIGAR.",
        ),
        ("", ""),
    ],
)
def test_receipt_uses_corrected_full_diagnoses_with_one_period(
    receipt_controller, entered, expected, monkeypatch
):
    controller, window = receipt_controller
    confirmation = Mock(side_effect=AssertionError("Exact correction must not prompt"))
    monkeypatch.setattr(ui.QInputDialog, "getItem", confirmation)
    window.dx_edit.setText(entered)
    assert controller.normalize_before_save()
    assert window.dx_edit.text() == expected
    assert controller.normalize_before_save()
    assert window.dx_edit.text() == expected
    confirmation.assert_not_called()


def test_cancelled_ambiguous_receipt_keeps_all_original_text(
    receipt_controller, monkeypatch
):
    controller, window = receipt_controller
    controller.model["config"] = normalize_config(
        {
            "diagnoses": [
                {"name": "ALFA", "aliases": ["X"]},
                {"name": "BETA", "aliases": ["X"]},
            ]
        }
    )
    monkeypatch.setattr(ui.QInputDialog, "getItem", lambda *_: ("", False))
    window.dx_edit.setText("ABORTO IMCOMPLETO; X")
    assert not controller.normalize_before_save()
    assert window.dx_edit.text() == "ABORTO IMCOMPLETO; X"


@pytest.mark.parametrize(
    "selected,expected", [("ALFA", "ALFA."), ("Conservar texto ingresado", "X.")]
)
def test_ambiguous_diagnosis_uses_the_users_explicit_selection(
    receipt_controller, monkeypatch, selected, expected
):
    controller, window = receipt_controller
    controller.model["config"] = normalize_config(
        {
            "diagnoses": [
                {"name": "ALFA", "aliases": ["X"]},
                {"name": "BETA", "aliases": ["X"]},
            ]
        }
    )
    monkeypatch.setattr(ui.QInputDialog, "getItem", lambda *_: (selected, True))
    window.dx_edit.setText("X")
    assert controller.normalize_before_save()
    assert window.dx_edit.text() == expected
