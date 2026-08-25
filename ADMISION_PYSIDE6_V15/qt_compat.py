"""
Compatibilidad visual Tk/ttkbootstrap -> PySide6 para Admisión 4.1.9.

Objetivo de este módulo:
- Cambiar el motor gráfico a Qt/PySide6.
- Mantener intacta la lógica de negocio del archivo original.
- Implementar únicamente el subconjunto de Tk/ttkbootstrap usado por Admisión.
- Conservar nombres de métodos (`pack`, `grid`, `bind`, `after`, `Treeview`, etc.)
  para reducir al mínimo los cambios de la aplicación y, con ello, el riesgo funcional.

NO contiene lógica clínica, de turnos, persistencia, Excel, PDF, seguridad ni integración.
"""
from __future__ import annotations

import os
import re
import sys
import uuid
import weakref
from types import SimpleNamespace
from typing import Any, Callable

from PySide6.QtCore import (
    Qt, QSize, QDate, QTimer, QObject, QEvent, Signal, Slot, QThread,
    QRect, QRectF, QPoint, QPointF, QLocale, QSignalBlocker,
)
from PySide6.QtGui import (
    QPixmap, QColor, QAction, QCursor, QKeySequence, QShortcut, QPainter,
    QBrush, QPen, QPolygonF, QTextDocument, QTextCursor, QIcon,
    QPageLayout, QFontMetrics, QPalette, QFont, QCloseEvent,
)
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter, QPrinterInfo, QPrintDialog, QPrintPreviewDialog
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QComboBox, QPushButton, QTabWidget, QTabBar, QListWidget,
    QListWidgetItem, QSpinBox, QDoubleSpinBox, QGroupBox, QMessageBox,
    QSplitter, QFormLayout, QDialog, QDialogButtonBox, QToolButton, QStyle,
    QDateEdit, QAbstractSpinBox, QFileDialog, QTableWidget, QTableWidgetItem,
    QAbstractItemView, QCompleter, QMenu, QHeaderView, QSizePolicy, QInputDialog,
    QGridLayout, QCheckBox, QScrollArea, QToolTip, QRadioButton, QTextEdit,
    QStyledItemDelegate, QStyleOptionViewItem, QStackedWidget,
    QCalendarWidget, QWidgetAction, QFrame, QScrollBar, QLayout,
)

PRIMARY = "primary"
SECONDARY = "secondary"
SUCCESS = "success"
INFO = "info"
WARNING = "warning"
DANGER = "danger"
LIGHT = "light"
DARK = "dark"

END = "end"
INSERT = "insert"
NORMAL = "normal"
DISABLED = "disabled"

_BOOT_COLORS = {
    PRIMARY: "#2563EB",
    SECONDARY: "#334155",
    SUCCESS: "#1F7A4D",
    INFO: "#0E7490",
    WARNING: "#A16207",
    DANGER: "#B42318",
}

_STYLE_REGISTRY: dict[str, dict[str, Any]] = {}
_STYLE_WIDGETS: "weakref.WeakSet[Any]" = weakref.WeakSet()
_COMPAT_THEME_TOKENS: dict[str, str] = {
    "window_bg": "#0A1420",
    "panel_bg": "#111E2E",
    "input_bg": "#0F1B2A",
    "input_disabled_bg": "#182636",
    "border": "#36516A",
    "border_focus": "#72C7F5",
    "text_primary": "#E5EEF8",
    "text_secondary": "#B8C6D6",
    "text_disabled": "#8EA1B2",
    "selection_bg": "#28577E",
    "selection_text": "#FFFFFF",
    "button_primary_bg": "#1976B9",
    "button_primary_hover": "#258FDC",
    "button_primary_text": "#FFFFFF",
}


def set_compat_theme_tokens(theme_tokens: dict[str, Any] | None) -> None:
    """Set the current visual contract for compat-owned popups and modals."""
    if not isinstance(theme_tokens, dict):
        return
    for key, value in theme_tokens.items():
        if isinstance(value, str) and value:
            _COMPAT_THEME_TOKENS[key] = value


def _message_box_qss() -> str:
    """Use the active visual contract rather than a fixed dark QMessageBox skin."""
    theme = _COMPAT_THEME_TOKENS
    window = theme.get("window_bg", "#0A1420")
    input_bg = theme.get("input_bg", window)
    border = theme.get("border", "#36516A")
    focus = theme.get("border_focus", theme.get("button_primary_bg", "#1976B9"))
    text = theme.get("text_primary", "#E5EEF8")
    disabled = theme.get("text_disabled", "#8EA1B2")
    button = theme.get("button_primary_bg", "#1976B9")
    hover = theme.get("button_primary_hover", button)
    button_text = theme.get("button_primary_text", "#FFFFFF")
    return (
        f"QMessageBox{{background-color:{window};color:{text};}}"
        f"QMessageBox QLabel{{background:transparent;color:{text};font:500 11pt 'Segoe UI';}}"
        f"QMessageBox QPushButton{{color:{button_text};background:{button};"
        f"border:1px solid {button};border-radius:7px;padding:7px 22px;"
        "min-width:76px;font-weight:700;}"
        f"QMessageBox QPushButton:hover{{background:{hover};border-color:{hover};}}"
        f"QMessageBox QPushButton:focus{{border:2px solid {focus};}}"
        f"QMessageBox QPushButton:disabled{{background:{input_bg};color:{disabled};border-color:{border};}}"
    )


def _checkmark_asset_url() -> str:
    """Return the checked-indicator asset using a Qt-safe path."""
    return os.path.join(os.path.dirname(__file__), "assets", "checkmark.svg").replace(
        os.sep, "/"
    )


def _choice_control_qss(values: dict[str, Any], *, radio: bool) -> str:
    """Build the sole visual contract for checkbox and radio controls.

    The widget itself is deliberately transparent and borderless.  Only the
    indicator owns a border; assigning a border to the full control makes Qt
    paint an unwanted rectangular frame around the label on some Windows
    styles.
    """
    text = values.get("foreground", values.get("fg", "#1F2A37"))
    muted = values.get("disabledforeground", values.get("disabled_fg", "#7C8B9A"))
    input_bg = values.get("indicator_bg", values.get("fieldbackground", "#FFFFFF"))
    border = values.get("indicator_border", values.get("bordercolor", "#61788E"))
    accent = values.get("indicator_checked", values.get("accent", "#1769AA"))
    focus = values.get("focus_border", accent)
    disabled_bg = values.get("disabled_background", values.get("background", input_bg))
    control = "QRadioButton" if radio else "QCheckBox"
    radius = "8px" if radio else "3px"
    checked = (
        f"{control}::indicator:checked{{background:{input_bg};border:4px solid {accent};}}"
        if radio
        else (
            f"{control}::indicator:checked{{background:{accent};border:1px solid {accent};"
            f"image:url({_checkmark_asset_url()});}}"
        )
    )
    return (
        f"{control}{{background:transparent;color:{text};spacing:7px;"
        "border:none;outline:none;padding:0px;margin:0px;}"
        f"{control}::indicator{{width:15px;height:15px;border-radius:{radius};"
        f"background:{input_bg};border:1px solid {border};}}"
        f"{control}::indicator:hover{{border-color:{focus};}}"
        f"{checked}"
        f"{control}:focus{{border:none;outline:none;}}"
        f"{control}:focus::indicator{{border:2px solid {focus};}}"
        f"{control}:disabled{{background:transparent;color:{muted};}}"
        f"{control}::indicator:disabled{{background:{disabled_bg};border-color:{border};}}"
    )


def _ensure_app() -> QApplication:
    """Return the host QApplication without ever creating one implicitly."""
    app = QApplication.instance()
    if app is None:
        raise RuntimeError(
            "Admisión requiere una QApplication existente. "
            "El entrypoint standalone debe crearla explícitamente."
        )
    return app


def create_standalone_application(argv=None) -> QApplication:
    """Create QApplication only for the standalone development entrypoint."""
    app = QApplication.instance()
    if app is not None:
        return app
    arguments = list(argv) if argv is not None else (sys.argv[:1] or ["admission"])
    app = QApplication(arguments)
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    return app


def _qt_parent(parent):
    if parent is None:
        return None
    if isinstance(parent, Window):
        return parent._central
    if isinstance(parent, Canvas):
        return parent.viewport()
    return parent


def _normalize_pair(value, default=0):
    if value is None:
        return default, default
    if isinstance(value, (tuple, list)):
        if len(value) == 0:
            return default, default
        if len(value) == 1:
            return int(value[0]), int(value[0])
        return int(value[0]), int(value[1])
    try:
        iv = int(value)
    except Exception:
        iv = default
    return iv, iv


def _font_from(value) -> QFont | None:
    if not value:
        return None
    if isinstance(value, QFont):
        return value
    family = "Arial"
    size = 11
    bold = False
    italic = False
    try:
        if isinstance(value, str):
            parts = value.split()
            if parts:
                family = parts[0]
            if len(parts) > 1 and str(parts[1]).isdigit():
                size = int(parts[1])
            bold = "bold" in value.lower()
            italic = "italic" in value.lower()
        elif isinstance(value, (tuple, list)):
            if len(value) > 0:
                family = str(value[0])
            if len(value) > 1:
                size = int(float(value[1]))
            extras = " ".join(str(x).lower() for x in value[2:])
            bold = "bold" in extras
            italic = "italic" in extras
        f = QFont(family, size)
        f.setBold(bold)
        f.setItalic(italic)
        return f
    except Exception:
        return None


def _anchor_alignment(anchor: str | None):
    a = str(anchor or "").strip().lower()
    align = Qt.AlignmentFlag(0)

    # Tk usa dos familias de valores aquí:
    # - anchor: w/e/n/s/nw/... 
    # - justify: left/right/center
    # La compatibilidad anterior solo reconocía w/e, por lo que justify="left"
    # terminaba centrado en Qt. Esto afectaba headers, subtítulos y mensajes en
    # varias ventanas. Se resuelven ambos vocabularios explícitamente.
    if a in {"left", "l"} or "w" in a:
        align |= Qt.AlignLeft
    elif a in {"right", "r"} or "e" in a:
        align |= Qt.AlignRight
    else:
        align |= Qt.AlignHCenter

    if a in {"top", "t"} or "n" in a:
        align |= Qt.AlignTop
    elif a in {"bottom", "b"} or "s" in a:
        align |= Qt.AlignBottom
    else:
        align |= Qt.AlignVCenter
    return align


def _grid_alignment(sticky: str | None):
    s = str(sticky or "").lower()
    if not s or ("e" in s and "w" in s) or ("n" in s and "s" in s):
        return Qt.AlignmentFlag(0)
    align = Qt.AlignmentFlag(0)
    if "w" in s:
        align |= Qt.AlignLeft
    if "e" in s:
        align |= Qt.AlignRight
    if "n" in s:
        align |= Qt.AlignTop
    if "s" in s:
        align |= Qt.AlignBottom
    return align


def _tk_index(index, text_len: int, cursor_pos: int = 0, sel_start: int | None = None, sel_end: int | None = None):
    if index is None:
        return 0
    if isinstance(index, int):
        return max(0, min(text_len, index))
    s = str(index).lower()
    if s in ("end", END):
        return text_len
    if s in ("insert", INSERT):
        return cursor_pos
    if s == "sel.first":
        return sel_start if sel_start is not None else cursor_pos
    if s == "sel.last":
        return sel_end if sel_end is not None else cursor_pos
    try:
        return max(0, min(text_len, int(float(s))))
    except Exception:
        return 0


