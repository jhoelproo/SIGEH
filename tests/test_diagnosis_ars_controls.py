from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from diagnosis_recommendation_admin import RecommendationAdminDialog
from diagnosis_recommendations import normalize_config, recommendations
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


def rule(ars_id, category="Laboratorios", item="LAB A", mode="pin"):
    return {
        "ars_id": ars_id,
        "diagnosis": "CEFALEA",
        "specialty": "GENERAL",
        "category": category,
        "item": item,
        "mode": mode,
    }


def test_editor_filters_catalogs_and_keeps_hidden_ars_associations(monkeypatch):
    qt = QApplication.instance() or QApplication([])
    monkeypatch.setattr(RecommendationAdminDialog, "reload", lambda self: None)
    dialog = RecommendationAdminDialog(
        SimpleNamespace(user_is_admin=lambda _: True), {}, {}
    )
    config = normalize_config({"ars_rules": [rule(1), rule(2, item="LAB B")]})
    dialog._loaded(
        {
            "config": {**config, "version": 1},
            "items": [
                {"ars_id": 1, "categoria": "Laboratorios", "nombre": "LAB A"},
                {"ars_id": 2, "categoria": "Laboratorios", "nombre": "LAB B"},
            ],
            "insurers": [{"id": 1, "nombre": "ARS A"}, {"id": 2, "nombre": "ARS B"}],
            "groups": {},
        }
    )
    while dialog._populating:
        qt.processEvents()
    dialog.tabs.setCurrentIndex(1)
    dialog.ars_selector.setCurrentIndex(1)
    assert not dialog.rules.isRowHidden(0) and dialog.rules.isRowHidden(1)
    selector = dialog.rules.cellWidget(0, 3)
    assert [selector.itemText(i) for i in range(selector.count())] == ["LAB A"]
    dialog.ars_selector.setCurrentIndex(2)
    assert dialog.rules.isRowHidden(0) and not dialog.rules.isRowHidden(1)
    assert len(dialog.configuration()["ars_rules"]) == 2
    assert dialog.configuration()["rules"] == []
    dialog.close()


@pytest.mark.parametrize("identity", [-1, 0, True, None, 2147483648, "1"])
def test_invalid_ars_identifiers_cannot_be_saved(identity):
    with pytest.raises(ValueError):
        normalize_config({"ars_rules": [rule(identity)]})


@pytest.mark.parametrize("values", [None, "CEFALEA", [""], [1], ["A" * 181]])
def test_invalid_deletion_data_is_rejected(values):
    with pytest.raises(ValueError):
        normalize_config({"excluded_diagnoses": values})


def test_specific_ars_block_overrides_shared_pin_without_affecting_other_ars():
    shared = {key: value for key, value in rule(1).items() if key != "ars_id"}
    config = normalize_config({"rules": [shared], "ars_rules": [rule(1, mode="block")]})
    model = {"config": config}
    catalog = {"Laboratorios": {"LAB A": 10}}
    assert not recommendations(model, "CEFALEA", "GENERAL", catalog, ars_id=1)[
        "Laboratorios"
    ]
    assert (
        recommendations(model, "CEFALEA", "GENERAL", catalog, ars_id=2)["Laboratorios"][
            0
        ]["item"]
        == "LAB A"
    )


def test_billing_recommendations_follow_the_active_ars_catalog(main_window):
    import CALCULOS_QT as app

    window = main_window
    window.recommendation_controller.model = {
        "config": normalize_config({"ars_rules": [rule(1), rule(2, item="LAB B")]})
    }
    window.current_ars = "ARS DEMO"
    window.ars_cache = {"Laboratorios": {"LAB A": 10, "LAB B": 20}}
    window.dx_edit.setText("CEFALEA")
    window.recommendation_controller.prepare_labels()
    assert ("Laboratorios", "LAB A") in window.recommendation_labels
    assert ("Laboratorios", "LAB B") not in window.recommendation_labels
    app.ARS_RUNTIME_CACHE.put("ARS DOS QA", {"ars_id": 2})
    window.current_ars = "ARS DOS QA"
    window.recommendation_controller.prepare_labels()
    assert ("Laboratorios", "LAB B") in window.recommendation_labels
    assert ("Laboratorios", "LAB A") not in window.recommendation_labels
