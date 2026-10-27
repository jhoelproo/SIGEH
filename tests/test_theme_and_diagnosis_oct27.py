"""Regressions for theme transitions and server-owned diagnosis removals."""

from time import perf_counter
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
from diagnosis_recommendation_admin import prepare_admin_data
from diagnosis_recommendations import DiagnosisCatalog, normalize_config
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


def test_removed_history_diagnosis_does_not_reappear_in_admin_or_completion():
    config = normalize_config({"excluded_diagnoses": ["CEFALEA M"]})
    data = prepare_admin_data(
        {
            "config": {**config, "version": 1},
            "items": [],
            "groups": {"old": {"diagnosis": "CEFALEA M", "items": []}},
        }
    )
    assert not data["definitions"]
    assert config["excluded_diagnoses"] == ["CEFALEA M"]


def test_administrator_can_suppress_a_builtin_without_rewriting_receipt_text():
    catalog = DiagnosisCatalog({"excluded_diagnoses": ["CEFALEA INTENSA"]})
    assert "CEFALEA INTENSA" not in catalog.suggestions("CEFALEA")
    assert catalog.first_two("CEFALEA INTENSA") == []
    assert catalog.normalize("CEFALEA INTENSA", allow_fuzzy=False) == "CEFALEA INTENSA"


def test_application_observer_does_not_reapply_the_same_stylesheet():
    application = SimpleNamespace(setStyleSheet=Mock())
    coordinator = SimpleNamespace(app=application, _is_dark=False)
    app.AppController._on_theme_toggled(coordinator, True)
    application.setStyleSheet.assert_not_called()


def test_theme_transition_and_diagnosis_popup_stay_readable(
    main_window, record_property
):
    window = main_window
    qt = QApplication.instance()
    durations = []
    for dark in (False, True, False, True):
        if window.is_dark_mode != dark:
            start = perf_counter()
            window.toggle_theme()
            qt.processEvents()
            durations.append(perf_counter() - start)
        popup = window.recommendation_controller.completion.completer.popup()
        popup.ensurePolished()
        palette = popup.palette()
        tokens = app.visual_theme_tokens(dark)
        assert palette.color(QPalette.Text).name().upper() == tokens["text_primary"]
        assert palette.color(QPalette.Base).name().upper() == tokens["popup_bg"]
    assert max(durations) < 3, durations
    record_property("theme_duration_seconds", durations)