class _CompatEvent:
    def __init__(self, widget=None, qevent=None):
        self.widget = widget
        self.x = 0
        self.y = 0
        self.x_root = 0
        self.y_root = 0
        self.delta = 0
        self.width = getattr(widget, "width", lambda: 0)()
        self.height = getattr(widget, "height", lambda: 0)()
        self.keysym = ""
        self.char = ""
        self.state = 0
        if qevent is not None:
            try:
                p = qevent.position().toPoint()
                self.x, self.y = p.x(), p.y()
                gp = qevent.globalPosition().toPoint()
                self.x_root, self.y_root = gp.x(), gp.y()
            except Exception:
                pass
            try:
                self.delta = qevent.angleDelta().y()
            except Exception:
                pass
            try:
                self.width = qevent.size().width()
                self.height = qevent.size().height()
            except Exception:
                pass
            try:
                key = qevent.key()
                self.keysym = {
                    Qt.Key_Return: "Return", Qt.Key_Enter: "Return", Qt.Key_Escape: "Escape",
                    Qt.Key_Down: "Down", Qt.Key_Up: "Up", Qt.Key_Tab: "Tab",
                    Qt.Key_Backtab: "ISO_Left_Tab", Qt.Key_F5: "F5", Qt.Key_F9: "F9",
                }.get(key, qevent.text() or "")
                self.char = qevent.text() or ""
            except Exception:
                pass


class _EventFilter(QObject):
    def __init__(self, owner):
        self.owner = owner
        super().__init__(owner)

    def eventFilter(self, obj, event):
        owner = self.owner
        try:
            event_type = event.type()
            names: list[str] = []
            if event_type == QEvent.StyleChange:
                minimum_height = int(
                    getattr(owner, "_compat_ipady_minimum_height", 0) or 0
                )
                if minimum_height and not getattr(
                    owner, "_compat_minimum_restore_scheduled", False
                ):
                    owner._compat_minimum_restore_scheduled = True

                    def restore_minimum_height(widget=owner, height=minimum_height):
                        widget._compat_minimum_restore_scheduled = False
                        try:
                            widget.setMinimumHeight(max(height, widget.minimumHeight()))
                        except RuntimeError:
                            pass

                    QTimer.singleShot(0, restore_minimum_height)
            if event_type == QEvent.FocusIn:
                names = ["<FocusIn>"]
            elif event_type == QEvent.FocusOut:
                names = ["<FocusOut>"]
            elif event_type == QEvent.Resize:
                names = ["<Configure>"]
            elif event_type == QEvent.Enter:
                names = ["<Enter>"]
            elif event_type == QEvent.Leave:
                names = ["<Leave>"]
            elif event_type == QEvent.Wheel:
                names = ["<MouseWheel>"]
            elif event_type == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    names = ["<Button-1>", "<ButtonRelease-1>"]
                elif event.button() == Qt.RightButton:
                    names = ["<Button-3>"]
            elif event_type == QEvent.MouseButtonDblClick:
                if event.button() == Qt.LeftButton:
                    names = ["<Double-1>"]
            elif event_type in (QEvent.KeyPress, QEvent.KeyRelease):
                mods = event.modifiers()
                key = event.key()
                prefix = "KeyRelease" if event_type == QEvent.KeyRelease else "KeyPress"
                if event_type == QEvent.KeyRelease:
                    names.append("<KeyRelease>")
                key_name = {
                    Qt.Key_Return: "Return", Qt.Key_Enter: "Return", Qt.Key_Escape: "Escape",
                    Qt.Key_Down: "Down", Qt.Key_Up: "Up", Qt.Key_Tab: "Tab",
                    Qt.Key_Backtab: "ISO_Left_Tab", Qt.Key_F5: "F5", Qt.Key_F9: "F9",
                }.get(key)
                if key_name and event_type == QEvent.KeyPress:
                    names.append(f"<{key_name}>")
                if event_type == QEvent.KeyPress and mods & Qt.ControlModifier:
                    txt = (event.text() or "").lower()
                    if key in (Qt.Key_Return, Qt.Key_Enter):
                        names.extend(["<Control-Return>"])
                    elif txt:
                        names.extend([f"<Control-{txt}>", f"<Control-{txt.upper()}>"])
                if event_type == QEvent.KeyPress and mods & Qt.ShiftModifier and key in (Qt.Key_Tab, Qt.Key_Backtab):
                    names.extend(["<Shift-Tab>", "<ISO_Left_Tab>"])

            if names:
                ev = _CompatEvent(owner, event)
                for name in names:
                    callbacks = list(owner._bindings.get(name, []))
                    for cb in callbacks:
                        try:
                            result = cb(ev)
                            if result == "break":
                                return True
                        except TypeError:
                            result = cb()
                            if result == "break":
                                return True
                        except Exception:
                            # La capa de compatibilidad no debe derribar el proceso por un evento visual.
                            pass
        except Exception:
            pass
        return super().eventFilter(obj, event)


