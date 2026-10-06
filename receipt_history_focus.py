"""Return keyboard input to receipt search after deliberate filter actions."""

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QPushButton


class ReceiptSearchFocus(QObject):
    def __init__(self, history):
        super().__init__(history)
        self.history = history
        self.popups = {}
        self.search_requested = False
        for button in history.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._restore)
        for combo in (
            history.ars_combo,
            history.status_combo,
            history.assignment_combo,
        ):
            combo.activated.connect(self.restore)
            self.watch_popup(combo)
        self.watch_popup(history.period_filter.period_combo)
        history.period_filter.period_combo.activated.connect(self._period_selected)
        history.user_filter.clicked.connect(self.restore)
        history.btn_clear_filters.clicked.connect(self.restore)
        for sequence in ("Ctrl+F", "F3"):
            shortcut = QShortcut(QKeySequence(sequence), history)
            shortcut.activated.connect(self.restore)

    def watch_popup(self, combo):
        popup = combo.view().window()
        self.popups[popup] = combo
        popup.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Hide and watched in self.popups:
            if self.popups[watched] == self.history.period_filter.period_combo:
                self._period_selected()
            else:
                self.restore()
        return False

    def restore(self, *_args):
        self.timer.start(0)

    def _restore(self):
        history = self.history
        if not history.isVisible() or not history.isActiveWindow():
            return
        if QApplication.activePopupWidget() or (
            QApplication.activeModalWidget() not in (None, history)
        ):
            return
        history.search_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _period_selected(self, *_args):
        if self.history.period_filter.period_combo.currentText() != "Personalizado":
            self.restore()

    def query_completed(self):
        history = self.history
        focus = history.focusWidget()
        if self.search_requested and focus not in (
            history.document_edit,
            history.period_filter.date_from,
            history.period_filter.date_to,
        ):
            self.restore()
        elif focus in (
            None,
            history.btn_search,
            history.btn_clear_filters,
        ):
            self.restore()
        self.search_requested = False

    def query_started(self):
        self.search_requested = self.history.focusWidget() in (
            self.history.search_edit,
            self.history.btn_search,
            self.history.btn_clear_filters,
        )
