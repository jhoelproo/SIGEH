"""The administrator can find and edit diagnoses without dropping hidden data."""

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QTableWidgetItem, QToolButton

from diagnosis_recommendation_admin import RecommendationAdminDialog
from diagnosis_recommendations import normalize_config


@pytest.fixture
def editor(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(RecommendationAdminDialog, "reload", lambda self: None)
    dialog = RecommendationAdminDialog(
        SimpleNamespace(user_is_admin=lambda _user: True), {"role": "admin"}, {}
    )
    config = normalize_config(
        {
            "diagnoses": [
                {"name": "CEFALEA INTENSA", "aliases": ["DOLOR QA"]},
                {"name": "ARTRALGIA", "aliases": ["ART QA"]},
            ],
            "rules": [
                {
                    "diagnosis": "ARTRALGIA",
                    "specialty": "PEDIATRÍA",
                    "category": "Medicamentos",
                    "item": "PRODUCTO QA",
                    "mode": "pin",
                    "priority": 1,
                }
            ],
        }
    )
    dialog._loaded(
        {
            "config": {**config, "version": 2},
            "items": [{"categoria": "Medicamentos", "nombre": "PRODUCTO QA"}],
            "groups": {
                "qa": {
                    "diagnosis": "CEFALEA INTENSA",
                    "specialty": "EMERGENCIOLOGÍA",
                    "items": [
                        {
                            "category": "Laboratorios",
                            "item": "EXAMEN QA",
                            "candidates": 1,
                        }
                    ],
                }
            },
        }
    )
    dialog.show()
    application.processEvents()
    yield dialog
    dialog.close()
    application.processEvents()


def shown_names(table):
    return [
        table.item(row, 0).text()
        for row in range(table.rowCount())
        if not table.isRowHidden(row)
    ]


def test_search_finds_aliases_and_ignores_case_accents_and_token_order(editor):
    editor.search.setText("qa dolor")
    assert shown_names(editor.diagnoses) == ["CEFALEA INTENSA"]
    assert editor.search_results.text() == "1 de 2"
    editor.tabs.setCurrentIndex(1)
    editor.search.setText("pediatria")
    assert not editor.rules.isRowHidden(0)
    assert editor.search_results.text() == "1 de 1"
    editor.search.setText("NO EXISTE")
    assert editor.rules.isRowHidden(0)
    assert editor.search_results.text() == "0 de 1"


def test_searches_current_combobox_values_and_pending_items(editor):
    editor.tabs.setCurrentIndex(1)
    editor.search.setText("producto")
    assert not editor.rules.isRowHidden(0)
    editor.rules.cellWidget(0, 3).setCurrentText("OTRO QA")
    assert editor.rules.isRowHidden(0)
    editor.tabs.setCurrentIndex(2)
    editor.search.setText("examen")
    assert shown_names(editor.pending) == ["CEFALEA INTENSA"]


def test_filter_preserves_hidden_diagnoses_and_edited_aliases_on_save(editor):
    editor.search.setText("cefalea")
    row = next(
        row
        for row in range(editor.diagnoses.rowCount())
        if editor.diagnoses.item(row, 0).text() == "CEFALEA INTENSA"
    )
    editor.diagnoses.setItem(row, 1, QTableWidgetItem("DOLOR NUEVO QA"))
    value = editor.configuration()
    assert {row["name"] for row in value["diagnoses"]} == {
        "ARTRALGIA",
        "CEFALEA INTENSA",
    }
    assert value["diagnoses"][row]["aliases"] == ["DOLOR NUEVO QA"]
    editor.search.setText("dolor nuevo")
    assert shown_names(editor.diagnoses) == ["CEFALEA INTENSA"]


def test_hidden_stale_selection_is_not_removed_or_reviewed(editor):
    editor.diagnoses.selectAll()
    editor.search.setText("cefalea")
    editor.remove_rows(editor.diagnoses)
    assert shown_names(editor.diagnoses) == []
    assert editor.diagnoses.rowCount() == 1
    assert editor.diagnoses.item(0, 0).text() == "ARTRALGIA"
    assert editor.search_results.text() == "0 de 1"
    editor.pending.selectRow(0)
    editor.search.setText("NO EXISTE")
    before = editor.rules.rowCount()
    editor.review_pending("pin")
    assert editor.rules.rowCount() == before


def test_add_reveals_blank_row_and_clear_button_restores_search(editor):
    editor.search.setText("NO EXISTE")
    editor.add_diagnosis()
    row = editor.diagnoses.rowCount() - 1
    assert editor.search.text() == ""
    assert not editor.diagnoses.isRowHidden(row)
    editor.search.setFocus()
    QTest.keyClicks(editor.search, "cefalea")
    button = editor.search.findChild(QToolButton)
    assert button is not None and button.isVisible()
    QTest.mouseClick(button, Qt.LeftButton)
    QApplication.processEvents()
    assert editor.search.text() == ""
    assert editor.search.hasFocus()
    assert all(
        not editor.diagnoses.isRowHidden(row)
        for row in range(editor.diagnoses.rowCount())
    )


def test_search_survives_reload_and_parameters_disable_only_search(editor):
    editor.search.setText("cefalea")
    data = {**editor.model, "config": {**editor.configuration(), "version": 3}}
    editor._loaded(data)
    assert editor.search.text() == "cefalea"
    assert shown_names(editor.diagnoses) == ["CEFALEA INTENSA"]
    editor.tabs.setCurrentIndex(3)
    assert not editor.search.isEnabled()
    assert editor.search_results.isHidden()
    editor.tabs.setCurrentIndex(0)
    assert editor.search.isEnabled()
    assert editor.search.text() == "cefalea"


def test_learned_typos_punctuation_and_locations_share_one_admin_definition(editor):
    data = {**editor.model, "config": {**editor.configuration(), "version": 3}}
    data["groups"] = {
        str(index): {
            "diagnosis": label,
            "specialty": "EMERGENCIOLOGÍA",
            "items": [],
        }
        for index, label in enumerate(
            ("ALTRAGIA DE BRAZO IZQUIERDO ..", "ARTRALGIA DE RODILLA", "ARTRALGIA.")
        )
    }
    editor._loaded(data)
    assert shown_names(editor.diagnoses).count("ARTRALGIA") == 1
    assert editor.configuration()["diagnoses"][0]["name"] == "ARTRALGIA"


def test_pending_equivalent_diagnoses_are_shown_once_with_correct_name(editor):
    editor.model["groups"] = {
        str(index): {
            "diagnosis": label,
            "specialty": "EMERGENCIOLOGÍA",
            "items": [
                {"category": "Laboratorios", "item": "EXAMEN QA", "candidates": 1}
            ],
        }
        for index, label in enumerate(("ALTRAGIA DE BRAZO", "ARTRALGIA DE RODILLA."))
    }
    editor._fill_pending()
    assert editor.pending.rowCount() == 1
    assert shown_names(editor.pending) == ["ARTRALGIA"]


def test_add_association_is_visible_while_search_is_active(editor):
    editor.tabs.setCurrentIndex(1)
    editor.search.setText("NO EXISTE")
    editor.add_rule()
    assert editor.search.text() == ""
    assert not editor.rules.isRowHidden(editor.rules.rowCount() - 1)


def test_legacy_conflicting_associations_remain_editable_until_explicit_resolution(
    editor,
):
    editor.search.setText("cefalea")
    rule = {
        "specialty": "PEDIATRÍA",
        "category": "Medicamentos",
        "item": "PRODUCTO QA",
        "priority": 1,
    }
    editor._loaded(
        {
            "config": {
                "version": 3,
                "diagnoses": [
                    {"name": "ALTRALGIA", "aliases": ["ANTERIOR QA"]},
                    {"name": "ARTRALGIA", "aliases": ["ACTUAL QA"]},
                ],
                "rules": [
                    {**rule, "diagnosis": "ALTRALGIA", "mode": "pin"},
                    {**rule, "diagnosis": "ARTRALGIA", "mode": "block"},
                ],
            },
            "items": editor.items,
            "groups": {},
        }
    )
    assert editor.diagnoses.rowCount() == 1
    assert editor.rules.rowCount() == 2
    assert editor.search.text() == ""
    assert "conflicto" in editor.status.text()
    assert editor.tabs.currentIndex() == 1
    with pytest.raises(ValueError, match="contradictorias"):
        editor.configuration()
    row = next(
        row
        for row in range(editor.rules.rowCount())
        if editor.rules.cellWidget(row, 4).currentText() == "Bloquear"
    )
    editor.rules.selectRow(row)
    editor.remove_rows(editor.rules)
    config = editor.configuration()
    assert len(config["rules"]) == 1
    assert config["rules"][0]["mode"] == "pin"
    assert set(config["diagnoses"][0]["aliases"]) == {"ACTUAL QA", "ANTERIOR QA"}


def test_custom_typo_groups_use_trusted_label_without_creating_admin_anchor(editor):
    editor._loaded(
        {
            "config": {"version": 3, "diagnoses": [{"name": "GASTROENTERITIS"}]},
            "items": editor.items,
            "groups": {
                str(index): {
                    "diagnosis": diagnosis,
                    "specialty": "EMERGENCIOLOGÍA",
                    "items": [
                        {
                            "category": "Laboratorios",
                            "item": "EXAMEN QA",
                            "candidates": 1,
                        }
                    ],
                }
                for index, diagnosis in enumerate(
                    ("GASTROENTERITTIS", "GASTROENTERITIS")
                )
            },
        }
    )
    assert shown_names(editor.diagnoses) == ["GASTROENTERITIS"]
    assert shown_names(editor.pending) == ["GASTROENTERITIS"]
    assert editor.configuration()["diagnoses"] == [
        {"name": "GASTROENTERITIS", "aliases": []}
    ]


def test_legacy_merged_alias_overflow_loads_without_loss_then_can_be_trimmed(editor):
    aliases = [f"QA{i}" for i in range(100)]
    editor.tabs.setCurrentIndex(1)
    editor._loaded(
        {
            "config": {
                "version": 3,
                "diagnoses": [
                    {"name": "CEFALEA", "aliases": aliases},
                    {"name": "CEFALEAA", "aliases": ["ADICIONAL QA"]},
                ],
            },
            "items": editor.items,
            "groups": {},
        }
    )
    assert editor.tabs.currentIndex() == 0
    assert editor.diagnoses.rowCount() == 1
    assert "equivalencias" in editor.status.text()
    assert set(editor.diagnoses.item(0, 1).text().split("; ")) == set(aliases) | {
        "ADICIONAL QA"
    }
    with pytest.raises(ValueError, match="equivalencias"):
        editor.configuration()
    editor.diagnoses.setItem(0, 1, QTableWidgetItem("; ".join(aliases)))
    assert len(editor.configuration()["diagnoses"][0]["aliases"]) == 100