class _GuiDispatcher(QObject):
    schedule_requested = Signal(str, int, object)
    cancel_requested = Signal(str)

    def __init__(self):
        super().__init__()
        self._timers: dict[str, QTimer] = {}
        self.schedule_requested.connect(self._schedule)
        self.cancel_requested.connect(self._cancel)

    @Slot(str, int, object)
    def _schedule(self, ident: str, delay: int, callback):
        timer = QTimer(self)
        timer.setSingleShot(True)
        def fire():
            self._timers.pop(ident, None)
            try:
                callback()
            finally:
                timer.deleteLater()
        timer.timeout.connect(fire)
        self._timers[ident] = timer
        timer.start(max(0, int(delay)))

    @Slot(str)
    def _cancel(self, ident: str):
        timer = self._timers.pop(str(ident), None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()


_DISPATCHER: _GuiDispatcher | None = None


def _dispatcher():
    global _DISPATCHER
    _ensure_app()
    if _DISPATCHER is None:
        _DISPATCHER = _GuiDispatcher()
        _DISPATCHER.moveToThread(QApplication.instance().thread())
    return _DISPATCHER


class _Variable(QObject):
    changed = Signal(object)
    _counter = 0

    def __init__(self, value=None):
        super().__init__()
        self._value = value
        self._traces: dict[str, Callable] = {}

    def get(self):
        return self._value

    def set(self, value):
        if self._value == value:
            return
        self._value = value
        self.changed.emit(value)
        for cb in list(self._traces.values()):
            try:
                cb("", "", "write")
            except TypeError:
                try:
                    cb()
                except Exception:
                    pass
            except Exception:
                pass

    def trace_add(self, mode, callback):
        type(self)._counter += 1
        ident = f"trace-{type(self)._counter}"
        self._traces[ident] = callback
        return ident

    def trace_remove(self, mode, cbname):
        self._traces.pop(str(cbname), None)


class StringVar(_Variable):
    def __init__(self, value=""):
        super().__init__("" if value is None else str(value))

    def set(self, value):
        super().set("" if value is None else str(value))


class BooleanVar(_Variable):
    def __init__(self, value=False):
        super().__init__(bool(value))

    def set(self, value):
        super().set(bool(value))


class IntVar(_Variable):
    def __init__(self, value=0):
        super().__init__(int(value or 0))

    def set(self, value):
        try:
            value = int(value)
        except Exception:
            value = 0
        super().set(value)


class DoubleVar(_Variable):
    def __init__(self, value=0.0):
        super().__init__(float(value or 0.0))


class _WidgetMixin:
    _compat_is_widget = True

    def _compat_init(self, parent=None, **kwargs):
        self._compat_parent = parent
        self._compat_children: list[Any] = []
        self._layout_mode = None
        self._compat_layout = None
        self._pending_column_cfg = {}
        self._pending_row_cfg = {}
        self._bindings: dict[str, list[Callable]] = {}
        self._compat_shortcuts: dict[str, QShortcut] = {}
        self._after_ids: set[str] = set()
        self._destroyed = False
        self._compat_options: dict[str, Any] = {}
        self._padding = kwargs.pop("padding", 0)
        self._last_pack_kwargs: dict[str, Any] | None = None
        self._last_grid_kwargs: dict[str, Any] | None = None
        self._pack_bottom_widgets: list[Any] = []
        self._pack_right_widgets: list[Any] = []
        self._style_name = kwargs.get("style", "") or ""
        self._bootstyle = str(kwargs.get("bootstyle", "") or "").lower()
        self._compat_ipady_minimum_height = 0
        self._compat_minimum_restore_scheduled = False
        self._event_filter = _EventFilter(self)
        self.installEventFilter(self._event_filter)
        _STYLE_WIDGETS.add(self)
        if parent is not None and hasattr(parent, "_compat_children"):
            parent._compat_children.append(self)
        # Semántica Tk: un widget hijo recién construido NO es visible hasta
        # que se gestione con pack/grid/place. Qt lo mostraría al mostrarse el
        # padre y eso provocaba controles huérfanos/superpuestos (p. ej. Embarazada).
        if parent is not None:
            try:
                self.hide()
            except Exception:
                pass
        self.configure(**kwargs)

    def _layout_host(self):
        return self

    def _ensure_layout(self, mode: str, side: str | None = None):
        host = self._layout_host()
        if self._compat_layout is not None:
            return self._compat_layout
        self._layout_mode = mode
        if mode == "grid":
            layout = QGridLayout()
        elif mode == "pack_h":
            layout = QHBoxLayout()
        else:
            layout = QVBoxLayout()
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        l, r = _normalize_pair(self._padding, 0)
        if isinstance(self._padding, (tuple, list)) and len(self._padding) >= 2:
            x, y = int(self._padding[0]), int(self._padding[1])
            layout.setContentsMargins(x, y, x, y)
        else:
            p = int(l)
            layout.setContentsMargins(p, p, p, p)
        layout.setSpacing(6)
        host.setLayout(layout)
        self._compat_layout = layout
        if isinstance(layout, QGridLayout):
            for col, cfg in self._pending_column_cfg.items():
                layout.setColumnStretch(int(col), int(cfg.get("weight", 0) or 0))
                if cfg.get("minsize"):
                    layout.setColumnMinimumWidth(int(col), int(cfg["minsize"]))
            for row, cfg in self._pending_row_cfg.items():
                layout.setRowStretch(int(row), int(cfg.get("weight", 0) or 0))
                if cfg.get("minsize"):
                    layout.setRowMinimumHeight(int(row), int(cfg["minsize"]))
        return layout

    def pack(self, **kwargs):
        if not kwargs and self._last_pack_kwargs is not None:
            kwargs = dict(self._last_pack_kwargs)
        else:
            self._last_pack_kwargs = dict(kwargs)
        parent = self._compat_parent
        if parent is None:
            self.show()
            return self
        side = str(kwargs.get("side", "top") or "top").lower()
        mode = "pack_h" if side in ("left", "right") else "pack_v"
        layout = parent._ensure_layout(mode, side) if hasattr(parent, "_ensure_layout") else None
        if layout is None:
            self.show(); return self
        fill = str(kwargs.get("fill", "") or "").lower()
        expand = bool(kwargs.get("expand", False))
        if fill in ("both", "x") or expand:
            hp = QSizePolicy.Expanding
        else:
            hp = QSizePolicy.Preferred
        if fill in ("both", "y") or expand:
            vp = QSizePolicy.Expanding
        else:
            vp = QSizePolicy.Preferred
        try:
            self.setSizePolicy(hp, vp)
        except Exception:
            pass
        ipady = int(kwargs.get("ipady", 0) or 0)
        if ipady:
            minimum_height = max(
                self.minimumHeight(), self.sizeHint().height() + ipady * 2
            )
            self._compat_ipady_minimum_height = max(
                self._compat_ipady_minimum_height, minimum_height
            )
            self.setMinimumHeight(self._compat_ipady_minimum_height)
        stretch = 1 if expand else 0
        alignment = Qt.AlignmentFlag(0)
        anchor = kwargs.get("anchor")
        if anchor:
            alignment = _anchor_alignment(anchor)
        elif side == "right":
            alignment = Qt.AlignRight
        elif side == "left":
            alignment = Qt.AlignLeft
        if isinstance(layout, QVBoxLayout):
            # Tk reserva el borde inferior para widgets packed con side=bottom.
            # Si luego llega contenido side=top, debe insertarse ANTES del footer.
            bottoms = getattr(parent, "_pack_bottom_widgets", [])
            if side == "bottom":
                if self not in bottoms:
                    bottoms.append(self)
                layout.addWidget(self, stretch, alignment)
            else:
                insert_at = max(0, layout.count() - len(bottoms))
                layout.insertWidget(insert_at, self, stretch, alignment)
        elif isinstance(layout, QHBoxLayout):
            # Equivalente para side=right: futuros side=left quedan antes.
            rights = getattr(parent, "_pack_right_widgets", [])
            if side == "right":
                if self not in rights:
                    rights.append(self)
                layout.addWidget(self, stretch, alignment)
            else:
                insert_at = max(0, layout.count() - len(rights))
                layout.insertWidget(insert_at, self, stretch, alignment)
        self.show()
        return self

    def pack_forget(self):
        self.hide()

    def grid(self, **kwargs):
        if not kwargs and self._last_grid_kwargs is not None:
            kwargs = dict(self._last_grid_kwargs)
        else:
            self._last_grid_kwargs = dict(kwargs)
        parent = self._compat_parent
        if parent is None:
            self.show(); return self
        layout = parent._ensure_layout("grid") if hasattr(parent, "_ensure_layout") else None
        if not isinstance(layout, QGridLayout):
            self.show(); return self
        row = int(kwargs.get("row", 0) or 0)
        column = int(kwargs.get("column", 0) or 0)
        rowspan = int(kwargs.get("rowspan", 1) or 1)
        columnspan = int(kwargs.get("columnspan", 1) or 1)
        sticky = kwargs.get("sticky", "")
        fill = str(sticky or "").lower()
        if "e" in fill and "w" in fill:
            hp = QSizePolicy.Expanding
        else:
            hp = self.sizePolicy().horizontalPolicy()
        if "n" in fill and "s" in fill:
            vp = QSizePolicy.Expanding
        else:
            vp = self.sizePolicy().verticalPolicy()
        try:
            self.setSizePolicy(hp, vp)
        except Exception:
            pass
        ipady = int(kwargs.get("ipady", 0) or 0)
        if ipady:
            minimum_height = max(
                self.minimumHeight(), self.sizeHint().height() + ipady * 2
            )
            self._compat_ipady_minimum_height = max(
                self._compat_ipady_minimum_height, minimum_height
            )
            self.setMinimumHeight(self._compat_ipady_minimum_height)
        layout.addWidget(self, row, column, rowspan, columnspan, _grid_alignment(sticky))
        self.show()
        return self

    def grid_remove(self):
        self.hide()

    def grid_forget(self):
        self.hide()

    def grid_configure(self, **kwargs):
        base = dict(self._last_grid_kwargs or {})
        base.update(kwargs)
        self._last_grid_kwargs = base
        return self.grid(**base)

    grid_config = grid_configure

    def grid_propagate(self, flag=None):
        # Qt usa sizeHint/sizePolicy; no existe un equivalente 1:1 necesario aquí.
        return None

    def columnconfigure(self, index, weight=0, minsize=0, **kwargs):
        cfg = {"weight": weight, "minsize": minsize}
        self._pending_column_cfg[int(index)] = cfg
        if isinstance(self._compat_layout, QGridLayout):
            self._compat_layout.setColumnStretch(int(index), int(weight or 0))
            if minsize:
                self._compat_layout.setColumnMinimumWidth(int(index), int(minsize))

    def rowconfigure(self, index, weight=0, minsize=0, **kwargs):
        cfg = {"weight": weight, "minsize": minsize}
        self._pending_row_cfg[int(index)] = cfg
        if isinstance(self._compat_layout, QGridLayout):
            self._compat_layout.setRowStretch(int(index), int(weight or 0))
            if minsize:
                self._compat_layout.setRowMinimumHeight(int(index), int(minsize))

    grid_columnconfigure = columnconfigure
    grid_rowconfigure = rowconfigure

    def bind(self, sequence, func, add=None):
        seq = str(sequence)
        if add == "+":
            self._bindings.setdefault(seq, []).append(func)
        else:
            self._bindings[seq] = [func]

        # En Tk, los bindings de una ventana reciben teclas de sus descendientes.
        # Qt usa shortcuts para reproducir ese comportamiento sin interceptar la lógica.
        if isinstance(self, (Window, Toplevel)):
            normalized = seq.lower().replace("control-", "ctrl+").replace("<", "").replace(">", "")
            keyseq = None
            mapping = {
                "f5": "F5", "f9": "F9", "escape": "Esc",
                "ctrl+return": "Ctrl+Return", "ctrl+l": "Ctrl+L",
                "ctrl+h": "Ctrl+H", "ctrl+z": "Ctrl+Z",
            }
            keyseq = mapping.get(normalized)
            if keyseq and normalized not in self._compat_shortcuts:
                sc = QShortcut(QKeySequence(keyseq), self)
                sc.setContext(Qt.WidgetWithChildrenShortcut)
                sc.activated.connect(lambda nseq=seq: self._emit_virtual(nseq))
                self._compat_shortcuts[normalized] = sc
        return f"bind-{id(func)}"

    def unbind(self, sequence, funcid=None):
        self._bindings.pop(str(sequence), None)

    def bind_all(self, sequence, func, add=None):
        root = self.winfo_toplevel()
        if root is not self and hasattr(root, "bind"):
            return root.bind(sequence, func, add)
        return self.bind(sequence, func, add)

    def unbind_all(self, sequence):
        root = self.winfo_toplevel()
        if hasattr(root, "unbind"):
            root.unbind(sequence)

    def _emit_virtual(self, sequence, event=None):
        ev = event or _CompatEvent(self)
        for cb in list(self._bindings.get(sequence, [])):
            try:
                cb(ev)
            except TypeError:
                cb()
            except Exception:
                pass

    def after(self, ms, callback=None, *args):
        if callback is None or self._destroyed:
            return None
        ident = str(uuid.uuid4())
        self._after_ids.add(ident)
        def invoke():
            self._after_ids.discard(ident)
            if self._destroyed:
                return
            try:
                callback(*args)
            except Exception:
                # Mantiene el comportamiento tolerante de Tk en callbacks diferidos.
                pass
        _dispatcher().schedule_requested.emit(ident, int(ms), invoke)
        return ident

    def after_idle(self, callback=None, *args):
        """Compatibilidad Tk: programa el callback para el próximo ciclo del event loop Qt."""
        if callback is None:
            return None
        return self.after(0, callback, *args)

    def after_cancel(self, ident):
        if ident:
            self._after_ids.discard(str(ident))
            _dispatcher().cancel_requested.emit(str(ident))

    def _cancel_after_callbacks(self, recursive=False):
        for ident in tuple(self._after_ids):
            try:
                _dispatcher().cancel_requested.emit(str(ident))
            except Exception:
                pass
            self._after_ids.discard(str(ident))
        if recursive:
            for child in list(getattr(self, "_compat_children", ())):
                try:
                    child._cancel_after_callbacks(recursive=True)
                except Exception:
                    pass

    def winfo_children(self):
        return [c for c in list(self._compat_children) if not getattr(c, "_destroyed", False)]

    def winfo_toplevel(self):
        obj = self
        seen = set()
        while getattr(obj, "_compat_parent", None) is not None and id(obj) not in seen:
            seen.add(id(obj))
            obj = obj._compat_parent
        return obj

    def winfo_exists(self):
        return not self._destroyed

    def winfo_ismapped(self):
        return self.isVisible()

    def winfo_width(self):
        return int(self.width())

    def winfo_height(self):
        return int(self.height())

    def winfo_screenwidth(self):
        screen = _ensure_app().primaryScreen()
        return int(screen.availableGeometry().width()) if screen else 1280

    def winfo_screenheight(self):
        screen = _ensure_app().primaryScreen()
        return int(screen.availableGeometry().height()) if screen else 720

    def winfo_rootx(self):
        try:
            return int(self.mapToGlobal(QPoint(0, 0)).x())
        except Exception:
            return 0

    def winfo_rooty(self):
        try:
            return int(self.mapToGlobal(QPoint(0, 0)).y())
        except Exception:
            return 0

    def keys(self):
        # Tk expone la lista de opciones configurables. Incluimos las usadas por Admisión.
        return tuple(set(self._compat_options) | {
            "style", "bootstyle", "background", "bg", "foreground", "fg",
            "font", "state", "text", "width", "height", "takefocus"
        })

    def update_idletasks(self):
        _ensure_app().processEvents()

    def update(self):
        _ensure_app().processEvents()

    def focus_set(self):
        try:
            self.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def focus_force(self):
        self.focus_set()
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def lift(self):
        try:
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    def destroy(self):
        if self._destroyed:
            return
        self._cancel_after_callbacks(recursive=True)
        self._destroyed = True
        try:
            self.close()
        except Exception:
            pass
        try:
            self.deleteLater()
        except Exception:
            pass

    def cget(self, option):
        key = str(option)
        if key == "text" and hasattr(self, "text"):
            try: return self.text()
            except Exception: return self._compat_options.get("text", "")
        if key in ("background", "bg"):
            return self._compat_options.get("background", self._compat_options.get("bg", ""))
        if key in ("foreground", "fg"):
            return self._compat_options.get("foreground", self._compat_options.get("fg", ""))
        if key in self._compat_options:
            return self._compat_options[key]
        if key == "state":
            return NORMAL if self.isEnabled() else DISABLED
        raise KeyError(option)

    def config(self, **kwargs):
        return self.configure(**kwargs)

    def configure(self, **kwargs):
        if not kwargs:
            return dict(self._compat_options)
        self._compat_options.update(kwargs)
        if "style" in kwargs:
            self._style_name = str(kwargs.get("style") or "")
        if "bootstyle" in kwargs:
            self._bootstyle = str(kwargs.get("bootstyle") or "").lower()
        if "state" in kwargs:
            state = str(kwargs.get("state") or NORMAL).lower()
            self.setEnabled(state not in ("disabled", DISABLED))
        if "takefocus" in kwargs:
            try:
                self.setFocusPolicy(Qt.StrongFocus if kwargs["takefocus"] else Qt.NoFocus)
            except Exception:
                pass
        if "cursor" in kwargs:
            if str(kwargs["cursor"]).lower() in ("hand2", "hand"):
                self.setCursor(Qt.PointingHandCursor)
        if "padding" in kwargs:
            self._padding = kwargs["padding"]
            if self._compat_layout is not None:
                try:
                    if isinstance(self._padding, (tuple, list)) and len(self._padding) >= 2:
                        x, y = int(self._padding[0]), int(self._padding[1])
                        self._compat_layout.setContentsMargins(x, y, x, y)
                    else:
                        p = int(self._padding or 0)
                        self._compat_layout.setContentsMargins(p, p, p, p)
                except Exception:
                    pass
        if "width" in kwargs:
            try:
                width = int(kwargs["width"])
                if isinstance(self, (Entry, Combobox, Button, Menubutton)):
                    self.setMinimumWidth(max(40, width * 8))
            except Exception:
                pass
        if "height" in kwargs:
            try:
                h = int(kwargs["height"])
                self.setMinimumHeight(max(self.minimumHeight(), h * 22))
            except Exception:
                pass
        self._apply_compat_style()
        return self

    def _style_values(self):
        style = {}
        if self._style_name and self._style_name in _STYLE_REGISTRY:
            style.update(_STYLE_REGISTRY[self._style_name])
        # ttkbootstrap usa <bootstyle>.TButton internamente.
        if self._bootstyle and isinstance(self, (Button, Menubutton)):
            for key in (f"{self._bootstyle}.TButton", f"{self._bootstyle}.TLabel"):
                if key in _STYLE_REGISTRY:
                    style.update(_STYLE_REGISTRY[key])
        style.update(self._compat_options)
        return style

    def _apply_compat_style(self):
        values = self._style_values()
        transparent_bg = bool(values.get("_qt_transparent", False))
        bg = None if transparent_bg else values.get("background", values.get("bg", values.get("fieldbackground")))
        fg = values.get("foreground", values.get("fg"))
        border = None if transparent_bg else (values.get("bordercolor") or values.get("highlightbackground"))
        font = _font_from(values.get("font"))
        if font is not None:
            try: self.setFont(font)
            except Exception: pass
        qss = []
        if transparent_bg:
            qss.append("background-color: transparent; border: none;")
        elif bg:
            qss.append(f"background-color: {bg};")
        if fg:
            qss.append(f"color: {fg};")
        if border:
            qss.append(f"border: 1px solid {border};")
        elif isinstance(self, (Entry, Combobox)):
            qss.append("border: 1px solid #203348;")
        if isinstance(self, (Button, Menubutton)):
            color = _BOOT_COLORS.get(self._bootstyle)
            if color and not bg:
                qss.append(f"background-color: {color};")
            qss.append("padding: 6px 10px; border-radius: 4px;")
            if not fg:
                qss.append("color: #FFFFFF;")
            control = "QPushButton"
            hover_bg = values.get("hoverbackground", bg or color)
            pressed_bg = values.get("pressedbackground", hover_bg)
            disabled_bg = values.get("disabled_background", bg or color)
            disabled_fg = values.get("disabled_foreground", fg or "#FFFFFF")
            focus_border = values.get("focus_border", border)
            if hover_bg:
                qss.append(f"{control}:hover{{background-color:{hover_bg};}}")
            if pressed_bg:
                qss.append(f"{control}:pressed{{background-color:{pressed_bg};}}")
            if disabled_bg:
                qss.append(
                    f"{control}:disabled{{background-color:{disabled_bg};"
                    f"color:{disabled_fg};border:1px solid {border or focus_border};}}"
                )
            if focus_border:
                qss.append(f"{control}:focus{{border:2px solid {focus_border};}}")
        elif isinstance(self, (Entry, Combobox)):
            qss.append("padding: 5px 7px; border-radius: 3px;")
            sel_bg = values.get("selectbackground")
            sel_fg = values.get("selectforeground")
            if sel_bg: qss.append(f"selection-background-color: {sel_bg};")
            if sel_fg: qss.append(f"selection-color: {sel_fg};")
            hover_bg = values.get("hoverbackground")
            disabled_bg = values.get("disabled_background")
            focus_border = values.get("focus_border")
            control = "QComboBox" if isinstance(self, Combobox) else "QLineEdit"
            if hover_bg:
                qss.append(f"{control}:hover{{background-color:{hover_bg};}}")
            if disabled_bg:
                qss.append(f"{control}:disabled{{background-color:{disabled_bg};}}")
            if focus_border:
                qss.append(f"{control}:focus{{border:2px solid {focus_border};}}")
        if isinstance(self, Checkbutton) and self._bootstyle in _BOOT_COLORS:
            qss.append(
                f"QCheckBox::indicator:checked{{background:{_BOOT_COLORS[self._bootstyle]};border:1px solid {_BOOT_COLORS[self._bootstyle]};}}"
            )
        if qss:
            try: self.setStyleSheet(" ".join(qss))
            except Exception: pass


class Style:
    def __init__(self, *args, **kwargs):
        _ensure_app()

    def configure(self, style_name, **kwargs):
        _STYLE_REGISTRY.setdefault(str(style_name), {}).update(kwargs)
        for w in list(_STYLE_WIDGETS):
            try:
                if getattr(w, "_style_name", "") == style_name or (
                    getattr(w, "_bootstyle", "") and f"{getattr(w, '_bootstyle')}.TButton" == style_name
                ):
                    w._apply_compat_style()
                elif isinstance(w, Treeview) and style_name in ("Treeview", "Modern.Treeview", "Treeview.Heading", "Modern.Treeview.Heading"):
                    w._apply_compat_style()
                elif isinstance(w, Notebook) and str(style_name).startswith("TNotebook"):
                    w._apply_compat_style()
            except Exception:
                pass

    def map(self, style_name, **kwargs):
        _STYLE_REGISTRY.setdefault(str(style_name), {})["__map__"] = kwargs
        self.configure(style_name)


class Window(QMainWindow, _WidgetMixin):
    def __init__(self, themename=None, *, owns_application_loop=False, **kwargs):
        _ensure_app()
        QMainWindow.__init__(self)
        self._central = QWidget(self)
        self._central.setMinimumSize(0, 0)
        self._central.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setCentralWidget(self._central)
        self._close_callback = None
        self._owns_application_loop = bool(owns_application_loop)
        self._compat_init(None, **kwargs)
        self._compat_children = []

    def _layout_host(self):
        return self._central

    def title(self, text=None):
        if text is None:
            return self.windowTitle()
        self.setWindowTitle(str(text))

    def geometry(self, spec=None):
        if spec is None:
            g = QWidget.geometry(self)
            return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
        m = re.match(r"\s*(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?", str(spec))
        if m:
            w, h = int(m.group(1)), int(m.group(2))
            self.resize(w, h)
            if m.group(3) is not None:
                self.move(int(m.group(3)), int(m.group(4)))

    def minsize(self, w, h):
        self.setMinimumSize(int(w), int(h))

    def resizable(self, x=True, y=True):
        x = bool(x); y = bool(y)
        self._compat_resizable = (x, y)
        if not x and not y:
            # No fijar el tamaño actual antes de que Qt calcule el layout.
            try:
                self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)
            except Exception:
                pass
            return
        try:
            self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, False)
        except Exception:
            pass
        size = self.size()
        if not x:
            self.setFixedWidth(size.width())
        else:
            self.setMinimumWidth(0); self.setMaximumWidth(16777215)
        if not y:
            self.setFixedHeight(size.height())
        else:
            self.setMinimumHeight(0); self.setMaximumHeight(16777215)

    def protocol(self, name, callback):
        if str(name) == "WM_DELETE_WINDOW":
            self._close_callback = callback

    def closeEvent(self, event: QCloseEvent):
        if getattr(self, "_in_close_callback", False):
            event.accept()
            return
        if self._close_callback:
            try:
                self._in_close_callback = True
                self._close_callback()
                event.accept()
                return
            except Exception:
                pass
            finally:
                self._in_close_callback = False
        event.accept()

    def configure(self, **kwargs):
        return _WidgetMixin.configure(self, **kwargs)

    config = configure

    def option_add(self, *args, **kwargs):
        return None

    def state(self):
        return "normal" if self.isVisible() else "withdrawn"

    def register(self, func):
        return func

    def mainloop(self):
        if not self._owns_application_loop:
            raise RuntimeError(
                "mainloop() solo está permitido en el wrapper standalone de Admisión."
            )
        self.show()
        return _ensure_app().exec()

    def clipboard_clear(self):
        _ensure_app().clipboard().clear()

    def clipboard_append(self, text):
        _ensure_app().clipboard().setText(str(text or ""))

    def clipboard_get(self):
        return _ensure_app().clipboard().text()

    def focus_get(self):
        return _ensure_app().focusWidget()

    def focus_displayof(self):
        widget = _ensure_app().focusWidget()
        return widget if widget is not None and widget.window() is self else None

    def bell(self):
        try:
            _ensure_app().beep()
        except Exception:
            pass


