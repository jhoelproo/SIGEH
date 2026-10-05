"""Shared visual components for the billing workspaces."""

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from workspace_accents import MetricCard


def workspace_styles(dark):
    background, panel, text, muted, border = (
        ("#071725", "#0d2134", "#edf5ff", "#aec4dc", "#25435f")
        if dark
        else ("#edf3fa", "#ffffff", "#152f4d", "#55708d", "#c6d6e7")
    )
    return f"""
    QWidget {{ font-family: 'Segoe UI'; font-size: 9pt; }}
    QLabel, QGroupBox, QPushButton, QLineEdit, QComboBox, QToolButton,
    QTabBar, QTableWidget, QListWidget {{ font-family: 'Segoe UI'; }}
    QLineEdit, QComboBox {{ min-height: 22px; padding: 3px 7px; }}
    QLineEdit#ModernSearch {{ min-height: 22px; padding: 3px 7px; }}
    QWidget#DesignSurface {{ background: {background}; color: {text}; }}
    QWidget#DesignCard, QWidget#DesignDetail {{ background: {panel};
        border: 1px solid {border}; border-radius: 10px; }}
    QLabel#DesignTitle {{ color: {text}; font-size: 15pt; font-weight: 800;
        border: none; background: transparent; padding: 4px; }}
    QLabel#DesignCaption {{ color: {muted}; border: none; background: transparent; }}
    QLabel#DesignValue {{ color: {text}; border: none; background: transparent;
        font-weight: 700; padding: 4px 0; }}
    QLabel#DesignTotal {{ color: #38df9c; background: #073f32; border-radius: 8px;
        border: 1px solid #147854; padding: 12px; font-size: 18pt; font-weight: 800; }}
    QPushButton#DesignPill {{ background: {panel}; color: {text}; border: 1px solid {border};
        border-radius: 8px; padding: 7px 10px; font-weight: 700; }}
    QPushButton#DesignPill:checked {{ background: #1069bd; color: white; border-color: #35a8ff; }}
    QPushButton#DesignAdd {{ background: #087deb; color: white; padding: 5px 10px;
        border: 1px solid #36a4fc; border-radius: 6px; font-weight: 700; }}
    QPushButton#DesignAdd:hover {{ background: #2398ff; }}
    QWidget#DesignCatalogRow {{ background: {panel}; border-bottom: 1px solid {border}; }}
    QWidget#DesignCatalogRow[selected="true"] {{ background: #20598a; color: white; }}
    QWidget#DesignCatalogRow QLabel {{ background: transparent; border: none; color: {text}; }}
    QWidget#DesignCatalogRow[selected="true"] QLabel {{ color: white; }}
    QToolButton#DesignStar {{ color: #e5a92d; border: none; background: transparent; font-size: 18pt; }}
    QGroupBox#DesignPatient {{ background: {panel}; border: 1px solid {border};
        border-radius: 10px; padding: 12px; margin-top: 15px; font-weight: 700; }}
    QGroupBox#DesignPatient::title {{ color: {text}; padding: 0 8px; }}
    QWidget#HeaderWidget {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0,
        stop:0 #0a1e34,stop:1 #143c61); color: white; border-bottom: 1px solid #28577c; }}
    QWidget#NavWidget {{ background: #0a1d30; border-right: 1px solid #25435f; }}
    QWidget#NavWidget QPushButton {{ text-align: left; padding: 9px; border-radius: 7px; }}
    QWidget#DesignDetail QScrollArea, QWidget#DesignDetail QWidget#DetailContents {{
        background: {panel}; border: none; }}
    QTableWidget {{ gridline-color: {border}; selection-background-color: #155c9a; }}
    QTableWidget::item {{ padding: 5px; }}
    QHeaderView::section {{ background: {panel}; color: {text};
        padding: 8px 5px; border: 1px solid {border}; font-weight: 700; }}
    """


def title(text):
    label = QLabel(text)
    label.setTextFormat(Qt.PlainText)
    label.setObjectName("DesignTitle")
    label.setWordWrap(True)
    return label


def metric_row(labels, tones=()):
    container = QWidget()
    layout = QHBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    for index, label in enumerate(labels):
        card = MetricCard(label, tones[index] if index < len(tones) else "blue")
        layout.addWidget(card, 1)
    return container


def pill(text, callback, parent=None):
    button = QPushButton(text, parent)
    button.setObjectName("DesignPill")
    button.setCheckable(True)
    button.setAutoDefault(False)
    button.clicked.connect(callback)
    return button


class DetailPanel(QWidget):
    def __init__(self, heading, fields, actions=(), parent=None):
        super().__init__(parent)
        self.setObjectName("DesignDetail")
        self.setMinimumWidth(210)
        self.setMaximumWidth(290)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        contents = QWidget()
        contents.setObjectName("DetailContents")
        scroll.setWidget(contents)
        layout = QVBoxLayout(contents)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.addWidget(title(heading))
        self.patient = QLabel("Selecciona una fila")
        self.patient.setObjectName("DesignValue")
        self.patient.setTextFormat(Qt.PlainText)
        self.patient.setWordWrap(True)
        layout.addWidget(self.patient)
        self.fields = {}
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.WrapAllRows)
        for key, label in fields:
            value = QLabel("—")
            value.setTextFormat(Qt.PlainText)
            value.setWordWrap(True)
            value.setObjectName("DesignValue")
            self.fields[key] = value
            form.addRow(label, value)
        layout.addLayout(form)
        self.buttons = {}
        buttons = QGridLayout()
        for index, (key, label, callback) in enumerate(actions):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.setMinimumWidth(0)
            button.clicked.connect(callback)
            self.buttons[key] = button
            buttons.addWidget(button, index, 0)
        layout.addLayout(buttons)
        layout.addStretch(1)

    def show_values(self, patient, values):
        self.patient.setText(str(patient or "Selecciona una fila"))
        for key, label in self.fields.items():
            label.setText(str(values.get(key) or "—"))


def fit_detail_panel(panel, width, minimum=1250):
    panel.setVisible(width >= minimum)


class DetailResponsive(QObject):
    def __init__(self, surface, panel, minimum=1250):
        super().__init__(surface)
        self.panel = panel
        self.minimum = minimum
        surface.installEventFilter(self)
        fit_detail_panel(panel, surface.width(), minimum)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Resize:
            fit_detail_panel(self.panel, watched.width(), self.minimum)
        return False
