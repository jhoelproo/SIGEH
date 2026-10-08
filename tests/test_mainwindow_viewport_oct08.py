"""Resize the actual three-module window through its normal Qt event path."""

from __future__ import annotations

import os
import json
from pathlib import Path
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, QSettings, Qt, Signal
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

import CALCULOS_QT as app


class PhysicalMonitor(QObject):
    """Keep the monitor fixed while the real window changes size."""

    availableGeometryChanged = Signal()
    geometryChanged = Signal()
    logicalDotsPerInchChanged = Signal()

    def __init__(self):
        super().__init__()
        self.geometry = QRect(0, 0, 1920, 1080)

    def availableGeometry(self):
        return self.geometry

    def logicalDotsPerInch(self):
        return 96.0

    def devicePixelRatio(self):
        return 1.0


@pytest.fixture
def main_window(tmp_path, monkeypatch):
    for variable in ("LOCALAPPDATA", "APPDATA", "PROGRAMDATA"):
        monkeypatch.setenv(variable, str(tmp_path / variable))
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path / "admission"))
    monkeypatch.setenv("DATABASE_URL", "postgresql://synthetic@127.0.0.1:9/disabled")
    qt = QApplication.instance() or QApplication([])
    original_font, original_style = qt.font(), qt.styleSheet()
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeuib.ttf")
    qt.setFont(QFont("Segoe UI", 10))
    old_format = QSettings.defaultFormat()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(tmp_path))
    monitor = PhysicalMonitor()
    monkeypatch.setattr(app.MainWindow, "screen", lambda _window: monitor)
    monkeypatch.setattr(app.ReceiptHistoryDialog, "screen", lambda _window: monitor)
    monkeypatch.setattr(QApplication, "primaryScreen", lambda: monitor)
    # Data seams only: construction and all layout/event handlers remain real.
    monkeypatch.setattr(app, "db_connect", Mock(return_value=None))
    monkeypatch.setattr(app, "ars_list", lambda: ["ARS DEMO"])
    monkeypatch.setattr(app, "list_active_system_users", list)
    monkeypatch.setattr(app.MainWindow, "_request_session_health", lambda _self: None)
    monkeypatch.setattr(
        app.EmergencyWorkspacePage, "_recover_committed_closures", lambda _self: None
    )
    monkeypatch.setattr(
        app.MonthlyBillingListsPage,
        "load_batches_async",
        lambda page, keep_id=None: page._apply_batches([], keep_id),
    )
    monkeypatch.setattr(app.ReceiptHistoryDialog, "load_rows", lambda *_a, **_k: None)
    universal = {
        category: {f"ÍTEM FICTICIO {index:02}": 10.0 + index for index in range(20)}
        for category in app.UNIVERSAL_CATEGORIES
    }
    tariff = {
        "ars_id": 1,
        "sala_emergencia": 500,
        "catalogs": {
            category: {f"ÍTEM FICTICIO {index:02}": 10.0 + index for index in range(20)}
            for category in app.ARS_CATEGORIES
        },
    }
    app.ARS_RUNTIME_CACHE.put("ARS DEMO", tariff)
    window = app.MainWindow(
        {
            "id": 1,
            "username": "viewport-qa",
            "full_name": "QA LOCAL",
            "role": app.ROLE_ADMIN,
        },
        session_id="viewport-qa-session",
        startup_data={
            "preferences": {"theme": "oscuro"},
            "universal": universal,
            "local_tariffs": {"ARS DEMO": tariff},
        },
    )
    window.module_tabs.setCurrentIndex(window.billing_module_index)
    window.resize(1280, 680)
    window.show()
    QTest.qWait(500)
    assert window.module_tabs.count() == 3
    assert not isinstance(window.emergency_workspace.full_page, QLabel)
    assert window.monthly_lists_page is not None
    try:
        yield window
    finally:
        window.emergency_workspace.shutdown()
        window._logout_finalizing = True
        window.close()
        window.deleteLater()
        qt.processEvents()
        qt.setFont(original_font)
        qt.setStyleSheet(original_style)
        QSettings.setDefaultFormat(old_format)


def _resize(window, width, height):
    window.resize(width, height)
    QTest.qWait(500)
    assert (window.width(), window.height()) == (width, height)
    snapshot = window.display_layout.current_snapshot()
    assert (snapshot.width, snapshot.height) == (width, height)
    return snapshot


def _complete_rows(listing):
    return sum(
        listing.viewport().rect().contains(listing.visualItemRect(listing.item(index)))
        for index in range(listing.count())
    )


def _accessible(window, widget):
    assert widget.isVisible() and widget.isEnabled()
    rectangle = QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())
    assert window.rect().contains(rectangle), (widget.objectName(), rectangle)