class EmbeddedWindowRoot(QWidget, _WidgetMixin):
    """Widget host that exposes the root API used by the real V15 interface."""

    def __init__(self, parent=None, themename=None, **kwargs):
        _ensure_app()
        QWidget.__init__(self, parent)
        # Replica la carcasa central de Window(QMainWindow): el contenido V15
        # se compone dentro de una superficie central, no directamente sobre el
        # widget anfitrión de la aplicación principal.
        self._central = QWidget(self)
        host_layout = QVBoxLayout(self)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(0)
        host_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        host_layout.addWidget(self._central)
        self._close_callback = None
        self._embedded_title = ""
        self._compat_init(None, **kwargs)
        self._compat_children = []
        self._owned_toplevels = set()
        self._suspended_toplevels = set()
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def minimumSizeHint(self):
        """Do not propagate standalone content width to the host MainWindow."""
        return QSize(0, 0)

    def _layout_host(self):
        return self._central

    def close_owned_toplevels(self):
        """Close every V15 secondary window without touching QApplication."""
        for window in tuple(self._owned_toplevels):
            try:
                window.destroy()
            except Exception:
                try:
                    window.close()
                except Exception:
                    pass
        self._owned_toplevels.clear()
        self._suspended_toplevels.clear()

    def suspend_owned_toplevels(self):
        """Hide V15 dialogs while another main module owns the viewport."""
        self._suspended_toplevels.clear()
        for window in tuple(self._owned_toplevels):
            try:
                if window.isVisible():
                    self._suspended_toplevels.add(window)
                    window.hide()
            except RuntimeError:
                self._owned_toplevels.discard(window)

    def resume_owned_toplevels(self):
        """Restore only dialogs that were visible before leaving Admisión."""
        for window in tuple(self._suspended_toplevels):
            try:
                window.show()
                window.raise_()
            except RuntimeError:
                self._owned_toplevels.discard(window)
        self._suspended_toplevels.clear()

    def destroy(self):
        self.close_owned_toplevels()
        _WidgetMixin.destroy(self)

    def title(self, text=None):
        if text is None:
            return self._embedded_title
        self._embedded_title = str(text)

    def geometry(self, spec=None):
        if spec is None:
            g = QWidget.geometry(self)
            return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
        match = re.match(r"\s*(\d+)x(\d+)", str(spec))
        if match and self.parentWidget() is None:
            self.resize(int(match.group(1)), int(match.group(2)))

    def minsize(self, w, h):
        if self.parentWidget() is None:
            self.setMinimumSize(int(w), int(h))

    def resizable(self, x=True, y=True):
        self._compat_resizable = (bool(x), bool(y))

    def protocol(self, name, callback):
        if str(name) == "WM_DELETE_WINDOW":
            self._close_callback = callback

    def closeEvent(self, event: QCloseEvent):
        if getattr(self, "_in_close_callback", False):
            event.accept()
            return
        if self._close_callback:
            try:
                self._in_close_callback = True
                self._close_callback()
                event.accept()
                return
            except Exception:
                pass
            finally:
                self._in_close_callback = False
        event.accept()

    def configure(self, **kwargs):
        result = _WidgetMixin.configure(self, **kwargs)
        # QMainWindow propaga el fondo al área central; QWidget no lo hace de
        # forma consistente al estar embebido. Se mantiene la misma paleta V15.
        bg = kwargs.get("background", kwargs.get("bg"))
        if bg:
            try:
                self._central.setStyleSheet(f"background-color: {bg};")
            except Exception:
                pass
        return result

    config = configure

    def option_add(self, *args, **kwargs):
        return None

    def state(self):
        return "normal" if self.isVisible() else "withdrawn"

    def register(self, func):
        return func

    def mainloop(self):
        raise RuntimeError("AdmissionWidget no controla el ciclo de eventos Qt.")

    def clipboard_clear(self):
        _ensure_app().clipboard().clear()

    def clipboard_append(self, text):
        _ensure_app().clipboard().setText(str(text or ""))

    def clipboard_get(self):
        return _ensure_app().clipboard().text()

    def focus_get(self):
        return _ensure_app().focusWidget()

    def focus_displayof(self):
        widget = _ensure_app().focusWidget()
        current = widget
        while current is not None:
            if current is self:
                return widget
            current = current.parentWidget()
        return None

    def bell(self):
        try:
            _ensure_app().beep()
        except Exception:
            pass


