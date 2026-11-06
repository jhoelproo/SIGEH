"""Qt presentation shared by the SIGEH administrative workspaces."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from sigeh_visual_theme import visual_theme_tokens


def admin_styles(dark):
    t = visual_theme_tokens(dark)
    return f"""
    QWidget#AdminHeading, QWidget#AdminInspector, QWidget#AdminToolbar {{
        background: {t["panel_bg"]}; border: 1px solid {t["border"]};
        border-radius: 8px;
    }}
    QLabel#AdminTitle {{ color: {t["text_primary"]}; font-size: 16pt;
        font-weight: 750; background: transparent; }}
    QLabel#AdminSection {{ color: {t["text_primary"]}; font-size: 11pt;
        font-weight: 750; background: transparent; }}
    QLabel#AdminCaption {{ color: {t["text_secondary"]}; background: transparent; }}
    QLabel#AdminStatus {{ color: {t["info_text"]}; background: {t["info_bg"]};
        border-radius: 5px; padding: 6px 9px; }}
    QScrollArea#AdminScroll, QWidget#AdminScrollContent {{
        background: {t["content_bg"]}; border: none;
    }}
    QLineEdit#AdminSearch {{ placeholder-text-color: {t["text_secondary"]}; }}
    QPlainTextEdit#AdminRecord {{ background: {t["panel_bg"]};
        color: {t["text_primary"]}; border: 1px solid {t["border"]};
        border-radius: 5px; padding: 6px; }}
    QPlainTextEdit#AdminRecord:focus {{ border: 1px solid {t["border_focus"]}; }}
    QTableWidget#AdminTable {{ alternate-background-color: {t["table_alt_bg"]};
        background: {t["table_bg"]}; color: {t["text_primary"]};
        gridline-color: {t["border"]}; selection-color: {t["selection_text"]};
        selection-background-color: {t["selection_bg"]}; }}
    QTableWidget#AdminTable QHeaderView::section {{ background: {t["table_header_bg"]};
        color: {t["text_primary"]}; padding: 6px 8px; font-weight: 700; }}
    QSplitter::handle {{ background: {t["border"]}; }}
    """


def admin_label(text="", role="Caption"):
    label = QLabel(text)
    label.setObjectName(f"Admin{role}")
    label.setTextFormat(Qt.PlainText)
    label.setWordWrap(True)
    return label


def admin_heading(title, description, action=None):
    widget = QWidget()
    widget.setObjectName("AdminHeading")
    row = QHBoxLayout(widget)
    row.setContentsMargins(14, 10, 14, 10)
    labels = QVBoxLayout()
    labels.setSpacing(3)
    labels.addWidget(admin_label(title, "Title"))
    labels.addWidget(admin_label(description))
    row.addLayout(labels, 1)
    if action is not None:
        row.addWidget(action, 0, Qt.AlignTop)
    return widget


def fit_admin_dialog(dialog, preferred=(1240, 760)):
    available = dialog.screen().availableGeometry()
    width, height = available.width() - 28, available.height() - 52
    dialog.setMinimumSize(min(720, width), min(440, height))
    dialog.resize(min(preferred[0], width), min(preferred[1], height))
    dialog.move(available.center() - dialog.rect().center())


def admin_table(table, widths, *, stretch=None, hidden=()):
    table.setObjectName("AdminTable")
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(34)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.Interactive)
    for column, width in widths.items():
        table.setColumnWidth(column, width)
    for column in hidden:
        table.setColumnHidden(column, True)
    if stretch is not None:
        header.setSectionResizeMode(stretch, QHeaderView.Stretch)


def selected_table_row(table):
    row = table.currentRow()
    return (
        row
        if row >= 0 and table.selectedItems() and not table.isRowHidden(row)
        else None
    )


class AdminSplit(QSplitter):
    """Keep the detail pane available below the table on narrower screens."""

    def __init__(self, primary, secondary):
        super().__init__(Qt.Horizontal)
        self.setChildrenCollapsible(False)
        self.setHandleWidth(6)
        self.addWidget(primary)
        self.addWidget(secondary)
        self.setStretchFactor(0, 3)
        self.setStretchFactor(1, 1)
        self.setSizes([760, 290])

    def resizeEvent(self, event):
        super().resizeEvent(event)
        orientation = Qt.Horizontal if event.size().width() >= 1000 else Qt.Vertical
        if self.orientation() != orientation:
            self.setOrientation(orientation)
            self.setSizes([760, 290] if orientation == Qt.Horizontal else [370, 145])


class RecordInspector(QWidget):
    def __init__(self, table, heading, empty_message, *, columns=None):
        super().__init__()
        self.setObjectName("AdminInspector")
        self.table = table
        self.columns = range(table.columnCount()) if columns is None else columns
        self.empty_message = empty_message
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.addWidget(admin_label(heading, "Section"))
        self.record = QPlainTextEdit()
        self.record.setObjectName("AdminRecord")
        self.record.setAccessibleName(heading)
        self.record.setReadOnly(True)
        self.record.setMinimumHeight(65)
        layout.addWidget(self.record, 1)
        table.itemSelectionChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        row = selected_table_row(self.table)
        if row is None:
            self.record.setPlainText(self.empty_message)
            return
        entries = []
        for column in self.columns:
            heading = self.table.horizontalHeaderItem(column)
            item = self.table.item(row, column)
            if heading and item and item.text():
                entries.append(f"{heading.text()} · {item.text()}")
        self.record.setPlainText("\n".join(entries))
