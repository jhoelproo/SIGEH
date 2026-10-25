"""Nonblocking recommendation refresh and receipt diagnosis normalization."""

from contextlib import contextmanager
import re
import sqlite3

from PySide6.QtCore import (
    QEvent,
    QObject,
    QStringListModel,
    QThread,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QInputDialog,
    QMessageBox,
)

from diagnosis_text import canonical_diagnosis, receipt_diagnosis
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
DIAGNOSIS_SEPARATOR = re.compile(r";|\n|\s*//\s*|\s*\|\s*|\s+/\s+")
MIN_COMPLETION_LENGTH = 2


def _diagnosis_separators(text):
    for match in DIAGNOSIS_SEPARATOR.finditer(text):
        is_origin_abbreviation = (
            match.group().strip() == "/"
            and re.search(r"\bO\s*$", text[: match.start()], re.IGNORECASE)
            and re.match(r"\s*I\b", text[match.end() :], re.IGNORECASE)
        )
        if not is_origin_abbreviation:
            yield match


def active_diagnosis_segment(text, cursor):
    """Locate the current diagnosis without treating the slash in O/I as a separator."""
    cursor = max(0, min(cursor, len(text)))
    start, end = 0, len(text)
    for match in _diagnosis_separators(text):
        if match.end() <= cursor:
            start = match.end()
        elif match.start() < cursor:
            return cursor, cursor, ""
        else:
            end = match.start()
            break
    segment = text[start:end]
    start += len(segment) - len(segment.lstrip())
    end -= len(segment) - len(segment.rstrip())
    end = max(start, end)
    return start, end, text[start : max(start, cursor)].strip()


def replace_active_diagnosis(text, cursor, diagnosis):
    start, end, _prefix = active_diagnosis_segment(text, cursor)
    replacement = canonical_diagnosis(diagnosis)
    return text[:start] + replacement + text[end:], start + len(replacement)


