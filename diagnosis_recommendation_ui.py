"""Nonblocking recommendation refresh and receipt diagnosis normalization."""

from contextlib import contextmanager
import sqlite3

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QApplication, QDialog, QInputDialog, QMessageBox

from diagnosis_recommendations import model_catalog, recommendations, text_key
from diagnosis_recommendation_store import (
    apply_changes,
    apply_response,
    restore_model,
    synchronize,
)
from network_retry import NetworkRetryGate
from receipt_continuity import local_receipt_store

CACHE_KEY = "diagnosis_recommendations"
SYNC_INTERVAL_MS = 60_000
BATCH_INTERVAL_MS = 2_000
_RUNNING_TASKS: set["RecommendationTask"] = set()


class RecommendationTask(QThread):
    completed = Signal(object)
    failed = Signal(object)

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.callback = callback
        self.connection = None
        self.finished.connect(lambda: _RUNNING_TASKS.discard(self))
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.wait_for_exit)

    def start(self, *args):
        _RUNNING_TASKS.add(self)
        super().start(*args)

    def cancel(self):
        self.requestInterruption()
        connection = self.connection
        if connection is None:
            return True
        try:
            connection.cancel()
            return True
        except Exception:
            return False

    def wait_for_exit(self):
        self.cancel()
        return self.wait(6000)

    def run(self):
        try:
            if self.isInterruptionRequested():
                return
            value = self.callback()
            if not self.isInterruptionRequested():
                self.completed.emit(value)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.failed.emit(exc)


@contextmanager
def recommendation_connection(backend):
    task = QThread.currentThread()
    with backend.db_connect() as con:
        if isinstance(task, RecommendationTask):
            task.connection = getattr(con, "con", None)
        try:
            con.execute("SET LOCAL statement_timeout='5s'")
            con.execute("SET LOCAL lock_timeout='2s'")
            if task.isInterruptionRequested():
                raise InterruptedError("Operación de recomendaciones cancelada.")
            yield con
        finally:
            if isinstance(task, RecommendationTask):
                task.connection = None


def current_specialty(window):
    attention = dict(getattr(window, "current_admission_attention", None) or {})
    return (
        attention.get("specialty")
        or attention.get("specialty_snapshot")
        or attention.get("especialidad")
        or getattr(window, "_editing_specialty_snapshot", "")
    )


def stop_recommendations(window):
    controller = getattr(window, "recommendation_controller", None)
    if controller is not None:
        controller.stop()


def install_recommendation_action(menu, window, backend):
    if backend.user_is_admin(window.current_user):
        action = menu.addAction("Recomendaciones por diagnóstico")
        action.triggered.connect(lambda: window.recommendation_controller.open_admin())


def normalize_receipt_diagnosis(window):
    controller = getattr(window, "recommendation_controller", None)
    if controller is not None and not controller.normalize_before_save():
        raise ValueError(
            "Generación cancelada. Revisa el diagnóstico antes de guardar."
        )


