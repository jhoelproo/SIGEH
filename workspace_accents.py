"""Shared presentation accents; stored text, actions and states stay unchanged."""

import re

from PySide6.QtCore import QEvent, QObject, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from app_icons import APP_ICONS


ACCENTS = {
    "blue": (
        "#65c3ff",
        "#075da8",
        "#10334f",
        "#e2f2ff",
        "#168bff",
        "#0565cd",
        "billing_receipts",
    ),
    "green": (
        "#70eaa0",
        "#116d36",
        "#103c2a",
        "#e2f7e9",
        "#1bab56",
        "#08783c",
        "verify",
    ),
    "amber": (
        "#ffce66",
        "#845300",
        "#493616",
        "#fff3d4",
        "#b77809",
        "#875402",
        "history",
    ),
    "purple": (
        "#d3acff",
        "#7137a8",
        "#32234b",
        "#f1e8ff",
        "#8a4fe0",
        "#5d2eb7",
        "billing_reports",
    ),
    "red": ("#ff98a1", "#a92736", "#48242d", "#ffe8eb", "#de4050", "#ac2035", "delete"),
}


def accent_colors(tone, dark):
    foreground, light_foreground, background, light_background, start, end, icon = (
        ACCENTS.get(tone, ACCENTS["blue"])
    )
    return {
        "foreground": foreground if dark else light_foreground,
        "background": background if dark else light_background,
        "start": start,
        "end": end,
        "icon": icon,
    }


def action_styles(tone, dark, *, filled=True, compact=False):
    colors = accent_colors(tone, dark)
    foreground = "#ffffff" if filled else colors["foreground"]
    background = (
        f"qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {colors['start']},stop:1 {colors['end']})"
        if filled
        else colors["background"]
    )
    disabled_background, disabled_text = (
        ("#172a3c", "#8093a6") if dark else ("#e9edf2", "#647386")
    )
    padding, height = ("3px 6px", 20) if compact else ("8px 10px", 22)
    return f"""
    QPushButton, QToolButton {{ background: {background}; color: {foreground};
        border: 1px solid {colors["start"]}; border-radius: 7px;
        padding: {padding}; min-height: {height}px; font-weight: 700; }}
    QPushButton:hover:enabled, QToolButton:hover:enabled {{
        background: {colors["start"]}; color: white; border-color: {colors["foreground"]}; }}
    QPushButton:pressed:enabled, QToolButton:pressed:enabled,
    QPushButton:checked {{ background: {colors["end"]}; color: white; }}
    QPushButton:focus, QToolButton:focus {{ border: 2px solid {colors["foreground"]}; }}
    QPushButton:disabled, QToolButton:disabled {{ background: {disabled_background};
        color: {disabled_text}; border-color: {disabled_background}; }}
    """


def style_action(button, tone, dark, *, filled=True, compact=False):
    button.setProperty("accentTone", tone)
    colors = accent_colors(tone, dark)
    button.setProperty(
        "semanticIconColor", "#ffffff" if filled else colors["foreground"]
    )
    button.setStyleSheet(action_styles(tone, dark, filled=filled, compact=compact))


def status_tone(text):
    value = str(text or "").casefold()
    for words, tone in (
        (("no facturado", "error", "rechazado"), "red"),
        (("facturado",), "blue"),
        (("listo", "completo", "pagado"), "green"),
        (("exonerado", "histórico"), "purple"),
        (("revisar", "pendiente", "preliminar"), "amber"),
    ):
        if any(word in value for word in words):
            return tone
    return "blue"


def style_status_label(label, dark):
    colors = accent_colors(status_tone(label.text()), dark)
    label.setStyleSheet(
        f"color:{colors['foreground']};background:{colors['background']};"
        f"border:1px solid {colors['start']};border-radius:9px;"
        "padding:6px 9px;font-weight:800;"
    )


class WorkspaceRowDelegate(QStyledItemDelegate):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.dark = True

    def styled_option(self, option, index):
        styled = QStyleOptionViewItem(option)
        self.initStyleOption(styled, index)
        styled.backgroundBrush = QBrush(Qt.NoBrush)
        styled.palette.setColor(
            QPalette.Text, QColor("#eaf3ff" if self.dark else "#17324d")
        )
        styled.palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
        return styled

    def paint(self, painter, option, index):
        styled = self.styled_option(option, index)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, option.widget)


