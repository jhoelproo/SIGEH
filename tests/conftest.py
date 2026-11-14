from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
import pytest


@pytest.fixture(scope="session", autouse=True)
def qt_application():
    """Keep Qt alive across modules that share native event queues."""
    return QApplication.instance() or QApplication([])


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    yield
    if QApplication.instance() is not None:
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
