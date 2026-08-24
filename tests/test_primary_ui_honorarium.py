from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import CALCULOS_QT as app


class _Result:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchall(self):
        return list(self._rows)


class _HonorariumDB:
    def __init__(self):
        self.row = {
            "id": 10,
            "nombre": "HUMANO",
            "suppress_honorarium_prompt": False,
        }
        self.audit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, query, params=()):
        sql = " ".join(str(query).split()).upper()
        if "SELECT ID,NOMBRE,SUPPRESS_HONORARIUM_PROMPT" in sql:
            return _Result([dict(self.row)])
        if sql.startswith("UPDATE ARS SET SUPPRESS_HONORARIUM_PROMPT"):
            self.row["suppress_honorarium_prompt"] = bool(params[0])
            return _Result(rowcount=1)
        if sql.startswith("INSERT INTO ACTION_HISTORY"):
            self.audit_count += 1
            return _Result(rowcount=1)
        if "SELECT ID AS ARS_ID,NOMBRE AS ARS_NAME" in sql:
            return _Result(
                [
                    {
                        "ars_id": self.row["id"],
                        "ars_name": self.row["nombre"],
                        "suppress_honorarium_prompt": self.row[
                            "suppress_honorarium_prompt"
                        ],
                        "updated_at": "2026-08-20T12:00:00+00:00",
                    }
                ]
            )
        raise AssertionError(f"SQL inesperado: {sql}")


def test_logout_releases_operational_lease_before_closing_login(monkeypatch):
    calls = []

    class Service:
        def release_login_session(self, **_kwargs):
            calls.append("release")
            return True

    monkeypatch.setattr(app, "OperationalSessionService", lambda _factory: Service())
    monkeypatch.setattr(
        app, "end_active_session", lambda *_args, **_kwargs: calls.append("close") or True
    )
    monkeypatch.setattr(app, "log_action", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "write_runtime_log", lambda *_args, **_kwargs: None)
    app.MainWindow._complete_remote_logout(
        "admin", "administrador", "LOGIN-1", "PC-1", "LOGOUT", "test"
    )
    assert calls == ["release", "close"]


def test_global_honorarium_setting_is_admin_only_and_audited(monkeypatch):
    database = _HonorariumDB()
    monkeypatch.setattr(app, "db_connect", lambda: database)
    with pytest.raises(PermissionError):
        app.save_ars_honorarium_prompt_settings(
            {10: True}, {"username": "aux", "role": "auxiliar"}
        )
    rows = app.save_ars_honorarium_prompt_settings(
        {10: True}, {"username": "admin", "role": "administrador"}
    )
    assert rows[0]["suppress_honorarium_prompt"] is True
    assert database.audit_count == 1
    assert app.HONORARIUM_PROMPT_SETTINGS.suppressed(ars_id=10)


class _BillingContext:
    current_ars = "HUMANO"
    _current_ars_id = 10
    _current_honorarium_prompt_suppressed = False
    _honorarium_prompted_ars = set()
    ars_cache = {"Honorarios": {"Honorario Día": 100.0}}

    @staticmethod
    def cart_has_category(_category):
        return False


def test_honorarium_default_and_new_ars_show_without_configuration():
    app.HONORARIUM_PROMPT_SETTINGS.update([])
    context = _BillingContext()
    context._honorarium_prompted_ars = set()
    assert app.should_show_honorarium_prompt(10, context)
    context._current_ars_id = 999
    context.current_ars = "ARS NUEVA"
    assert app.should_show_honorarium_prompt(999, context)


def test_honorarium_suppression_and_missing_services_skip_prompt():
    context = _BillingContext()
    context._honorarium_prompted_ars = set()
    app.HONORARIUM_PROMPT_SETTINGS.update(
        [{
            "ars_id": 10,
            "ars_name": "HUMANO",
            "suppress_honorarium_prompt": True,
        }]
    )
    assert not app.should_show_honorarium_prompt(10, context)
    app.HONORARIUM_PROMPT_SETTINGS.update([])
    context.ars_cache = {"Honorarios": {}}
    assert not app.should_show_honorarium_prompt(10, context)