class StatusBadgeDelegate(WorkspaceRowDelegate):
    def paint(self, painter, option, index):
        styled = self.styled_option(option, index)
        text = styled.text
        styled.text = ""
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, styled, painter, option.widget)
        if not text:
            return
        colors = accent_colors(status_tone(text), self.dark)
        rect = QRectF(option.rect).adjusted(7, 7, -7, -7)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setClipRect(option.rect)
        painter.setBrush(QColor(colors["background"]))
        painter.setPen(QPen(QColor(colors["start"]), 1))
        painter.drawRoundedRect(rect, 7, 7)
        font = QFont("Segoe UI", 8)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(colors["foreground"]))
        display = QFontMetrics(font).elidedText(
            text, Qt.ElideRight, max(0, int(rect.width()) - 8)
        )
        painter.drawText(rect, Qt.AlignCenter, display)
        painter.restore()


def metric_parts(text):
    lines = str(text or "").split("\n", 1)
    first, second = lines[0], lines[1] if len(lines) > 1 else ""
    number = re.compile(r"^(?:RD\$\s*|Facturados:\s*)?\d[\d,.]*")
    match = number.match(first)
    candidate, caption = (first, second) if match else (second, first)
    match = number.match(candidate)
    if not match:
        return "—", caption, candidate
    value = match.group().removeprefix("Facturados:").strip()
    detail = candidate[match.end() :].removeprefix(" recibos").strip(" ·")
    return value, caption, detail


class MetricTextPainter(QObject):
    def __init__(self, label):
        super().__init__(label)
        self.dark = True
        label.setMinimumHeight(62)
        label.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Paint:
            return False
        value, caption, detail = metric_parts(watched.text())
        painter = QPainter(watched)
        foreground, muted = (
            ("#edf5ff", "#adc5de") if self.dark else ("#152f4d", "#526b84")
        )
        font = QFont("Segoe UI", 20)
        font.setBold(True)
        while (
            font.pointSize() > 10
            and QFontMetrics(font).horizontalAdvance(value) > watched.width()
        ):
            font.setPointSize(font.pointSize() - 1)
        painter.setFont(font)
        painter.setPen(QColor(foreground))
        painter.drawText(
            watched.rect().adjusted(0, 0, 0, -29), Qt.AlignLeft | Qt.AlignVCenter, value
        )
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(muted))
        caption_rect = watched.rect().adjusted(0, 34, 0, -13)
        painter.drawText(
            caption_rect,
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(font).elidedText(caption, Qt.ElideRight, watched.width()),
        )
        painter.drawText(
            watched.rect().adjusted(0, 49, 0, 0),
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(font).elidedText(detail, Qt.ElideRight, watched.width()),
        )
        painter.end()
        return True


class MetricCard(QWidget):
    def __init__(self, label, tone):
        super().__init__()
        self.tone = tone
        self.setObjectName("DesignMetricCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setProperty("accentTone", tone)
        self.body = QHBoxLayout(self)
        self.body.setContentsMargins(12, 9, 12, 9)
        self.body.setSpacing(10)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        self.icon.setFixedSize(42, 42)
        self.body.addWidget(self.icon)
        label.setObjectName("DesignMetricValue")
        label.setMinimumWidth(0)
        label.setWordWrap(True)
        self.body.addWidget(label, 1)
        self.painter = MetricTextPainter(label)
        self.set_theme(True)

    def set_theme(self, dark):
        colors = accent_colors(self.tone, dark)
        panel, border = ("#0c1f32", "#26435e") if dark else ("#ffffff", "#c3d5e8")
        self.setStyleSheet(
            f"QWidget#DesignMetricCard {{ background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            f"stop:0 {colors['background']},stop:1 {panel});border:1px solid {border};border-radius:10px; }}"
            "QLabel#DesignMetricValue {background:transparent;border:none;padding:0;}"
        )
        self.icon.setStyleSheet(
            f"background:{colors['background']};border:1px solid {colors['start']};border-radius:11px;"
        )
        self.icon.setPixmap(
            APP_ICONS.icon(colors["icon"], size=26, color=colors["foreground"]).pixmap(
                26, 26
            )
        )
        self.painter.dark = bool(dark)
        self.update()

    def add_action(self, button):
        button.setFixedWidth(24)
        self.body.addWidget(button)


def apply_metric_theme(container, dark):
    for card in container.findChildren(MetricCard):
        card.set_theme(dark)


def table_styles(dark):
    background, alternate, text, border, header = (
        ("#091b2a", "#0d2235", "#eaf3ff", "#244058", "#152f47")
        if dark
        else ("#ffffff", "#f0f6fd", "#17324d", "#c4d7e9", "#e6effa")
    )
    return f"""
    QTableWidget {{background:{background};alternate-background-color:{alternate};
        color:{text};gridline-color:{border};border:1px solid {border};border-radius:8px;
        selection-background-color:#1267bd;selection-color:white;}}
    QTableWidget QHeaderView::section {{background:{header};color:{text};
        border:0;border-right:1px solid {border};border-bottom:1px solid {border};
        padding:8px 5px;font-weight:700;}}
    QTableWidget::item {{padding:5px;}}
    """