class DiagnosisCompletion(QObject):
    """Offer local diagnosis choices while leaving text editing in the line edit."""

    def __init__(self, field, catalog_provider, parent=None):
        super().__init__(parent)
        self.field, self.catalog_provider = field, catalog_provider
        self.closed = False
        self.applying = False
        self.model = QStringListModel(self)
        self.completer = QCompleter(self.model, self)
        self.completer.setWidget(field)
        self.completer.setCompletionMode(QCompleter.UnfilteredPopupCompletion)
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setMaxVisibleItems(12)
        self.completer.activated[str].connect(self.select)
        field.textEdited.connect(self.update)
        field.cursorPositionChanged.connect(self.cursor_moved)
        field.installEventFilter(self)

    def eventFilter(self, watched, event):
        if (
            not self.closed
            and watched is self.field
            and event.type() == QEvent.KeyPress
            and self.completer.popup().isVisible()
        ):
            return self._popup_key(event.key())
        return False

    def _popup_key(self, key):
        popup = self.completer.popup()
        if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
            selected = popup.currentIndex()
            if selected.isValid():
                self.select(selected.data())
            return selected.isValid()
        if key == Qt.Key_Escape:
            popup.hide()
            return True
        if key in (Qt.Key_Up, Qt.Key_Down):
            self._move_selection(-1 if key == Qt.Key_Up else 1)
            return True
        return False

    def _move_selection(self, delta):
        popup = self.completer.popup()
        model = self.completer.completionModel()
        row = max(0, min(popup.currentIndex().row() + delta, model.rowCount() - 1))
        popup.setCurrentIndex(model.index(row, 0))

    def update(self, _text=None):
        if self.closed or self.applying or not self.field.hasFocus():
            return
        _start, _end, prefix = active_diagnosis_segment(
            self.field.text(), self.field.cursorPosition()
        )
        values = (
            self.catalog_provider().suggestions(prefix)
            if len(text_key(prefix)) >= MIN_COMPLETION_LENGTH
            else []
        )
        if values != self.model.stringList():
            self.model.setStringList(values)
        if not values:
            self.completer.popup().hide()
            return
        self.completer.setCompletionPrefix(prefix)
        self.completer.popup().setMinimumWidth(max(self.field.width(), 360))
        self.completer.complete()
        self.completer.popup().setCurrentIndex(
            self.completer.completionModel().index(0, 0)
        )

    def cursor_moved(self, _previous, _current):
        if self.completer.popup().isVisible():
            self.update()

    def select(self, diagnosis):
        if self.closed:
            return
        text, cursor = replace_active_diagnosis(
            self.field.text(), self.field.cursorPosition(), diagnosis
        )
        self.applying = True
        try:
            self.field.setText(text)
            self.field.setCursorPosition(cursor)
            self.completer.popup().hide()
            self.field.setFocus()
        finally:
            self.applying = False

    def stop(self):
        self.closed = True
        self.completer.popup().hide()


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
            "Escribe para seleccionar un diagnóstico. Separa diagnósticos con «;» o « / ». Las recomendaciones utilizan los dos primeros diagnósticos distintos."
        )
        self.completion = DiagnosisCompletion(window.dx_edit, self.catalog, self)
        window.recommendation_labels = {}
        self.prepare_labels()

    def catalog(self):
        signature = (
            id(self.model),
            id(self.model.get("config")),
            id(self.model.get("groups")),
        )
        if signature != getattr(self, "_catalog_signature", None):
            self._catalog = model_catalog(self.model)
            self._catalog_signature = signature
        return self._catalog

    def start(self):
        self.timer.start()
        self.request_sync()

    def stop(self):
        self.closed = True
        self.timer.stop()
        self.debounce.stop()
        self.completion.stop()
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
        snapshot = self.model

        def fetch():
            with recommendation_connection(self.backend) as con:
                data = synchronize(
                    con,
                    cursor,
                    generation=generation,
                    interrupted=QThread.currentThread().isInterruptionRequested,
                )
            return self.prepare_response(snapshot, data)

        self.worker = RecommendationTask(fetch, self)
        self.worker.completed.connect(self.loaded)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.window._recommendation_worker = self.worker
        self.worker.start()

    def prepare_response(self, snapshot, data):
        if self.closed or QThread.currentThread().isInterruptionRequested():
            return data
        if not data["changes"] and not data.get("reset"):
            return data
        model = restore_model(apply_response(snapshot, data))
        catalog = model_catalog(model)
        if self.closed or QThread.currentThread().isInterruptionRequested():
            return data
        error = self.persist_model(model)
        return {
            **data,
            "prepared_model": model,
            "prepared_catalog": catalog,
            "cache_error": error,
        }

    @staticmethod
    def persist_model(model):
        try:
            local_receipt_store().cache(CACHE_KEY, model)
        except (OSError, ValueError, sqlite3.Error) as exc:
            return f"RECOMMENDATION_CACHE_WRITE_FAILED {type(exc).__name__}"
        return ""

    def loaded(self, data):
        if self.closed:
            return
        self.more = bool(data["more"])
        if not data["changes"] and not data.get("reset"):
            self.retry.succeeded()
            return
        try:
            self.model = data.get("prepared_model") or restore_model(
                apply_response(self.model, data)
            )
        except (ValueError, TypeError) as exc:
            self.failed(exc)
            return
        self.retry.succeeded()
        if "prepared_catalog" in data:
            self._catalog = data["prepared_catalog"]
            self._catalog_signature = (
                id(self.model),
                id(self.model.get("config")),
                id(self.model.get("groups")),
            )
        error = (
            data.get("cache_error", "")
            if "prepared_model" in data
            else self.persist_model(self.model)
        )
        if error:
            self.backend.write_runtime_log(error)
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
                catalog=self.catalog(),
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
        if self.completion.completer.popup().isVisible():
            self.completion.update()
        previous = self.window.recommendation_labels
        self.prepare_labels()
        if previous != self.window.recommendation_labels:
            self.window.refresh_picker()

    def normalize_before_save(self):
        catalog = self.catalog()
        values = []
        for part in catalog.split(self.window.dx_edit.text()):
            value = self.choose_diagnosis(catalog, part)
            if value is None:
                return False
            values.append(value)
        self.window.dx_edit.setText(receipt_diagnosis("; ".join(values)))
        return True

    def choose_diagnosis(self, catalog, part):
        part = canonical_diagnosis(part)
        choices = catalog.choices(part)
        if not choices:
            return part.strip().upper()
        exact = (
            text_key(part) in catalog.names
            or text_key(part) in catalog.aliases
            or choices == [part]
        )
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
