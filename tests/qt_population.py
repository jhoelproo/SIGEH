from PySide6.QtCore import QDeadlineTimer
from PySide6.QtWidgets import QApplication


def wait_for_population(dialog, timeout_ms=3000):
    deadline = QDeadlineTimer(timeout_ms)
    while dialog._populating and not deadline.hasExpired():
        QApplication.processEvents()
    assert not dialog._populating, "Qt table population did not finish"