class Toplevel(QDialog, _WidgetMixin):
    def __init__(self, parent=None, **kwargs):
        _ensure_app()
        qt_parent = parent if isinstance(parent, QMainWindow) else _qt_parent(parent)
        QDialog.__init__(self, qt_parent)
        self._return_focus_widget = _ensure_app().focusWidget()
        self._close_callback = None
        self.setModal(False)
        self._compat_init(parent, **kwargs)
        owner = parent.winfo_toplevel() if hasattr(parent, "winfo_toplevel") else parent
        self._owner_root = owner if hasattr(owner, "_owned_toplevels") else None
        if self._owner_root is not None:
            self._owner_root._owned_toplevels.add(self)
        # A dialog may be created from another dialog instead of from the
        # embedded root.  Walk the lightweight compatibility ownership chain
        # so nested windows still receive the single host theme before they
        # draw their first frame.
        self._admission_theme_applier = self._resolve_admission_theme_applier(owner, parent)
        # Tkinter muestra Toplevel al crearlo; reproducimos esa semántica.
        def _polish_and_show():
            if self._destroyed:
                return
            apply_theme = self._admission_theme_applier
            if callable(apply_theme):
                try:
                    apply_theme(self)
                except Exception:
                    # A theme failure must never prevent the functional dialog
                    # from opening.  The owner logs detailed failures itself.
                    pass
            self.show()

        QTimer.singleShot(0, _polish_and_show)

    @staticmethod
    def _resolve_admission_theme_applier(*candidates):
        """Find the root theme hook without coupling dialogs to App V15."""
        pending = [candidate for candidate in candidates if candidate is not None]
        visited = set()
        while pending:
            candidate = pending.pop(0)
            candidate_id = id(candidate)
            if candidate_id in visited:
                continue
            visited.add(candidate_id)
            applier = getattr(candidate, "_admission_theme_applier", None)
            if callable(applier):
                return applier
            for attr in ("_owner_root", "_compat_parent", "parent"):
                related = getattr(candidate, attr, None)
                if callable(related):
                    try:
                        related = related()
                    except Exception:
                        related = None
                if related is not None:
                    pending.append(related)
        return None

    def title(self, text=None):
        if text is None: return self.windowTitle()
        self.setWindowTitle(str(text))

    def geometry(self, spec=None):
        if spec is None:
            g = super().geometry()
            return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
        txt = str(spec).strip()
        m = re.match(r"(\d+)x(\d+)(?:\+(-?\d+)\+(-?\d+))?", txt)
        if m:
            self.resize(int(m.group(1)), int(m.group(2)))
            if m.group(3) is not None:
                self.move(int(m.group(3)), int(m.group(4)))
            return
        pos = re.match(r"\+(-?\d+)\+(-?\d+)", txt)
        if pos:
            self.move(int(pos.group(1)), int(pos.group(2)))

    def minsize(self, w, h): self.setMinimumSize(int(w), int(h))
    def resizable(self, x=True, y=True):
        x = bool(x); y = bool(y)
        self._compat_resizable = (x, y)
        if not x and not y:
            # No fijar el tamaño actual antes de que Qt calcule el layout.
            try:
                self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, True)
            except Exception:
                pass
            return
        try:
            self.setWindowFlag(Qt.MSWindowsFixedSizeDialogHint, False)
        except Exception:
            pass
        size = self.size()
        if not x:
            self.setFixedWidth(size.width())
        else:
            self.setMinimumWidth(0); self.setMaximumWidth(16777215)
        if not y:
            self.setFixedHeight(size.height())
        else:
            self.setMinimumHeight(0); self.setMaximumHeight(16777215)

    def protocol(self, name, callback):
        if str(name) == "WM_DELETE_WINDOW": self._close_callback = callback

    def closeEvent(self, event):
        if self._owner_root is not None:
            self._owner_root._owned_toplevels.discard(self)
            self._owner_root._suspended_toplevels.discard(self)
        if getattr(self, "_in_close_callback", False):
            event.accept()
            self._restore_owner_focus()
            return
        if self._close_callback:
            try:
                self._in_close_callback = True
                self._close_callback()
                event.accept()
                self._restore_owner_focus()
                return
            except Exception:
                pass
            finally:
                self._in_close_callback = False
        event.accept()
        self._restore_owner_focus()

    def _restore_owner_focus(self):
        target = self._return_focus_widget
        if target is None or getattr(self._owner_root, "_destroyed", False):
            return
        try:
            owner_window = self._owner_root.window()
            if owner_window is not None:
                owner_window.activateWindow()
            if target.isVisible() and target.isEnabled():
                target.setFocus(Qt.OtherFocusReason)
        except (AttributeError, RuntimeError):
            pass

    def transient(self, parent=None):
        try:
            p = parent or self._compat_parent
            dialog_parent = _qt_parent(p)
            if isinstance(dialog_parent, QWidget):
                self.setParent(dialog_parent, Qt.Dialog)
        except Exception:
            pass

    def grab_set(self): self.setModal(True)
    def grab_release(self): self.setModal(False)
    def wait_window(self, window=None):
        target = window if isinstance(window, QDialog) else self
        if target.isVisible():
            target.exec()
    def withdraw(self): self.hide()
    def deiconify(self): self.show()


class Frame(QWidget, _WidgetMixin):
    def __init__(self, parent=None, **kwargs):
        kwargs.setdefault("style", "TFrame")
        QWidget.__init__(self, _qt_parent(parent))
        self._compat_init(parent, **kwargs)


class LabelFrame(QGroupBox, _WidgetMixin):
    def __init__(self, parent=None, text="", **kwargs):
        QGroupBox.__init__(self, str(text or ""), _qt_parent(parent))
        kwargs.setdefault("text", text)
        self._compat_init(parent, **kwargs)


class Label(QLabel, _WidgetMixin):
    def __init__(self, parent=None, text="", textvariable=None, image=None, **kwargs):
        kwargs.setdefault("style", "TLabel")
        QLabel.__init__(self, _qt_parent(parent))
        self._textvariable = textvariable
        self._image_ref = image
        if image is not None:
            pix = image.pixmap if isinstance(image, PhotoImage) else image
            if isinstance(pix, QPixmap): self.setPixmap(pix)
        else:
            self.setText(str(text or ""))
        if textvariable is not None:
            self.setText(str(textvariable.get() or ""))
            textvariable.changed.connect(lambda v: self.setText(str(v or "")))
        self._compat_init(parent, text=text, textvariable=textvariable, image=image, **kwargs)

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.setText(str(kwargs["text"] or ""))
        if "textvariable" in kwargs and kwargs["textvariable"] is not None:
            var = kwargs["textvariable"]
            self._textvariable = var
            self.setText(str(var.get() or ""))
            var.changed.connect(lambda v: self.setText(str(v or "")))
        if "image" in kwargs and kwargs["image"] is not None:
            img = kwargs["image"]
            pix = img.pixmap if isinstance(img, PhotoImage) else img
            if isinstance(pix, QPixmap): self.setPixmap(pix)
        if "wraplength" in kwargs:
            self.setWordWrap(True)
            try: self.setMaximumWidth(int(kwargs["wraplength"]))
            except Exception: pass
        if "justify" in kwargs:
            self.setAlignment(_anchor_alignment(kwargs["justify"]))
        if "anchor" in kwargs:
            self.setAlignment(_anchor_alignment(kwargs["anchor"]))
        return _WidgetMixin.configure(self, **kwargs)

    config = configure


class Button(QPushButton, _WidgetMixin):
    def __init__(self, parent=None, text="", command=None, **kwargs):
        kwargs.setdefault("style", "TButton")
        QPushButton.__init__(self, str(text or ""), _qt_parent(parent))
        self._command = command
        self._clicked_handler = None
        if command is not None:
            self._connect_command_handler()
        self._compat_init(parent, text=text, command=command, **kwargs)

    def _connect_command_handler(self):
        def _handler(_checked=False):
            if self._command is not None:
                return self._command()
        self._clicked_handler = _handler
        self.clicked.connect(_handler)

    def configure(self, **kwargs):
        if "text" in kwargs: self.setText(str(kwargs["text"] or ""))
        if "command" in kwargs and kwargs["command"] is not None:
            if self._clicked_handler is not None:
                try:
                    self.clicked.disconnect(self._clicked_handler)
                except (RuntimeError, TypeError):
                    pass
            self._command = kwargs["command"]
            self._connect_command_handler()
        return _WidgetMixin.configure(self, **kwargs)
    config = configure
    def invoke(self):
        if self._command: return self._command()


class Entry(QLineEdit, _WidgetMixin):
    def __init__(self, parent=None, textvariable=None, validate=None, validatecommand=None, show=None, **kwargs):
        kwargs.setdefault("style", "TEntry")
        QLineEdit.__init__(self, _qt_parent(parent))
        self._textvariable = textvariable
        self._validate = validate
        self._validatecommand = validatecommand
        self._updating_var = False
        if show:
            self.setEchoMode(QLineEdit.Password)
        if textvariable is not None:
            self.setText(str(textvariable.get() or ""))
            textvariable.changed.connect(self._from_var)
            self.textChanged.connect(self._to_var)
        if validate == "key" and validatecommand:
            self.textEdited.connect(self._validate_text)
        self._last_valid = self.text()
        self._compat_init(parent, textvariable=textvariable, validate=validate, validatecommand=validatecommand, show=show, **kwargs)

    def _from_var(self, value):
        if self._updating_var: return
        if self.text() != str(value or ""):
            with QSignalBlocker(self):
                self.setText(str(value or ""))

    def _to_var(self, text):
        if self._textvariable is not None:
            self._updating_var = True
            try: self._textvariable.set(text)
            finally: self._updating_var = False

    def _validate_text(self, text):
        fn = self._validatecommand[0] if isinstance(self._validatecommand, (tuple, list)) else self._validatecommand
        try:
            ok = bool(fn(text))
        except TypeError:
            try: ok = bool(fn(text, ""))
            except Exception: ok = True
        except Exception:
            ok = True
        if ok:
            self._last_valid = text
        else:
            with QSignalBlocker(self):
                self.setText(self._last_valid)
            self._to_var(self._last_valid)

    def get(self): return self.text()
    def delete(self, first, last=None):
        txt = self.text()
        ss = self.selectionStart()
        se = ss + len(self.selectedText()) if ss >= 0 else None
        a = _tk_index(first, len(txt), self.cursorPosition(), ss if ss >= 0 else None, se)
        b = _tk_index(last if last is not None else a + 1, len(txt), self.cursorPosition(), ss if ss >= 0 else None, se)
        if b < a: a, b = b, a
        self.setText(txt[:a] + txt[b:])
        self.setCursorPosition(a)
        self._to_var(self.text())
    def insert(self, index, string):
        txt = self.text()
        ss = self.selectionStart(); se = ss + len(self.selectedText()) if ss >= 0 else None
        pos = _tk_index(index, len(txt), self.cursorPosition(), ss if ss >= 0 else None, se)
        self.setText(txt[:pos] + str(string or "") + txt[pos:])
        self.setCursorPosition(pos + len(str(string or "")))
        self._to_var(self.text())
    def icursor(self, index): self.setCursorPosition(_tk_index(index, len(self.text()), self.cursorPosition()))
    def index(self, index):
        ss = self.selectionStart(); se = ss + len(self.selectedText()) if ss >= 0 else None
        return _tk_index(index, len(self.text()), self.cursorPosition(), ss if ss >= 0 else None, se)
    def selection_get(self):
        if not self.hasSelectedText(): raise RuntimeError("no selection")
        return self.selectedText()
    def selection_range(self, start, end):
        s = _tk_index(start, len(self.text()), self.cursorPosition())
        e = _tk_index(end, len(self.text()), self.cursorPosition())
        self.setSelection(s, max(0, e - s))
    select_range = selection_range
    def selection_present(self): return self.hasSelectedText()

    def configure(self, **kwargs):
        if "show" in kwargs:
            self.setEchoMode(QLineEdit.Password if kwargs["show"] else QLineEdit.Normal)
        return _WidgetMixin.configure(self, **kwargs)
    config = configure


