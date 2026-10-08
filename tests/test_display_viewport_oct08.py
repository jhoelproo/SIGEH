"""Display profiles follow the real window viewport on the same monitor."""

import os
from types import SimpleNamespace
from types import MethodType

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QRect, QSettings
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

import display_layout as layout


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def viewport(qt, tmp_path, monkeypatch):
    window = QWidget()
    screen = SimpleNamespace(
        availableGeometry=lambda: QRect(0, 0, 2560, 1440),
        logicalDotsPerInch=lambda: 120.0,
        devicePixelRatio=lambda: 1.25,
    )
    monkeypatch.setattr(window, "screen", lambda: screen)
    manager = layout.DisplayLayoutManager(window, "viewport-test")
    manager.settings = QSettings(str(tmp_path / "display.ini"), QSettings.IniFormat)
    monkeypatch.setattr(manager, "_bind_window_screen", lambda: None)
    monkeypatch.setattr(manager, "_ensure_window_visible", lambda: None)
    yield window, manager
    manager._debounce.stop()
    qt.removeEventFilter(manager)
    window.removeEventFilter(manager)
    window.close()
    manager.deleteLater()
    window.deleteLater()
    qt.processEvents()


@pytest.mark.parametrize("visible", [False, True])
@pytest.mark.parametrize(
    "size,expected,profile",
    [
        ((1280, 680), (1280, 680), layout.PROFILE_VERY_COMPACT),
        ((1366, 768), (1366, 768), layout.PROFILE_VERY_COMPACT),
        ((1920, 1080), (1920, 1080), layout.PROFILE_COMPACT),
        ((3000, 1800), (2560, 1440), layout.PROFILE_WIDE),
    ],
)
def test_snapshot_uses_window_viewport_capped_by_monitor(
    qt, viewport, size, expected, profile, visible
):
    window, manager = viewport
    window.resize(*size)
    if visible:
        window.show()
        qt.processEvents()
    snapshot = manager._make_snapshot()
    assert (snapshot.width, snapshot.height) == expected
    assert snapshot.recommended_profile == profile
    assert snapshot.logical_dpi == 120.0
    assert snapshot.device_pixel_ratio == 1.25
    assert snapshot.windows_scale == 1.25


@pytest.mark.parametrize("invalid", [0, -1])
def test_unavailable_viewport_dimension_falls_back_to_monitor(
    viewport, monkeypatch, invalid
):
    window, manager = viewport
    monkeypatch.setattr(window, "width", lambda: invalid)
    monkeypatch.setattr(window, "height", lambda: invalid)
    snapshot = manager._make_snapshot()
    assert (snapshot.width, snapshot.height) == (2560, 1440)


def test_no_screen_uses_safe_geometry_but_keeps_valid_window_size(
    viewport, monkeypatch
):
    window, manager = viewport
    window.resize(1024, 700)
    monkeypatch.setattr(window, "screen", lambda: None)
    monkeypatch.setattr(layout.QGuiApplication, "primaryScreen", lambda: None)
    snapshot = manager._make_snapshot()
    assert (snapshot.width, snapshot.height) == (1024, 700)
    assert snapshot.logical_dpi == 96.0
    assert snapshot.device_pixel_ratio == 1.0


def test_viewport_snapshot_retains_saved_and_preview_preferences(viewport):
    window, manager = viewport
    window.resize(1280, 680)
    manager.settings.setValue("display/viewport-test/profile", layout.PROFILE_WIDE)
    manager.settings.setValue("display/viewport-test/density", layout.DENSITY_NORMAL)
    manager.settings.setValue("display/viewport-test/text_scale", "110")
    saved = manager._make_snapshot()
    assert (saved.width, saved.height) == (1280, 680)
    assert saved.recommended_profile == layout.PROFILE_VERY_COMPACT
    assert saved.applied_profile == layout.PROFILE_WIDE
    assert saved.density == layout.DENSITY_NORMAL
    assert saved.text_percent == 110
    manager.setProperty("preview_profile", layout.PROFILE_COMPACT)
    manager.setProperty("preview_density", layout.DENSITY_COMPACT)
    manager.setProperty("preview_text_scale", "90")
    preview = manager._make_snapshot()
    assert preview.applied_profile == layout.PROFILE_COMPACT
    assert preview.density == layout.DENSITY_COMPACT
    assert preview.text_percent == 90
    assert (
        manager.settings.value("display/viewport-test/profile") == layout.PROFILE_WIDE
    )


def test_real_resize_debounce_emits_same_monitor_changes_and_restores_wide(
    qt, viewport
):
    window, manager = viewport
    window.resize(2560, 1440)
    window.show()
    qt.processEvents()
    manager._debounce.stop()
    manager.refresh(force=True)
    events = QSignalSpy(manager.layout_changed)
    window.resize(1280, 680)
    qt.processEvents()
    assert events.wait(1500)
    compact = events.at(0)[0]
    assert (compact["snapshot"].width, compact["snapshot"].height) == (1280, 680)
    assert compact["profile_changed"]
    assert compact["snapshot"].applied_profile == layout.PROFILE_VERY_COMPACT
    window.resize(1366, 700)
    qt.processEvents()
    assert events.wait(1500)
    same_profile = events.at(1)[0]
    assert (same_profile["snapshot"].width, same_profile["snapshot"].height) == (
        1366,
        700,
    )
    assert not same_profile["profile_changed"]
    window.resize(2560, 1440)
    qt.processEvents()
    assert events.wait(1500)
    restored = events.at(2)[0]
    assert restored["snapshot"].applied_profile == layout.PROFILE_WIDE
    assert restored["profile_changed"]
    manager.refresh()
    QTest.qWait(250)
    assert events.count() == 3


@pytest.mark.parametrize(
    "size,expected",
    [
        ((2560, 1024), (2556, 1024)),
        ((1800, 1440), (1800, 1432)),
        ((2560, 1440), (2556, 1432)),
        ((1800, 1000), (1800, 1000)),
    ],
)
def test_frame_caps_resize_client_dimensions_once_without_growth(
    viewport, monkeypatch, size, expected
):
    window, manager = viewport
    window.resize(*size)
    window.move(20, 30)
    monkeypatch.setattr(
        window,
        "frameGeometry",
        lambda: QRect(window.x(), window.y(), window.width() + 4, window.height() + 8),
    )
    ensure_visible = MethodType(
        layout.DisplayLayoutManager._ensure_window_visible, manager
    )
    ensure_visible()
    assert (window.width(), window.height()) == expected
    stable_geometry = window.geometry()
    for _ in range(3):
        ensure_visible()
        assert window.geometry() == stable_geometry
    assert window.x() >= 0 and window.y() >= 0
    assert window.frameGeometry().right() <= 2560
    assert window.frameGeometry().bottom() <= 1440


def test_maximized_window_keeps_client_geometry_and_position(viewport, monkeypatch):
    window, manager = viewport
    window.resize(3000, 1800)
    window.move(20, 30)
    before = window.geometry()
    monkeypatch.setattr(window, "isMaximized", lambda: True)
    ensure_visible = MethodType(
        layout.DisplayLayoutManager._ensure_window_visible, manager
    )
    ensure_visible()
    assert window.geometry() == before
