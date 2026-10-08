"""Give receipt rows priority while keeping summaries available on demand."""

from PySide6.QtCore import QEvent, QObject, QTimer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QToolButton


class ReceiptHistoryResponsive(QObject):
    def __init__(self, workspace, heading):
        super().__init__(workspace.history)
        self.workspace = workspace
        self.heading = heading
        self.expanded = False
        self.summary_button = QPushButton("Mostrar resumen de auditoría")
        self.summary_button.setCheckable(True)
        self.summary_button.setAutoDefault(False)
        from workspace_accents import style_action

        style_action(self.summary_button, "blue", True, filled=False, compact=True)
        self.summary_button.toggled.connect(self.toggle_summary)
        heading_layout = heading.layout()
        title = heading_layout.takeAt(0).widget()
        title_row = QHBoxLayout()
        title_row.addWidget(title, 1)
        title_row.addWidget(self.summary_button)
        heading_layout.insertLayout(0, title_row)
        workspace.history.installEventFilter(self)
        self.fit()

    def toggle_summary(self, checked):
        self.expanded = checked
        self.fit()

    def fit(self):
        h = self.workspace.history
        compact = h.height() < 800
        root = h.layout()
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(4 if compact else 8)
        high_role = h._is_high_history_role()
        show_summary = high_role and (not compact or self.expanded)
        self.summary_button.setVisible(compact and high_role)
        h.metrics_widget.setVisible(show_summary)
        h.audit_queue_summary.setVisible(show_summary)
        for caption in self.heading.findChildren(QLabel, "DesignCaption"):
            caption.setVisible(not compact)
        h.table.verticalHeader().setDefaultSectionSize(34 if compact else 42)
        h.filter_summary.setVisible(not compact)
        _fit_history_filters(self.workspace, compact)
        for button in (h.btn_previous_page, h.btn_next_page):
            button.setStyleSheet("QPushButton {min-height:24px;padding:3px 10px;}")
        _fit_history_actions(h, compact)
        self.workspace.details.setVisible(h.width() >= 1450)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            QTimer.singleShot(0, self.fit)
        return False


def _fit_history_actions(history, compact):
    parent = history.btn_close.parentWidget()
    buttons = parent.findChildren(QPushButton) + parent.findChildren(QToolButton)
    for button in buttons:
        button.setMaximumHeight(36 if compact else 16777215)


def _fit_history_filters(workspace, compact):
    if workspace.compact_filter_options.property("compact") == compact:
        return
    workspace.compact_filter_options.setProperty("compact", compact)
    h = workspace.history
    grid = h.search_edit.parentWidget().layout()
    options = workspace.compact_filter_options.layout()
    for control in (h.period_filter, workspace.assignment_filter):
        grid.removeWidget(control)
        options.removeWidget(control)
    if compact:
        options.addWidget(h.period_filter, 2)
        options.addWidget(workspace.assignment_filter, 1)
    else:
        grid.addWidget(h.period_filter, 2, 0, 1, 2)
        grid.addWidget(workspace.assignment_filter, 2, 2)
    h.period_filter.show()
    workspace.assignment_filter.show()
    workspace.compact_filter_options.setVisible(compact)