class Text(QTextEdit, _WidgetMixin):
    def __init__(self, parent=None, **kwargs):
        QTextEdit.__init__(self, _qt_parent(parent))
        self._compat_init(parent, **kwargs)
    def get(self, start="1.0", end="end"): return self.toPlainText()
    def delete(self, start="1.0", end="end"): self.clear()
    def insert(self, index, text): self.insertPlainText(str(text or ""))
    def selection_get(self): return self.textCursor().selectedText()


class Combobox(QComboBox, _WidgetMixin):
    def __init__(self, parent=None, textvariable=None, values=(), state="normal", **kwargs):
        kwargs.setdefault("style", "TCombobox")
        QComboBox.__init__(self, _qt_parent(parent))
        self._textvariable = textvariable
        self.addItems([str(v) for v in (values or [])])
        self.setEditable(str(state).lower() != "readonly")
        if textvariable is not None:
            self.set(str(textvariable.get() or ""))
            textvariable.changed.connect(self.set)
            self.currentTextChanged.connect(lambda t: textvariable.set(t))
        self.currentIndexChanged.connect(lambda _i: self._emit_virtual("<<ComboboxSelected>>"))
        self._compat_init(parent, textvariable=textvariable, values=values, state=state, **kwargs)

    def get(self): return self.currentText()
    def set(self, value):
        val = str(value or "")
        idx = self.findText(val)
        if idx >= 0:
            self.setCurrentIndex(idx)
        elif self.isEditable():
            self.setEditText(val)
        else:
            # ttk readonly puede mantener una variable sin elemento; Qt no. Añadimos temporalmente.
            if val:
                self.addItem(val); self.setCurrentIndex(self.count() - 1)
        if self._textvariable is not None and self._textvariable.get() != val:
            self._textvariable.set(val)
    def current(self, index=None):
        if index is None: return self.currentIndex()
        self.setCurrentIndex(int(index))
    def configure(self, **kwargs):
        if "values" in kwargs:
            cur = self.currentText()
            with QSignalBlocker(self):
                self.clear(); self.addItems([str(v) for v in kwargs.get("values") or []])
                idx = self.findText(cur)
                if idx >= 0: self.setCurrentIndex(idx)
        if "state" in kwargs:
            st = str(kwargs["state"]).lower()
            self.setEnabled(st != "disabled")
            self.setEditable(st not in ("readonly", "disabled"))
        return _WidgetMixin.configure(self, **kwargs)
    config = configure
    def _apply_compat_style(self):
        values=self._style_values()
        bg=values.get("fieldbackground",values.get("background","#111E2E"))
        fg=values.get("foreground","#F5F9FF")
        border=values.get("bordercolor","#203348")
        hover_bg=values.get("hoverbackground", bg)
        disabled_bg=values.get("disabled_background", bg)
        focus_border=values.get("focus_border", border)
        sel_bg="#1D6EFF"; sel_fg="#FFFFFF"
        maps=values.get("__map__",{})
        try:
            pairs=maps.get("selectbackground",[])
            if pairs: sel_bg=pairs[0][1]
            pairs=maps.get("selectforeground",[])
            if pairs: sel_fg=pairs[0][1]
        except Exception: pass
        self._compat_popup_bg = bg
        self._compat_popup_fg = fg
        self._compat_popup_border = border
        self._compat_popup_sel_bg = sel_bg
        self._compat_popup_sel_fg = sel_fg
        self.setStyleSheet(
            f"QComboBox{{background:{bg};color:{fg};border:1px solid {border};padding:5px 7px;border-radius:3px;}}"
            f"QComboBox:hover{{background:{hover_bg};}}"
            f"QComboBox:focus{{border:2px solid {focus_border};}}"
            f"QComboBox:disabled{{background:{disabled_bg};}}"
            f"QComboBox QAbstractItemView{{background:{bg};color:{fg};border:0;margin:0;padding:0;outline:0;"
            f"selection-background-color:{sel_bg};selection-color:{sel_fg};}}"
        )
        try:
            view=self.view()
            view.setFrameShape(QFrame.NoFrame)
            view.setContentsMargins(0,0,0,0)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.viewport().setStyleSheet(f"background-color:{bg};")
        except Exception:
            pass
        font=_font_from(values.get("font"))
        if font: self.setFont(font)

    def showPopup(self):
        QComboBox.showPopup(self)
        # El popup de QComboBox vive en un contenedor nativo separado. Si ese
        # contenedor no se estiliza, Windows deja una franja gris bajo la lista.
        try:
            bg=getattr(self,"_compat_popup_bg","#111E2E")
            fg=getattr(self,"_compat_popup_fg","#F5F9FF")
            border=getattr(self,"_compat_popup_border","#203348")
            sel_bg=getattr(self,"_compat_popup_sel_bg","#1D6EFF")
            sel_fg=getattr(self,"_compat_popup_sel_fg","#FFFFFF")
            view=self.view()
            view.setFrameShape(QFrame.NoFrame)
            view.setContentsMargins(0,0,0,0)
            view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            view.viewport().setStyleSheet(f"background-color:{bg};border:0;margin:0;padding:0;")
            popup=view.window()
            popup.setContentsMargins(0,0,0,0)
            popup.setStyleSheet(
                f"QFrame{{background-color:{bg};border:1px solid {border};margin:0;padding:0;}}"
                f"QAbstractItemView{{background-color:{bg};color:{fg};border:0;margin:0;padding:0;outline:0;"
                f"selection-background-color:{sel_bg};selection-color:{sel_fg};}}"
                "QScrollBar:horizontal{height:0px;}"
            )
        except Exception:
            pass


class Checkbutton(QCheckBox, _WidgetMixin):
    def __init__(self, parent=None, text="", variable=None, command=None, **kwargs):
        kwargs.setdefault("style", "TCheckbutton")
        QCheckBox.__init__(self, str(text or ""), _qt_parent(parent))
        self._variable = variable
        self._command = command
        if variable is not None:
            self.setChecked(bool(variable.get()))
            variable.changed.connect(lambda v: self.setChecked(bool(v)))
        self.toggled.connect(self._on_toggle)
        self._compat_init(parent, text=text, variable=variable, command=command, **kwargs)
    def _on_toggle(self, checked):
        if self._variable is not None and bool(self._variable.get()) != bool(checked): self._variable.set(bool(checked))
        if self._command is not None:
            try: self._command()
            except Exception: pass
    def configure(self, **kwargs):
        if "text" in kwargs: self.setText(str(kwargs["text"] or ""))
        return _WidgetMixin.configure(self, **kwargs)
    config = configure

    def _apply_compat_style(self):
        values = self._style_values()
        self.setStyleSheet(_choice_control_qss(values, radio=False))
        font = _font_from(values.get("font"))
        if font is not None:
            self.setFont(font)


class Radiobutton(QRadioButton, _WidgetMixin):
    def __init__(self, parent=None, text="", variable=None, value=None, command=None, **kwargs):
        kwargs.setdefault("style", "TRadiobutton")
        QRadioButton.__init__(self, str(text or ""), _qt_parent(parent))
        self._variable, self._value, self._command = variable, value, command
        if variable is not None:
            self.setChecked(variable.get() == value)
            variable.changed.connect(lambda v: self.setChecked(v == self._value))
        self.toggled.connect(self._on_toggle)
        self._compat_init(parent, text=text, variable=variable, value=value, command=command, **kwargs)
    def _on_toggle(self, checked):
        if checked and self._variable is not None: self._variable.set(self._value)
        if checked and self._command is not None:
            try: self._command()
            except Exception: pass

    def _apply_compat_style(self):
        values = self._style_values()
        self.setStyleSheet(_choice_control_qss(values, radio=True))
        font = _font_from(values.get("font"))
        if font is not None:
            self.setFont(font)


class Menubutton(QPushButton, _WidgetMixin):
    def __init__(self, parent=None, text="", textvariable=None, **kwargs):
        kwargs.setdefault("style", "TButton")
        initial = str(textvariable.get() if textvariable is not None else (text or ""))
        QPushButton.__init__(self, initial, _qt_parent(parent))
        self._menu_wrapper = None
        self._textvariable = textvariable
        if textvariable is not None:
            textvariable.changed.connect(lambda v: self.setText(str(v or "")))
        self._compat_init(parent, text=text, textvariable=textvariable, **kwargs)
    def configure(self, **kwargs):
        if "text" in kwargs: self.setText(str(kwargs["text"] or ""))
        if "textvariable" in kwargs and kwargs["textvariable"] is not None:
            self._textvariable = kwargs["textvariable"]
            self.setText(str(self._textvariable.get() or ""))
            self._textvariable.changed.connect(lambda v: self.setText(str(v or "")))
        if "menu" in kwargs:
            menu = kwargs["menu"]
            self._menu_wrapper = menu
            self.setMenu(menu if isinstance(menu, QMenu) else getattr(menu, "_menu", None))
        return _WidgetMixin.configure(self, **kwargs)
    config = configure


class Menu(QMenu):
    def __init__(self, parent=None, tearoff=0, **kwargs):
        super().__init__(_qt_parent(parent))
        self._compat_parent = parent
        self._actions: list[QAction] = []
        bg = kwargs.get("bg") or kwargs.get("background")
        fg = kwargs.get("fg") or kwargs.get("foreground")
        abg = kwargs.get("activebackground")
        afg = kwargs.get("activeforeground")
        qss=[]
        if bg: qss.append(f"QMenu{{background:{bg};}}")
        if fg: qss.append(f"QMenu{{color:{fg};}}")
        if abg or afg:
            qss.append(f"QMenu::item:selected{{background:{abg or bg or '#1D6EFF'};color:{afg or fg or '#FFFFFF'};}}")
        if qss: self.setStyleSheet("".join(qss))
        font=_font_from(kwargs.get("font"))
        if font: self.setFont(font)
    def add_command(self, label="", command=None, state="normal", **kwargs):
        action = QAction(str(label or ""), self)
        if command is not None: action.triggered.connect(lambda _checked=False, cb=command: cb())
        action.setEnabled(str(state).lower() != "disabled")
        self.addAction(action); self._actions.append(action); return action
    def add_separator(self): return super().addSeparator()
    def add_cascade(self, label="", menu=None, **kwargs):
        if menu is not None: self.addMenu(menu); menu.setTitle(str(label or ""))
    def delete(self, first, last=None):
        if str(first) == "0" and str(last).lower() == "end":
            self.clear(); self._actions.clear(); return
        try:
            i = int(first); acts = self.actions()
            j = len(acts)-1 if last is None or str(last).lower()=="end" else int(last)
            for act in acts[i:j+1]: self.removeAction(act)
        except Exception: pass
    def tk_popup(self, x, y): self.popup(QPoint(int(x), int(y)))
    def post(self, x, y): self.popup(QPoint(int(x), int(y)))