class RecommendationController(QObject):
    def __init__(self, window, backend):
        super().__init__(window)
        self.window, self.backend = window, backend
        self.model = apply_changes({}, [])
        try:
            self.model = restore_model(
                local_receipt_store().cached(CACHE_KEY, self.model)
            )
        except (OSError, ValueError, sqlite3.Error) as exc:
            backend.write_runtime_log(
                f"RECOMMENDATION_CACHE_READ_FAILED {type(exc).__name__}"
            )
        self.retry = NetworkRetryGate()
        self.worker = None
        self.more = False
        self.requested = False
        self.closed = False
        self.timer = QTimer(self)
        self.timer.setInterval(SYNC_INTERVAL_MS)
        self.timer.timeout.connect(self.request_sync)
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(250)
        self.debounce.timeout.connect(self.refresh)
        window.dx_edit.textChanged.connect(lambda: self.debounce.start())
        window.dx_edit.setToolTip(
            "Separa diagnósticos con «;» o « / ». Las recomendaciones utilizan los dos primeros diagnósticos distintos."
        )
        window.recommendation_labels = {}
        self.prepare_labels()

    def start(self):
        self.timer.start()
        self.request_sync()

    def stop(self):
        self.closed = True
        self.timer.stop()
        self.debounce.stop()
        if self.worker is not None:
            self.worker.cancel()
            self.worker.setParent(None)
            self.worker.finished.connect(self.worker.deleteLater)

    def request_sync(self):
        if self.closed or not self.retry.ready:
            return
        if self.worker is not None:
            self.requested = True
            return
        if getattr(self.backend, "db_pool", None) is None:
            return
        cursor = self.model.get("cursor", 0)
        generation = self.model.get("generation", "")

        def fetch():
            with recommendation_connection(self.backend) as con:
                return synchronize(
                    con,
                    cursor,
                    generation=generation,
                    interrupted=QThread.currentThread().isInterruptionRequested,
                )

        self.worker = RecommendationTask(fetch, self)
        self.worker.completed.connect(self.loaded)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.window._recommendation_worker = self.worker
        self.worker.start()

    def loaded(self, data):
        if self.closed:
            return
        self.more = bool(data["more"])
        if not data["changes"] and not data.get("reset"):
            self.retry.succeeded()
            return
        try:
            self.model = restore_model(apply_response(self.model, data))
        except (ValueError, TypeError) as exc:
            self.failed(exc)
            return
        self.retry.succeeded()
        try:
            local_receipt_store().cache(CACHE_KEY, self.model)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.backend.write_runtime_log(
                f"RECOMMENDATION_CACHE_WRITE_FAILED {type(exc).__name__}"
            )
        self.refresh()

    def failed(self, error):
        self.more = False
        self.retry.failed(error)
        self.backend.write_runtime_log(
            f"RECOMMENDATION_SYNC_FAILED {type(error).__name__}"
        )

    def finished(self):
        self.worker = None
        self.window._recommendation_worker = None
        requested, self.requested = self.requested, False
        if (self.more or requested) and not self.closed and self.retry.ready:
            QTimer.singleShot(0 if requested else BATCH_INTERVAL_MS, self.request_sync)

    def prepare_labels(self):
        catalogs = {**self.window.universal, **self.window.ars_cache}
        try:
            values = recommendations(
                self.model,
                self.window.dx_edit.text(),
                current_specialty(self.window),
                catalogs,
            )
        except ValueError:
            values = {}
        self.window.recommendation_labels = {
            (category, item["item"]): {**item, "rank": index}
            for category, items in values.items()
            for index, item in enumerate(items)
        }

    def refresh(self):
        if self.closed:
            return
        previous = self.window.recommendation_labels
        self.prepare_labels()
        if previous != self.window.recommendation_labels:
            self.window.refresh_picker()

    def normalize_before_save(self):
        catalog = model_catalog(self.model)
        values = []
        for part in catalog.split(self.window.dx_edit.text()):
            value = self.choose_diagnosis(catalog, part)
            if value is None:
                return False
            values.append(value)
        self.window.dx_edit.setText("; ".join(values))
        return True

    def choose_diagnosis(self, catalog, part):
        choices = catalog.choices(part)
        if not choices:
            return part.strip().upper()
        exact = text_key(part) in catalog.names or text_key(part) in catalog.aliases
        if len(choices) == 1 and exact:
            return choices[0]
        value, accepted = QInputDialog.getItem(
            self.window,
            "Confirmar diagnóstico",
            f"Diagnóstico ingresado: {part}\nSelecciona cómo se guardará:",
            [*choices, "Conservar texto ingresado"],
            0,
            False,
        )
        if not accepted:
            return None
        return part.strip().upper() if value == "Conservar texto ingresado" else value

    def open_admin(self):
        if not self.backend.user_is_admin(self.window.current_user):
            QMessageBox.warning(
                self.window,
                "Recomendaciones",
                "Solo el administrador puede configurar recomendaciones.",
            )
            return
        if getattr(self.backend, "db_pool", None) is None:
            QMessageBox.information(
                self.window,
                "Recomendaciones",
                "Conéctate para modificar la configuración compartida. Las sugerencias guardadas siguen disponibles.",
            )
            return
        from diagnosis_recommendation_admin import RecommendationAdminDialog

        dialog = RecommendationAdminDialog(
            self.backend, self.window.current_user, self.model, self.window
        )
        if dialog.exec() == QDialog.Accepted:
            self.request_sync()
