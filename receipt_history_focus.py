"""Explicit receipt-search focus actions without interrupting other controls."""

from PySide6.QtCore import QObject, QTimer, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QPushButton


class ReceiptSearchFocus(QObject):
    def __init__(self, history):
        super().__init__(history)
        self.history = history
        for button in history.findChildren(QPushButton):
            button.setAutoDefault(False)
            button.setDefault(False)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._restore)
        history.btn_clear_filters.clicked.connect(self.restore)
        for sequence in ("Ctrl+F", "F3"):
            shortcut = QShortcut(QKeySequence(sequence), history)
            shortcut.activated.connect(self.restore)

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
