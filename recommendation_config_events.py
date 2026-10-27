"""Small, commit-only PostgreSQL signals for administrator configuration changes."""

import select
from threading import Event

import psycopg2
from psycopg2.extensions import parse_dsn
from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QApplication

CHANNEL = "sigeh_recommendation_config"
_RUNNING_EVENTS: set["RecommendationConfigEvents"] = set()


def listener_parameters(dsn):
    parameters = parse_dsn(dsn)
    if (
        parameters.get("host", "").endswith(".pooler.supabase.com")
        and parameters.get("port") == "6543"
    ):
        parameters["port"] = "5432"
    if parameters.get("host", "").endswith((".pooler.supabase.com", ".supabase.co")):
        parameters.setdefault("sslmode", "require")
    return {
        **parameters,
        "connect_timeout": 5,
        "application_name": "sigeh_recommendation_events",
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 3,
    }


def receive_changes(connection, timeout=0.5):
    if not select.select([connection], [], [], timeout)[0]:
        return False
    connection.poll()
    changed = any(
        notification.channel == CHANNEL for notification in connection.notifies
    )
    connection.notifies.clear()
    return changed


class RecommendationConfigEvents(QThread):
    changed = Signal()

    def __init__(self, dsn, parent=None):
        super().__init__(parent)
        self.dsn = dsn
        self.stopping = Event()
        self.finished.connect(lambda: _RUNNING_EVENTS.discard(self))
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.shutdown)

    def start(self, *args):
        _RUNNING_EVENTS.add(self)
        super().start(*args)

    def stop(self):
        self.stopping.set()
        self.requestInterruption()

    def shutdown(self):
        self.stop()
        return self.wait(6000)

    def run(self):
        delay = 5
        while not self.stopping.is_set():
            try:
                self._listen()
                delay = 5
            except (psycopg2.Error, OSError, ValueError):
                if self.stopping.wait(delay):
                    return
                delay = min(60, delay * 2)

    def _listen(self):
        connection = psycopg2.connect(**listener_parameters(self.dsn))
        try:
            connection.autocommit = True
            with connection.cursor() as cursor:
                cursor.execute("LISTEN sigeh_recommendation_config")
            self.changed.emit()
            while not self.stopping.is_set():
                if receive_changes(connection):
                    self.changed.emit()
        finally:
            connection.close()