class Listbox(QListWidget, _WidgetMixin):
    def __init__(self, parent=None, **kwargs):
        QListWidget.__init__(self, _qt_parent(parent))
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self._compat_visible_rows = max(1, int(kwargs.get("height", 5) or 5))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._compat_init(parent, **kwargs)
        self.viewport().installEventFilter(self._event_filter)
        self._sync_compat_height()
    def _sync_compat_height(self):
        # Tk Listbox(height=N) expresa filas visibles. En Qt el sizePolicy por
        # defecto es verticalmente expansible, lo que hacía que una lista de
        # sugerencias estirara toda la fila del formulario. Limitamos la altura
        # al número real de elementos, hasta el máximo configurado.
        try:
            rows = max(1, min(self._compat_visible_rows, max(1, self.count())))
            row_h = max(20, self.sizeHintForRow(0) if self.count() else self.fontMetrics().height() + 6)
            h = rows * row_h + self.frameWidth() * 2 + 4
            self.setMinimumHeight(h)
            self.setMaximumHeight(h)
        except Exception:
            pass
    def delete(self, first, last=None):
        if str(last).lower() == "end" or str(first).lower() == "end":
            if int(first or 0) == 0:
                self.clear(); self._sync_compat_height(); return
        a = 0 if str(first).lower()=="end" else int(first)
        b = a if last is None else (self.count()-1 if str(last).lower()=="end" else int(last))
        for row in range(b, a-1, -1): self.takeItem(row)
        self._sync_compat_height()
    def insert(self, index, item):
        if str(index).lower() == "end":
            self.addItem(str(item))
        else:
            self.insertItem(int(index), str(item))
        self._sync_compat_height()
    def curselection(self): return tuple(sorted(i.row() for i in self.selectedIndexes()))
    def get(self, index):
        it = self.item(int(index)); return it.text() if it else ""
    def size(self): return self.count()
    def selection_clear(self, first, last=None): self.clearSelection()
    def selection_set(self, first, last=None):
        row = int(first); it = self.item(row)
        if it: it.setSelected(True); self.setCurrentRow(row)
    def activate(self, index): self.setCurrentRow(int(index))
    def _apply_compat_style(self):
        values=self._style_values()
        bg=values.get("background",values.get("bg","#0B1624"))
        fg=values.get("foreground",values.get("fg","#EAF2FF"))
        sbg=values.get("selectbackground","#1D6EFF")
        sfg=values.get("selectforeground","#FFFFFF")
        border=values.get("highlightbackground","#254260")
        self.setStyleSheet(
            f"QListWidget{{background:{bg};color:{fg};border:1px solid {border};}}"
            f"QListWidget::item:selected{{background:{sbg};color:{sfg};}}"
        )
        font=_font_from(values.get("font"))
        if font: self.setFont(font)


class Separator(QFrame, _WidgetMixin):
    def __init__(self, parent=None, orient="horizontal", **kwargs):
        kwargs.setdefault("style", "TSeparator")
        QFrame.__init__(self, _qt_parent(parent))
        self._compat_orient = str(orient).lower()
        self.setFrameShape(QFrame.NoFrame)
        self._compat_init(parent, orient=orient, **kwargs)
        if self._compat_orient.startswith("h"):
            self.setMinimumHeight(1); self.setMaximumHeight(1)
            self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        else:
            self.setMinimumWidth(1); self.setMaximumWidth(1)
            self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
    def _apply_compat_style(self):
        values = self._style_values()
        color = values.get("background", values.get("bordercolor", "#40566D"))
        self.setStyleSheet(f"background:{color}; border:none;")


class Scrollbar(QScrollBar, _WidgetMixin):
    def __init__(self, parent=None, orient="vertical", command=None, **kwargs):
        orientation = Qt.Vertical if str(orient).lower().startswith("v") else Qt.Horizontal
        QScrollBar.__init__(self, orientation, _qt_parent(parent))
        self._command = command
        if command is not None:
            self.valueChanged.connect(self._invoke_command)
        self._compat_init(parent, orient=orient, command=command, **kwargs)
    def _invoke_command(self, value):
        if self._command is None: return
        try: self._command("moveto", value / max(1, self.maximum()))
        except Exception: pass
    def set(self, first, last=None):
        try:
            f, l = float(first), float(last)
            self.setRange(0, 1000); self.setPageStep(max(1, int((l-f)*1000)))
            with QSignalBlocker(self): self.setValue(int(f*1000))
        except Exception: pass


class Canvas(QScrollArea, _WidgetMixin):
    def __init__(self, parent=None, **kwargs):
        QScrollArea.__init__(self, _qt_parent(parent))
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self._embedded = None
        self._yscrollcommand = kwargs.get("yscrollcommand")
        self.verticalScrollBar().valueChanged.connect(self._sync_scrollbar)
        self._compat_init(parent, **kwargs)
        self.viewport().installEventFilter(self._event_filter)
    def create_window(self, coords, window=None, anchor="nw", **kwargs):
        if window is not None:
            self._embedded = window
            window.setParent(self.viewport())
            self.setWidget(window)
        return 1
    def bbox(self, what="all"):
        if self._embedded is None: return (0,0,self.width(),self.height())
        return (0,0,self._embedded.sizeHint().width(), self._embedded.sizeHint().height())
    def itemconfigure(self, item, **kwargs):
        if self._embedded is not None and "width" in kwargs:
            try:
                width = max(1, int(kwargs["width"]))
                self._embedded.setMinimumWidth(width)
                self._embedded.resize(width, max(self._embedded.height(), self._embedded.sizeHint().height()))
                self._embedded.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
            except Exception:
                pass
    def yview(self, *args):
        sb = self.verticalScrollBar()
        if not args:
            maxv = max(1, sb.maximum()); page = sb.pageStep()
            return (sb.value()/maxv, min(1.0,(sb.value()+page)/maxv))
        if args[0] == "scroll":
            units = int(args[1]); sb.setValue(sb.value() + units * max(20, sb.singleStep()))
        elif args[0] == "moveto":
            frac = float(args[1]); sb.setValue(int(frac * sb.maximum()))
    def yview_scroll(self, number, what="units"):
        return self.yview("scroll", int(number), what)

    def _sync_scrollbar(self, _value):
        cb = self._compat_options.get("yscrollcommand") or self._yscrollcommand
        if cb:
            try: cb(*self.yview())
            except Exception: pass
    def configure(self, **kwargs):
        if "yscrollcommand" in kwargs: self._yscrollcommand = kwargs["yscrollcommand"]
        return _WidgetMixin.configure(self, **kwargs)
    config = configure


class Treeview(QTableWidget, _WidgetMixin):
    def __init__(self, parent=None, columns=(), show="headings", height=None, **kwargs):
        kwargs.setdefault("style", "Treeview")
        cols = list(columns or [])
        QTableWidget.__init__(self, 0, len(cols), _qt_parent(parent))
        self._columns = [str(c) for c in cols]
        self._iid_counter = 0
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(False)
        if height:
            self.setMinimumHeight(int(height) * 29 + 36)
        self.itemSelectionChanged.connect(lambda: self._emit_virtual("<<TreeviewSelect>>"))
        self._compat_init(parent, columns=columns, show=show, height=height, **kwargs)
        self.viewport().installEventFilter(self._event_filter)

    def _row_for_iid(self, iid):
        target = str(iid)
        for r in range(self.rowCount()):
            item = self.item(r, 0)
            if item is not None and str(item.data(Qt.UserRole)) == target:
                return r
        return -1

    def heading(self, column, text="", **kwargs):
        try: idx = self._columns.index(str(column))
        except ValueError: return
        item = self.horizontalHeaderItem(idx) or QTableWidgetItem()
        item.setText(str(text or column)); self.setHorizontalHeaderItem(idx, item)

    def column(self, column, width=None, anchor=None, **kwargs):
        try: idx = self._columns.index(str(column))
        except ValueError: return
        if width is not None: self.setColumnWidth(idx, int(width))
        # alineación por celda se aplica al insertar.
        self._compat_options.setdefault("column_anchor", {})[str(column)] = anchor

    def insert(self, parent="", index="end", iid=None, values=(), **kwargs):
        self._iid_counter += 1
        iid = str(iid if iid is not None else self._iid_counter)
        row = self.rowCount() if str(index).lower()=="end" else max(0, min(self.rowCount(), int(index)))
        self.insertRow(row)
        vals = list(values or [])
        anchors = self._compat_options.get("column_anchor", {})
        for c in range(len(self._columns)):
            val = vals[c] if c < len(vals) else ""
            item = QTableWidgetItem(str(val if val is not None else ""))
            item.setData(Qt.UserRole, iid)
            anc = str(anchors.get(self._columns[c]) or "").lower()
            if anc == "center": item.setTextAlignment(Qt.AlignCenter)
            elif anc == "e": item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            else: item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self.setItem(row, c, item)
        return iid

    def get_children(self, item=None):
        out=[]
        for r in range(self.rowCount()):
            it=self.item(r,0)
            if it is not None: out.append(str(it.data(Qt.UserRole)))
        return tuple(out)

    def delete(self, *items):
        rows=[]
        for iid in items:
            r=self._row_for_iid(iid)
            if r>=0: rows.append(r)
        for r in sorted(set(rows), reverse=True): self.removeRow(r)

    def selection(self):
        rows=sorted({i.row() for i in self.selectedIndexes()})
        out=[]
        for r in rows:
            it=self.item(r,0)
            if it is not None: out.append(str(it.data(Qt.UserRole)))
        return tuple(out)

    def selection_set(self, iid):
        r=self._row_for_iid(iid)
        if r>=0:
            self.selectRow(r); self.setCurrentCell(r,0)

    def focus(self, iid=None):
        if iid is None:
            sel=self.selection(); return sel[0] if sel else ""
        self.selection_set(iid); self.setFocus()

    def item(self, iid, option=None, **kwargs):  # type: ignore[override]
        # Compatibilidad Tk. Las llamadas internas de Qt usan ints; delegarlas.
        if isinstance(iid, int) and isinstance(option, int):
            return QTableWidget.item(self, iid, option)
        row=self._row_for_iid(iid)
        if row<0: return () if option=="values" else {}
        vals=[]
        for c in range(self.columnCount()):
            cell=QTableWidget.item(self,row,c)
            vals.append(cell.text() if cell else "")
        tup=tuple(vals)
        if option=="values": return tup
        return {"values": tup}

    def identify_row(self, y):
        row=self.rowAt(int(y))
        if row<0: return ""
        it=QTableWidget.item(self,row,0)
        return str(it.data(Qt.UserRole)) if it else ""

    def yview(self, *args):
        sb=self.verticalScrollBar()
        maxv=max(1,sb.maximum()); page=sb.pageStep()
        if args:
            if args[0]=="moveto": sb.setValue(int(float(args[1])*maxv))
            elif args[0]=="scroll": sb.setValue(sb.value()+int(args[1])*max(1,sb.singleStep()))
        return (sb.value()/maxv, min(1.0,(sb.value()+page)/maxv))

    def configure(self, **kwargs):
        out=_WidgetMixin.configure(self, **kwargs)
        self._apply_compat_style()
        return out
    config=configure

    def _apply_compat_style(self):
        values=self._style_values()
        style_name=self._style_name or "Treeview"
        base=_STYLE_REGISTRY.get(style_name,{})
        head=_STYLE_REGISTRY.get(f"{style_name}.Heading", _STYLE_REGISTRY.get("Treeview.Heading",{}))
        bg=values.get("background",base.get("background","#0B1624"))
        fg=values.get("foreground",base.get("foreground","#EAF2FF"))
        border=values.get("bordercolor",base.get("bordercolor","#203348"))
        hbg=head.get("background","#12243A"); hfg=head.get("foreground","#FFFFFF")
        selected="#1D6EFF"; selected_fg="#FFFFFF"
        maps=base.get("__map__",{})
        try:
            selected_pairs=maps.get("background",[])
            if selected_pairs: selected=selected_pairs[0][1]
            fg_pairs=maps.get("foreground",[])
            if fg_pairs: selected_fg=fg_pairs[0][1]
        except Exception: pass
        self.setStyleSheet(
            f"QTableWidget{{background:{bg};color:{fg};gridline-color:{border};border:1px solid {border};}}"
            f"QTableWidget::item:selected{{background:{selected};color:{selected_fg};}}"
            f"QHeaderView::section{{background:{hbg};color:{hfg};border:1px solid {border};padding:5px;font-weight:bold;}}"
        )
        font=_font_from(base.get("font") or values.get("font"))
        if font: self.setFont(font)
        try:
            rh=int(base.get("rowheight",29)); self.verticalHeader().setDefaultSectionSize(rh)
        except Exception: pass


