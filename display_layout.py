"""Perfiles visuales adaptables y persistentes por dispositivo.

Este módulo no conoce datos clínicos ni ejecuta consultas. Centraliza únicamente
la medición de pantalla, las preferencias locales de diseño y la adaptación de
ventanas Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Iterable

from PySide6.QtCore import QEvent, QObject, QSettings, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QLineEdit,
    QMainWindow,
    QSizePolicy,
    QSpinBox,
    QDoubleSpinBox,
    QTabBar,
    QTableWidget,
    QWidget,
)


PROFILE_AUTO = "AUTO"
PROFILE_VERY_COMPACT = "MUY_COMPACTO"
PROFILE_COMPACT = "COMPACTO"
PROFILE_STANDARD = "ESTANDAR"
PROFILE_WIDE = "AMPLIO"
VALID_PROFILES = (
    PROFILE_AUTO,
    PROFILE_VERY_COMPACT,
    PROFILE_COMPACT,
    PROFILE_STANDARD,
    PROFILE_WIDE,
)

DENSITY_AUTO = "AUTOMATICA"
DENSITY_VERY_COMPACT = "MUY_COMPACTA"
DENSITY_COMPACT = "COMPACTA"
DENSITY_NORMAL = "NORMAL"
DENSITY_COMFORTABLE = "COMODA"
VALID_DENSITIES = (
    DENSITY_AUTO,
    DENSITY_VERY_COMPACT,
    DENSITY_COMPACT,
    DENSITY_NORMAL,
    DENSITY_COMFORTABLE,
)

TEXT_AUTO = "AUTO"
VALID_TEXT_SCALES = (TEXT_AUTO, "85", "90", "100", "110", "125")


@dataclass(frozen=True)
class DisplaySnapshot:
    width: int
    height: int
    logical_dpi: float
    device_pixel_ratio: float
    windows_scale: float
    recommended_profile: str
    applied_profile: str
    density: str
    text_percent: int

    @property
    def compact(self) -> bool:
        return self.applied_profile in (PROFILE_VERY_COMPACT, PROFILE_COMPACT)


def recommend_layout_profile(
    width: int,
    height: int,
    logical_dpi: float = 96.0,
    device_pixel_ratio: float = 1.0,
) -> str:
    """Selecciona el perfil usando el área lógica útil, no píxeles físicos."""
    width = max(1, int(width))
    height = max(1, int(height))
    scale = max(float(logical_dpi or 96.0) / 96.0, 1.0)
    # Qt ya entrega geometría lógica. El DPI solo inclina los casos fronterizos.
    if width <= 1600 or height <= 900 or scale >= 1.45:
        return PROFILE_VERY_COMPACT
    if width < 1920 or height < 1080 or (scale >= 1.20 and width < 2560):
        return PROFILE_COMPACT
    if width >= 1920 and height >= 1080:
        return PROFILE_WIDE
    return PROFILE_STANDARD


def recommended_density(profile: str, logical_dpi: float) -> str:
    if profile == PROFILE_VERY_COMPACT:
        return DENSITY_VERY_COMPACT
    if profile == PROFILE_COMPACT or float(logical_dpi or 96.0) >= 132.0:
        return DENSITY_COMPACT
    if profile == PROFILE_WIDE and float(logical_dpi or 96.0) <= 110.0:
        return DENSITY_COMFORTABLE
    return DENSITY_NORMAL


def profile_ratios(profile: str) -> tuple[float, float, float]:
    """Paciente, catálogo y recibo dentro del área útil de Facturación."""
    if profile == PROFILE_VERY_COMPACT:
        return (0.22, 0.31, 0.47)
    if profile == PROFILE_COMPACT:
        return (0.24, 0.33, 0.43)
    if profile == PROFILE_WIDE:
        return (0.23, 0.35, 0.42)
    return (0.25, 0.35, 0.40)


def should_expand_main_module_tabs(logical_width: int) -> bool:
    """Distribuye la navegaciÃ³n completa salvo en un ancho lÃ³gico extremo."""
    return int(logical_width or 0) >= 720


class DisplayLayoutManager(QObject):
    """Observa pantalla/DPI y aplica preferencias locales con debounce."""

    layout_changed = Signal(object)

    def __init__(self, window: QWidget, device_id: str, parent=None):
        super().__init__(parent or window)
        self.window = window
        self.device_id = str(device_id or "local-device")
        self.settings = QSettings(
            "Hospital Provincial",
            "Sistema Facturacion Medica",
        )
        self._settings_prefix = f"display/{self.device_id}"
        self._screen = None
        self._window_handle = None
        self._last_snapshot: DisplaySnapshot | None = None
        self._base_font_point_size = max(
            8.0,
            float(QApplication.instance().font().pointSizeF() or 9.0),
        )
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self.refresh)
        self.window.installEventFilter(self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def start(self) -> None:
        self._bind_window_screen()
        QTimer.singleShot(0, self.refresh)

    def preferences(self) -> dict:
        profile = str(
            self.settings.value(
                f"{self._settings_prefix}/profile",
                PROFILE_AUTO,
            )
            or PROFILE_AUTO
        ).upper()
        density = str(
            self.settings.value(
                f"{self._settings_prefix}/density",
                DENSITY_AUTO,
            )
            or DENSITY_AUTO
        ).upper()
        text_scale = str(
            self.settings.value(
                f"{self._settings_prefix}/text_scale",
                TEXT_AUTO,
            )
            or TEXT_AUTO
        ).upper()
        return {
            "layout_profile": (
                profile if profile in VALID_PROFILES else PROFILE_AUTO
            ),
            "layout_density": (
                density if density in VALID_DENSITIES else DENSITY_AUTO
            ),
            "layout_text_scale": (
                text_scale if text_scale in VALID_TEXT_SCALES else TEXT_AUTO
            ),
            "layout_modified_at": str(
                self.settings.value(
                    f"{self._settings_prefix}/modified_at",
                    "",
                )
                or ""
            ),
            "layout_device_id": self.device_id,
        }

    def update_preferences(
        self,
        *,
        profile: str,
        density: str,
        text_scale: str,
        persist: bool = True,
    ) -> None:
        profile = str(profile or PROFILE_AUTO).upper()
        density = str(density or DENSITY_AUTO).upper()
        text_scale = str(text_scale or TEXT_AUTO).upper()
        if profile not in VALID_PROFILES:
            profile = PROFILE_AUTO
        if density not in VALID_DENSITIES:
            density = DENSITY_AUTO
        if text_scale not in VALID_TEXT_SCALES:
            text_scale = TEXT_AUTO
        if persist:
            self.settings.setValue(f"{self._settings_prefix}/profile", profile)
            self.settings.setValue(f"{self._settings_prefix}/density", density)
            self.settings.setValue(
                f"{self._settings_prefix}/text_scale",
                text_scale,
            )
            self.settings.setValue(
                f"{self._settings_prefix}/modified_at",
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            )
            self.settings.sync()
        else:
            self.setProperty("preview_profile", profile)
            self.setProperty("preview_density", density)
            self.setProperty("preview_text_scale", text_scale)
        self._last_snapshot = None
        self.refresh(force=True)

    def clear_preview(self) -> None:
        for name in (
            "preview_profile",
            "preview_density",
            "preview_text_scale",
        ):
            self.setProperty(name, None)
        self._last_snapshot = None

    def reset_recommended(self) -> None:
        self.update_preferences(
            profile=PROFILE_AUTO,
            density=DENSITY_AUTO,
            text_scale=TEXT_AUTO,
        )

    def current_snapshot(self) -> DisplaySnapshot:
        if self._last_snapshot is None:
            return self._make_snapshot()
        return self._last_snapshot

    def display_summary(self) -> str:
        snapshot = self.current_snapshot()
        return (
            f"Área útil: {snapshot.width} × {snapshot.height} · "
            f"Escala: {round(snapshot.windows_scale * 100):d} % · "
            f"Recomendado: {snapshot.recommended_profile.title()} · "
            f"Aplicado: {snapshot.applied_profile.title()}"
        )

    def save_splitter(self, splitter, key: str) -> None:
        sizes = [max(0, int(value)) for value in splitter.sizes()]
        total = sum(sizes)
        if total <= 0:
            return
        ratios = [round(value / total, 6) for value in sizes]
        profile = self.current_snapshot().applied_profile
        self.settings.setValue(
            f"{self._settings_prefix}/splitters/{profile}/{key}",
            json.dumps(ratios),
        )
        self.settings.sync()

    def restore_splitter(
        self,
        splitter,
        key: str,
        default_ratios: Iterable[float],
        minimums: Iterable[int] | None = None,
    ) -> None:
        profile = self.current_snapshot().applied_profile
        raw = self.settings.value(
            f"{self._settings_prefix}/splitters/{profile}/{key}",
            "",
        )
        ratios = list(default_ratios)
        if raw:
            try:
                candidate = [float(value) for value in json.loads(str(raw))]
                if len(candidate) == splitter.count() and sum(candidate) > 0:
                    ratios = candidate
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
        available = max(
            splitter.width(),
            sum(splitter.sizes()),
            1,
        )
        minimum_values = list(minimums or [0] * splitter.count())
        if len(minimum_values) != splitter.count():
            minimum_values = [0] * splitter.count()
        sizes = [
            max(int(minimum_values[index]), int(available * ratio))
            for index, ratio in enumerate(ratios)
        ]
        splitter.setSizes(sizes)

    def reset_splitters(self) -> None:
        self.settings.beginGroup(f"{self._settings_prefix}/splitters")
        self.settings.remove("")
        self.settings.endGroup()
        self.settings.sync()
        self._last_snapshot = None
        self.refresh(force=True)

    def schedule_refresh(self, *_args) -> None:
        self._debounce.start()

    def refresh(self, force: bool = False) -> None:
        self._bind_window_screen()
        snapshot = self._make_snapshot()
        if not force and snapshot == self._last_snapshot:
            return
        profile_changed = (
            self._last_snapshot is None
            or self._last_snapshot.applied_profile != snapshot.applied_profile
        )
        self._last_snapshot = snapshot
        self._apply_font(snapshot)
        self._ensure_window_visible()
        self.layout_changed.emit(
            {
                "snapshot": snapshot,
                "profile_changed": profile_changed,
            }
        )

    def eventFilter(self, watched, event):
        event_type = event.type()
        if watched is self.window and event_type in (
            QEvent.Resize,
            QEvent.Move,
            QEvent.WindowStateChange,
            QEvent.Show,
        ):
            self.schedule_refresh()
        elif (
            event_type == QEvent.Show
            and isinstance(watched, QWidget)
            and watched.isWindow()
            and watched is not self.window
        ):
            QTimer.singleShot(
                0,
                lambda widget=watched: self._adapt_top_level(widget),
            )
        return super().eventFilter(watched, event)

    def _make_snapshot(self) -> DisplaySnapshot:
        screen = self.window.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            width, height, dpi, ratio = 1366, 768, 96.0, 1.0
        else:
            geometry = screen.availableGeometry()
            width, height = geometry.width(), geometry.height()
            dpi = float(screen.logicalDotsPerInch() or 96.0)
            ratio = float(screen.devicePixelRatio() or 1.0)
        recommended = recommend_layout_profile(width, height, dpi, ratio)
        preferences = self.preferences()
        preview_profile = self.property("preview_profile")
        preview_density = self.property("preview_density")
        preview_text = self.property("preview_text_scale")
        configured_profile = str(
            preview_profile or preferences["layout_profile"]
        ).upper()
        applied = (
            recommended
            if configured_profile == PROFILE_AUTO
            else configured_profile
        )
        configured_density = str(
            preview_density or preferences["layout_density"]
        ).upper()
        density = (
            recommended_density(applied, dpi)
            if configured_density == DENSITY_AUTO
            else configured_density
        )
        configured_text = str(
            preview_text or preferences["layout_text_scale"]
        ).upper()
        text_percent = (
            100 if configured_text == TEXT_AUTO else int(configured_text)
        )
        return DisplaySnapshot(
            width=width,
            height=height,
            logical_dpi=dpi,
            device_pixel_ratio=ratio,
            windows_scale=max(dpi / 96.0, 1.0),
            recommended_profile=recommended,
            applied_profile=applied,
            density=density,
            text_percent=text_percent,
        )

    def _bind_window_screen(self) -> None:
        handle = self.window.windowHandle()
        if handle is not None and handle is not self._window_handle:
            if self._window_handle is not None:
                try:
                    self._window_handle.screenChanged.disconnect(
                        self._screen_changed
                    )
                except (RuntimeError, TypeError):
                    pass
            handle.screenChanged.connect(self._screen_changed)
            self._window_handle = handle
        screen = self.window.screen() or QGuiApplication.primaryScreen()
        if screen is self._screen:
            return
        if self._screen is not None:
            for signal_name in (
                "availableGeometryChanged",
                "geometryChanged",
                "logicalDotsPerInchChanged",
            ):
                try:
                    getattr(self._screen, signal_name).disconnect(
                        self.schedule_refresh
                    )
                except (RuntimeError, TypeError):
                    pass
        self._screen = screen
        if screen is not None:
            for signal_name in (
                "availableGeometryChanged",
                "geometryChanged",
                "logicalDotsPerInchChanged",
            ):
                getattr(screen, signal_name).connect(self.schedule_refresh)

    def _screen_changed(self, *_args) -> None:
        self._screen = None
        self.schedule_refresh()

    def _apply_font(self, snapshot: DisplaySnapshot) -> None:
        app = QApplication.instance()
        if app is None:
            return
        font = app.font()
        target = max(
            8.0,
            self._base_font_point_size * snapshot.text_percent / 100.0,
        )
        if abs(float(font.pointSizeF()) - target) >= 0.05:
            font.setPointSizeF(target)
            app.setFont(font)

    def _ensure_window_visible(self) -> None:
        screen = self.window.screen() or QGuiApplication.primaryScreen()
        if screen is None or self.window.isMaximized():
            return
        available = screen.availableGeometry()
        frame = self.window.frameGeometry()
        width = min(frame.width(), available.width())
        height = min(frame.height(), available.height())
        x = min(max(frame.x(), available.left()), available.right() - width + 1)
        y = min(max(frame.y(), available.top()), available.bottom() - height + 1)
        if width != frame.width() or height != frame.height():
            self.window.resize(width, height)
        if x != frame.x() or y != frame.y():
            self.window.move(x, y)

    def _adapt_top_level(self, widget: QWidget) -> None:
        if widget is None or not widget.isVisible():
            return
        snapshot = self.current_snapshot()
        screen = widget.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        max_width = max(640, int(available.width() * 0.94))
        max_height = max(520, int(available.height() * 0.92))
        if widget.minimumWidth() > max_width:
            widget.setMinimumWidth(max_width)
        if widget.minimumHeight() > max_height:
            widget.setMinimumHeight(max_height)
        if widget.width() > max_width or widget.height() > max_height:
            widget.resize(
                min(widget.width(), max_width),
                min(widget.height(), max_height),
            )
        density_height = {
            DENSITY_VERY_COMPACT: 27,
            DENSITY_COMPACT: 30,
            DENSITY_NORMAL: 34,
            DENSITY_COMFORTABLE: 38,
        }.get(snapshot.density, 34)
        for button in widget.findChildren(QAbstractButton):
            if button.objectName() == "SpinArrowBtn":
                continue
            button.setMinimumHeight(density_height)
        for editor_type in (QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox):
            for editor in widget.findChildren(editor_type):
                editor.setMinimumHeight(density_height)
        for table in widget.findChildren(QTableWidget):
            row_height = density_height + (0 if snapshot.compact else 2)
            table.verticalHeader().setDefaultSectionSize(row_height)
            table.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
            table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        for tab_bar in widget.findChildren(QTabBar):
            if tab_bar.objectName() == "MainModuleTabs":
                expand = should_expand_main_module_tabs(snapshot.width)
                tab_bar.setExpanding(expand)
                tab_bar.setUsesScrollButtons(False)
                tab_bar.setSizePolicy(
                    QSizePolicy.Expanding,
                    QSizePolicy.Preferred,
                )
            else:
                tab_bar.setExpanding(False)
                tab_bar.setUsesScrollButtons(snapshot.compact)
        frame = widget.frameGeometry()
        x = min(max(frame.x(), available.left()), available.right() - frame.width() + 1)
        y = min(max(frame.y(), available.top()), available.bottom() - frame.height() + 1)
        widget.move(x, y)
