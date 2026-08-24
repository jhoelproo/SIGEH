import inspect

import pytest

import CALCULOS_QT as app


class _Cursor:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        return _Cursor(self.row)


def test_medication_markup_uses_central_cache_without_repricing_history():
    app.update_medication_markup_cache(35.0, 1)
    assert app.calculate_medication_price(100.0) == 135.0
    assert app.get_effective_price("Medicamentos", 100.0) == 135.0
    assert app.get_effective_price("Materiales", 100.0) == 100.0
    app.update_medication_markup_cache(40.0, 2)
    assert app.calculate_medication_price(100.0) == 140.0
    app.update_medication_markup_cache(35.0, 3)


def test_claim_uses_in_memory_operational_context(monkeypatch):
    connection = _Connection(None)
    monkeypatch.setattr(app, "db_connect", lambda: connection)
    monkeypatch.setattr(
        app.BillingAdmissionQueryService,
        "current_shift",
        lambda _self: pytest.fail("current_shift must not be queried"),
    )
    result = app.claim_projected_billable_attention(
        -1,
        "legacy",
        username="admin",
        session_id="session",
        current_user={"role": "administrador"},
        global_attention_id="00000000-0000-0000-0000-000000000000",
        operational_context={
            "operational_source_id": "00000000-0000-0000-0000-000000000001",
            "turn_id": 1,
            "generation": 1,
        },
    )
    assert result is None
    assert len(connection.calls) == 1


def test_ars_change_callback_has_no_database_reads():
    source = inspect.getsource(app.MainWindow.on_ars_changed)
    assert "get_emergency_price" not in source
    assert "get_ars_items" not in source
    assert "db_connect" not in source


def test_session_timers_are_unified_and_background():
    source = inspect.getsource(app.MainWindow._build_timers)
    assert "session_health_timer" in source
    assert "remote_logout_timer" not in source
    assert "session_heartbeat_timer" not in source
    request_source = inspect.getsource(app.MainWindow._request_session_health)
    assert "SessionHealthWorker" in request_source
    assert "_session_health_requested_again" in request_source


def test_action_history_has_exact_nine_headers(monkeypatch, qapp):
    monkeypatch.setattr(
        app,
        "get_action_history_filter_options",
        lambda: {"users": [], "modules": [], "actions": []},
    )
    monkeypatch.setattr(app.HistoryDialog, "load_rows", lambda *_args, **_kwargs: None)
    dialog = app.HistoryDialog(None)
    try:
        assert dialog.table.columnCount() == 9
        assert [
            dialog.table.horizontalHeaderItem(index).text()
            for index in range(dialog.table.columnCount())
        ][-2:] == ["ARS", "Descripción"]
    finally:
        dialog.close()


def test_ars_audit_uses_structured_ars_column(monkeypatch):
    captured = {}
    monkeypatch.setattr(app, "log_action", lambda *args, **kwargs: captured.update(kwargs))
    app.log_ars_item_action(
        {"username": "admin", "role": "administrador"},
        "ITEM_ARS_ACTUALIZADO",
        item="Radiografía de pie",
        category="Imágenes",
        ars="HUMANO",
    )
    assert captured["ars_name"] == "HUMANO"


def test_catalog_dialog_rejects_non_admin(qapp):
    with pytest.raises(PermissionError):
        app.UniversalCatalogAdminDialog(
            {"username": "aux", "role": "auxiliar"}
        )


def test_ars_dialog_uses_available_1080p_height(monkeypatch, qapp):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QWidget

    class _Screen:
        def availableGeometry(self):
            return QRect(0, 0, 1920, 1040)

    class _Parent(QWidget):
        def screen(self):
            return _Screen()

    monkeypatch.setattr(app, "ars_list", lambda: [])
    parent = _Parent()
    dialog = app.ARSManagerDialog(
        {"username": "admin", "role": "administrador"}, parent
    )
    try:
        assert dialog.height() >= 990
        assert dialog.height() <= 1008
        assert dialog.width() <= 1888
    finally:
        dialog.close()
        parent.close()


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