class Notebook(QTabWidget, _WidgetMixin):
    def __init__(self, parent=None, **kwargs):
        kwargs.setdefault("style", "TNotebook")
        QTabWidget.__init__(self, _qt_parent(parent))
        self._compat_init(parent, **kwargs)
        # Los flujos heredados registran ``<<NotebookTabChanged>>`` como en
        # Tk. Sin esta proyección, cambiar una pestaña Qt no ejecuta sus
        # loaders diferidos y deja estados como "Cargando…" indefinidamente.
        self.currentChanged.connect(
            lambda _index: self._emit_virtual("<<NotebookTabChanged>>")
        )
    def add(self, child, text="", **kwargs):
        self.addTab(child, str(text or ""))
    def select(self, tab_id=None):
        if tab_id is None: return self.currentWidget()
        if isinstance(tab_id,int): self.setCurrentIndex(tab_id)
        else:
            idx=self.indexOf(tab_id)
            if idx>=0: self.setCurrentIndex(idx)
    def index(self, what):
        if str(what)=="current": return self.currentIndex()
        if isinstance(what,QWidget): return self.indexOf(what)
        return int(what)
    def tab(self, tab_id, option=None):
        """Expose the Tk ``Notebook.tab(..., 'text')`` contract used by V15."""
        index = self.indexOf(tab_id) if isinstance(tab_id, QWidget) else int(tab_id)
        if option == "text":
            return self.tabText(index) if index >= 0 else ""
        return ""
    def _apply_compat_style(self):
        base=_STYLE_REGISTRY.get("TNotebook",{})
        tab=_STYLE_REGISTRY.get("TNotebook.Tab",{})
        bg=base.get("background","#0E1B2B")
        tbg=tab.get("background","#111E2E"); fg=tab.get("foreground","#EAF2FF")
        selected=tbg
        maps=tab.get("__map__",{})
        try:
            pairs=maps.get("background",[])
            if pairs: selected=pairs[0][1]
        except Exception: pass
        self.setStyleSheet(
            f"QTabWidget::pane{{border:1px solid {base.get('bordercolor','#203348')};background:{bg};}}"
            f"QTabBar::tab{{background:{tbg};color:{fg};padding:7px 12px;}}"
            f"QTabBar::tab:selected{{background:{selected};}}"
        )


class PhotoImage:
    def __init__(self, file=None, **kwargs):
        self.pixmap=QPixmap(str(file)) if file else QPixmap()
    def width(self): return self.pixmap.width()
    def height(self): return self.pixmap.height()
    def subsample(self, x, y=None):
        y=y or x
        out=PhotoImage()
        if self.pixmap.isNull():
            return out
        w=max(1,self.width()//max(1,int(x))); h=max(1,self.height()//max(1,int(y)))
        out.pixmap=self.pixmap.scaled(w,h,Qt.KeepAspectRatio,Qt.SmoothTransformation)
        return out


class DateEntry(QDateEdit, _WidgetMixin):
    def __init__(self, parent=None, dateformat="%d/%m/%Y", firstweekday=0, width=16, startdate=None, **kwargs):
        kwargs.setdefault("style", "TEntry")
        QDateEdit.__init__(self, _qt_parent(parent))
        self.setCalendarPopup(True)
        self.setDisplayFormat("dd/MM/yyyy")
        if startdate is not None:
            try: self.setDate(QDate(startdate.year,startdate.month,startdate.day))
            except Exception: pass
        self.entry=self
        self._compat_init(parent, width=width, **kwargs)
    def get(self): return self.date().toString("dd/MM/yyyy")
    def delete(self, first=0, last=None):
        # QDateEdit no admite campo vacío en el flujo actual; se sustituirá en insert inmediatamente.
        pass
    def insert(self, index, text):
        qd=QDate.fromString(str(text),"dd/MM/yyyy")
        if qd.isValid(): self.setDate(qd)

TBDateEntry = DateEntry


class _MessageBoxNS:
    @staticmethod
    def _parent(parent):
        if parent is None:
            focus = _ensure_app().focusWidget()
            parent = focus.window() if isinstance(focus, QWidget) else None
        parent = _qt_parent(parent)
        if not isinstance(parent, QWidget):
            return None
        if isinstance(parent, (QDialog, QMainWindow, EmbeddedWindowRoot)):
            return parent
        return parent.window()

    @classmethod
    def _show(cls, icon, title, message, buttons, default=None, *, parent=None, high_contrast=False, extra_qss=""):
        box = QMessageBox(cls._parent(parent))
        box.setIcon(icon)
        box.setWindowTitle(str(title))
        box.setText(str(message))
        box.setTextFormat(Qt.PlainText)
        box.setStandardButtons(buttons)
        if default is not None:
            box.setDefaultButton(default)
        if high_contrast:
            box.setMinimumWidth(560)
        box.setStyleSheet(_message_box_qss() + str(extra_qss or ""))
        return box.exec()

    @classmethod
    def showinfo(cls,title,message,parent=None,**kwargs):
        _ensure_app()
        return cls._show(QMessageBox.Information, title, message, QMessageBox.Ok, parent=parent)
    @classmethod
    def showwarning(cls,title,message,parent=None,**kwargs):
        _ensure_app()
        # Keep the body width independent from QMessageBox's warning icon;
        # the selectors are theme-neutral and use colors from _message_box_qss.
        notice_layout_qss = (
            "QLabel#qt_msgbox_label{min-width:420px;}"
            "QLabel#qt_msgboxex_icon_label{min-width:48px;max-width:48px;}"
        )
        return cls._show(
            QMessageBox.Warning,
            title,
            message,
            QMessageBox.Ok,
            parent=parent,
            high_contrast=bool(kwargs.get("high_contrast")),
            extra_qss=notice_layout_qss,
        )
    @classmethod
    def showerror(cls,title,message,parent=None,**kwargs):
        _ensure_app()
        return cls._show(QMessageBox.Critical, title, message, QMessageBox.Ok, parent=parent)
    @classmethod
    def askyesno(cls,title,message,parent=None,**kwargs):
        _ensure_app()
        res=cls._show(QMessageBox.Question, title, message, QMessageBox.Yes|QMessageBox.No, QMessageBox.No, parent=parent)
        return res==QMessageBox.Yes
    @classmethod
    def askretrycancel(cls,title,message,parent=None,**kwargs):
        _ensure_app()
        res=cls._show(QMessageBox.Warning, title, message, QMessageBox.Retry|QMessageBox.Cancel, QMessageBox.Cancel, parent=parent)
        return res==QMessageBox.Retry
    @classmethod
    def askyesnocancel(cls,title,message,parent=None,**kwargs):
        _ensure_app()
        res=cls._show(QMessageBox.Question, title, message, QMessageBox.Yes|QMessageBox.No|QMessageBox.Cancel, QMessageBox.Cancel, parent=parent)
        if res==QMessageBox.Yes: return True
        if res==QMessageBox.No: return False
        return None


class _SimpleDialogNS:
    @staticmethod
    def askstring(title,prompt,show=None,parent=None,initialvalue="",**kwargs):
        _ensure_app()
        p=_MessageBoxNS._parent(parent)
        mode=QLineEdit.Password if show else QLineEdit.Normal
        text,ok=QInputDialog.getText(p,str(title),str(prompt),mode,str(initialvalue or ""))
        return str(text) if ok else None


class _FileDialogNS:
    @staticmethod
    def askopenfilename(parent=None,title="",filetypes=None,**kwargs):
        path,_=QFileDialog.getOpenFileName(_MessageBoxNS._parent(parent),str(title or ""),"")
        return path
    @staticmethod
    def asksaveasfilename(parent=None,title="",defaultextension="",filetypes=None,**kwargs):
        path,_=QFileDialog.getSaveFileName(_MessageBoxNS._parent(parent),str(title or ""),"")
        if path and defaultextension and not os.path.splitext(path)[1]: path+=str(defaultextension)
        return path
    @staticmethod
    def askdirectory(parent=None,title="",**kwargs):
        return QFileDialog.getExistingDirectory(_MessageBoxNS._parent(parent),str(title or ""),"")


messagebox = _MessageBoxNS()
simpledialog = _SimpleDialogNS()
filedialog = _FileDialogNS()


class TclError(RuntimeError):
    pass


tk = SimpleNamespace(
    Tk=Window,
    Toplevel=Toplevel,
    Frame=Frame,
    LabelFrame=LabelFrame,
    Label=Label,
    Entry=Entry,
    Text=Text,
    Button=Button,
    Menubutton=Menubutton,
    Menu=Menu,
    Listbox=Listbox,
    Canvas=Canvas,
    PhotoImage=PhotoImage,
    StringVar=StringVar,
    BooleanVar=BooleanVar,
    IntVar=IntVar,
    DoubleVar=DoubleVar,
    END=END,
    INSERT=INSERT,
    NORMAL=NORMAL,
    DISABLED=DISABLED,
    TclError=TclError,
)

ttk = SimpleNamespace(
    Treeview=Treeview,
    Notebook=Notebook,
    Scrollbar=Scrollbar,
    Separator=Separator,
    Label=Label,
    Frame=Frame,
    Entry=Entry,
    Combobox=Combobox,
    Checkbutton=Checkbutton,
    Radiobutton=Radiobutton,
    Button=Button,
    Style=Style,
)

tb = SimpleNamespace(
    Window=Window,
    Frame=Frame,
    Label=Label,
    Button=Button,
    Entry=Entry,
    Checkbutton=Checkbutton,
    Combobox=Combobox,
    Radiobutton=Radiobutton,
    Menubutton=Menubutton,
    Separator=Separator,
    Style=Style,
)

__all__ = [
    "tk","ttk","tb","messagebox","filedialog","simpledialog","Toplevel","TBDateEntry",
    "PRIMARY","SECONDARY","SUCCESS","INFO","WARNING","DANGER","LIGHT","DARK",
    "END","INSERT","NORMAL","DISABLED","EmbeddedWindowRoot",
    "create_standalone_application",
]
