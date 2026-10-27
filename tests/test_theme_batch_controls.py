from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
from app_icons import AppIcons
from ADMISION_PYSIDE6_V15 import qt_compat


def test_theme_restores_updates_when_a_component_fails(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(app, "store_boot_theme_preference", lambda _: None)
    window = SimpleNamespace(
        is_dark_mode=False,
        setUpdatesEnabled=Mock(),
        setStyleSheet=Mock(),
        apply_button_colors=Mock(side_effect=RuntimeError("Synthetic style failure")),
    )
    with pytest.raises(RuntimeError):
        app.MainWindow._apply_theme_transition(window)
    assert [call.args for call in window.setUpdatesEnabled.call_args_list] == [
        (False,),
        (True,),
    ]
    window.apply_button_colors.side_effect = None
    window._restore_billing_original_icons = Mock()
    window._refresh_cart_action_icons = Mock()
    window.theme_toggled = Mock()
    app.MainWindow._apply_theme_transition(window)
    window.theme_toggled.emit.assert_called_once_with(False)


def test_nested_style_batches_apply_once_and_ignore_unchanged_values(monkeypatch):
    application = QApplication.instance() or QApplication([])
    widget = SimpleNamespace(
        _style_name="QA.Batch", _bootstyle="", _apply_compat_style=Mock()
    )
    monkeypatch.setattr(qt_compat, "_STYLE_WIDGETS", [widget])
    monkeypatch.setattr(qt_compat, "_STYLE_REGISTRY", {})
    style = qt_compat.Style()
    with style.batch():
        style.configure("QA.Batch", foreground="red")
        with style.batch():
            style.configure("QA.Batch", foreground="blue")
        widget._apply_compat_style.assert_not_called()
    widget._apply_compat_style.assert_called_once()
    style.configure("QA.Batch", foreground="blue")
    widget._apply_compat_style.assert_called_once()
    style._refresh(set())
    widget._apply_compat_style.side_effect = RuntimeError("Deleted widget")
    style.configure("QA.Batch", foreground="green")


def test_icon_cache_reuses_pixels_and_has_a_bounded_size(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(AppIcons, "_icon_cache", {})
    paint = Mock(return_value=QPixmap(1, 1))
    monkeypatch.setattr(AppIcons, "_paint", paint)
    first = AppIcons.icon("add", color="#123456")
    assert AppIcons.icon("add", color="#123456").cacheKey() == first.cacheKey()
    assert paint.call_count == 3
    for size in range(1, 258):
        AppIcons.icon("add", size=size, color="#123456")
    assert len(AppIcons._icon_cache) <= 256


def test_nested_icon_refresh_restores_filter_on_exception(monkeypatch):
    application = QApplication.instance() or QApplication([])
    icons = AppIcons()
    decorate = Mock()
    monkeypatch.setattr(icons, "decorate_tree", decorate)
    with pytest.raises(RuntimeError):
        with icons.batch_refresh(None):
            with icons.batch_refresh(None):
                assert icons._refresh_depth == 2
            raise RuntimeError("Synthetic failure")
    assert icons._refresh_depth == 0
    decorate.assert_called_once_with(None)
