import json
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QSettings

from admission_report_favorites import MAX_SAVED_VIEWS, ReportFavoriteStore


@pytest.fixture
def preferences(tmp_path):
    return QSettings(str(tmp_path / "views.ini"), QSettings.IniFormat)


def test_views_are_persistent_and_isolated_by_user(preferences):
    alice = ReportFavoriteStore("alice", preferences)
    bob = ReportFavoriteStore("bob", preferences)
    alice.save({"name": "Mi vista"})
    assert bob.list() == []
    assert ReportFavoriteStore("alice", preferences).list()[0]["name"] == "Mi vista"
    alice.save({"name": "Mi vista", "specialty": "PEDIATRIA"})
    assert len(alice.list()) == 1
    assert alice.list()[0]["specialty"] == "PEDIATRIA"
    alice.remove("Mi vista")
    assert alice.list() == []


@pytest.mark.parametrize("raw", ["bad", "null", "{}", '[{"name":""}]'])
def test_corrupted_views_are_reported_without_replacing_data(preferences, raw):
    store = ReportFavoriteStore("alice", preferences)
    preferences.setValue(store.key, raw)
    with pytest.raises(ValueError):
        store.save({"name": "new"})
    assert preferences.value(store.key) == raw


def test_view_limit_does_not_prevent_updating_existing_view(preferences):
    store = ReportFavoriteStore("alice", preferences)
    values = [{"name": str(index)} for index in range(MAX_SAVED_VIEWS)]
    preferences.setValue(store.key, json.dumps(values))
    with pytest.raises(ValueError, match="40"):
        store.save({"name": "another"})
    store.save({"name": "0", "specialty": "PEDIATRIA"})
    assert len(store.list()) == MAX_SAVED_VIEWS


def test_anonymous_session_cannot_save_views(preferences):
    with pytest.raises(ValueError):
        ReportFavoriteStore(" ", preferences)


def test_preferences_storage_errors_are_reported():
    settings = Mock()
    settings.value.return_value = "[]"
    settings.status.return_value = QSettings.AccessError
    store = ReportFavoriteStore("alice", settings)
    with pytest.raises(OSError):
        store.save({"name": "View"})