def _assert_three_tabs(window):
    tabs = window.module_tabs
    assert [tabs.tabText(index) for index in range(3)] == [
        "Emergencias",
        "Facturación",
        "Listados de ARS",
    ]
    for index in range(3):
        assert tabs.isTabEnabled(index)
        assert tabs.rect().contains(tabs.tabRect(index))


def _assert_compact_billing(window):
    listing = window.catalog_workspace.current_list()
    assert listing.viewport().height() >= 220
    assert listing.sizeHintForRow(0) == 44
    assert _complete_rows(listing) >= 5
    for button in (window.btn_generate, window.btn_reset):
        _accessible(window, button)
        assert button.height() <= 36


def _save_billing_capture(window, filename):
    path = Path("output/oct08-responsive") / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    assert window.grab().save(str(path))
    listing = window.catalog_workspace.current_list()
    path.with_suffix(".json").write_text(
        json.dumps(
            {
                "client": [window.width(), window.height()],
                "monitor": [
                    window.screen().availableGeometry().width(),
                    window.screen().availableGeometry().height(),
                ],
                "catalog_viewport": listing.viewport().height(),
                "row_height": listing.sizeHintForRow(0),
                "complete_rows": _complete_rows(listing),
                "footer_heights": [
                    window.btn_generate.height(),
                    window.btn_reset.height(),
                ],
                "module_tabs": window.module_tabs.count(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _assert_border_clamp_stable(window):
    window.resize(1920, 1024)
    QTest.qWait(500)
    assert window.width() <= 1920
    assert window.height() == 1024
    stable_size = window.size()
    QTest.qWait(500)
    assert window.size() == stable_size


@pytest.mark.parametrize("width", [1280, 1366])
def test_small_window_has_five_catalog_rows_and_three_modules(main_window, width):
    snapshot = _resize(main_window, width, 680)
    assert snapshot.height <= 768
    _assert_three_tabs(main_window)
    _assert_compact_billing(main_window)
    _save_billing_capture(main_window, f"billing-three-modules-{width}.png")


def test_wide_layout_restores_after_real_resize(main_window):
    _resize(main_window, 1366, 680)
    compact_height = main_window.catalog_workspace.current_list().viewport().height()
    snapshot = _resize(main_window, 1900, 1024)
    assert snapshot.height > 768
    assert (
        main_window.catalog_workspace.current_list().viewport().height()
        > compact_height
    )
    assert main_window.patient_scroll.minimumWidth() >= 285
    assert main_window.nav_widget.width() >= 188
    assert main_window.btn_generate.height() > 36
    _assert_border_clamp_stable(main_window)
    _save_billing_capture(main_window, "billing-three-modules-1920.png")
    _resize(main_window, 1280, 680)
    _assert_compact_billing(main_window)


def test_physical_small_monitor_stays_stable_and_usable(main_window):
    monitor = main_window.screen()
    monitor.geometry = QRect(0, 0, 1280, 720)
    monitor.availableGeometryChanged.emit()
    main_window.resize(1280, 680)
    QTest.qWait(500)
    assert main_window.width() <= 1280
    assert main_window.height() == 680
    snapshot = main_window.display_layout.current_snapshot()
    assert (snapshot.width, snapshot.height) == (
        main_window.width(),
        main_window.height(),
    )
    stable_size = main_window.size()
    QTest.qWait(500)
    assert main_window.size() == stable_size
    _assert_three_tabs(main_window)
    _assert_compact_billing(main_window)
    _save_billing_capture(main_window, "billing-three-modules-monitor-1280.png")


def test_history_filters_remain_accessible_inside_small_window(main_window):
    history = app.ReceiptHistoryDialog(main_window)
    history.show()
    history.resize(1280, 680)
    history.activateWindow()
    QTest.qWait(500)
    try:
        assert (history.width(), history.height()) == (1280, 680)
        for widget in (
            history.search_edit,
            history.ars_combo,
            history.status_combo,
            history.user_filter,
            history.btn_search,
            history.btn_clear_filters,
        ):
            _accessible(history, widget)
        QTest.mouseClick(history.history_workspace.advanced_button, Qt.LeftButton)
        QTest.qWait(100)
        _accessible(history, history.document_type_combo)
        _accessible(history, history.document_edit)
        _accessible(history, history.search_edit)
        _accessible(history, history.period_filter)
        _accessible(history, history.assignment_combo)
        history.search_edit.setFocus()
        QTest.keyClicks(history.search_edit, "990430")
        assert history.search_edit.text() == "990430"
        QTest.mouseClick(history.btn_clear_filters, Qt.LeftButton)
        QTest.qWait(50)
        assert history.search_edit.text() == ""
        assert history.search_edit.hasFocus()
        assert history.grab().save(
            "output/oct08-responsive/history-three-modules-1280.png"
        )
    finally:
        history.close()
        history.deleteLater()