def test_honorarium_cache_refresh_changes_other_pc_without_database_lookup():
    context = _BillingContext()
    context._honorarium_prompted_ars = set()
    app.HONORARIUM_PROMPT_SETTINGS.update(
        [{
            "ars_id": 10,
            "ars_name": "HUMANO",
            "suppress_honorarium_prompt": False,
        }]
    )
    assert app.should_show_honorarium_prompt(10, context)
    app.HONORARIUM_PROMPT_SETTINGS.update(
        [{
            "ars_id": 10,
            "ars_name": "HUMANO",
            "suppress_honorarium_prompt": True,
        }]
    )
    assert not app.should_show_honorarium_prompt(10, context)


def test_honorarium_reminder_is_shown_only_once_per_receipt_and_ars(monkeypatch):
    reminders = []

    class FakeMessageBox:
        Information = 1
        AcceptRole = 2
        RejectRole = 3

        def __init__(self, _parent=None):
            self._clicked = None

        def setWindowTitle(self, _text):
            return None

        def setIcon(self, _icon):
            return None

        def setText(self, text):
            reminders.append(text)

        def setInformativeText(self, _text):
            return None

        def addButton(self, _text, role):
            button = object()
            if role == self.RejectRole:
                self._clicked = button
            return button

        def exec(self):
            return 0

        def clickedButton(self):
            return self._clicked

    class BillingContext:
        current_ars = "HUMANO"
        _current_ars_id = 10
        _current_honorarium_prompt_suppressed = False
        _honorarium_prompted_ars = set()
        ars_cache = {"Honorarios": {"Honorario Día": 100.0}}

        @staticmethod
        def cart_has_category(_category):
            return False

    monkeypatch.setattr(app, "QMessageBox", FakeMessageBox)
    app.HONORARIUM_PROMPT_SETTINGS.update(
        [
            {
                "ars_id": 10,
                "ars_name": "HUMANO",
                "suppress_honorarium_prompt": False,
            }
        ]
    )
    context = BillingContext()
    app.MainWindow.maybe_prompt_honorario(context, "Medicamentos")
    app.MainWindow.maybe_prompt_honorario(context, "Laboratorios")
    assert len(reminders) == 1
    assert context._honorarium_prompted_ars == {10}


def test_menu_styles_and_turn_entrypoints_are_canonical():
    for dark in (False, True):
        stylesheet = app.get_stylesheet(dark)
        assert "QMenu::item:disabled" in stylesheet
        assert "QMenu::separator" in stylesheet
        assert "QMenu::item:selected" in stylesheet
    source = Path("admission_source/facturacion_tabs.py").read_text(encoding="utf-8")
    assert 'command=self.request_change_admission_turn' in source
    assert "self.root.bind('<F5>', self.request_change_admission_turn)" in source
    assert "def request_change_admission_turn" in source


def test_database_update_dialog_buttons_have_theme_aware_visible_states():
    qt_app = app.QApplication.instance() or app.QApplication([])
    dialog = app.AdmissionDatabaseImportDialog(
        {"username": "admin", "role": "administrador"}, "PC-1"
    )
    for dark in (False, True):
        dialog.apply_theme(dark)
        assert dialog.styleSheet()
    assert dialog.choose_button.minimumHeight() >= 38
    assert dialog.analyze_button.minimumHeight() >= 40
    assert dialog.apply_button.minimumHeight() >= 40
    assert dialog.close_button.minimumHeight() >= 40
    assert dialog.progress_bar.minimum() == 0
    assert dialog.progress_bar.maximum() == 100
    assert dialog.progress_percent_label.text() == "0 %"
    dialog.close()
    assert qt_app is not None
