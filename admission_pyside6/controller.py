from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from .models import AdmissionFormState, AdmissionResult
from .service import AdmissionService, AdmissionValidationError


class AdmissionController(QObject):
    attention_created = Signal(object)
    attention_updated = Signal(object)
    attention_cancelled = Signal(object)
    detail_sheet_generated = Signal(object)
    detail_sheet_requested = Signal(int)
    shift_changed = Signal(object)
    history_refresh_requested = Signal()
    state_changed = Signal(object)
    operation_failed = Signal(str)
    search_completed = Signal(object)
    shift_opened = Signal(object)
    shift_closed = Signal(object)
    shift_summary_loaded = Signal(object)
    history_loaded = Signal(object)

    def __init__(self, service: AdmissionService, parent=None, document_service=None):
        super().__init__(parent)
        self.service = service
        self.document_service = document_service
        self.service.context.event_bus.shift_changed.connect(self.shift_changed.emit)

    def _state(self):
        self.state_changed.emit(self.service.state)

    @Slot(str)
    def search(self, text: str):
        try:
            rows = self.service.search_patients(text)
            self.search_completed.emit(rows)
            return rows
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return []

    @Slot(object)
    def register(self, data) -> AdmissionResult:
        try:
            result = self.service.register(data)
            self._state()
            if result.ok:
                self.attention_created.emit(result.data)
                self.service.context.event_bus.attention_created.emit(result.data)
                self.history_refresh_requested.emit()
                if self.document_service is not None:
                    attention_id = self._result_attention_id(result.data)
                    if attention_id:
                        document_result = self.document_service.register_output(attention_id)
                        if document_result.ok:
                            self.detail_sheet_generated.emit(document_result)
                        else:
                            self.operation_failed.emit(document_result.message)
            return result
        except AdmissionValidationError as exc:
            self._state()
            message = "\n".join(exc.errors)
            self.operation_failed.emit(message)
            return AdmissionResult(False, AdmissionFormState.ERROR, errors=exc.errors)
        except Exception as exc:
            self.service.set_state(AdmissionFormState.ERROR)
            self._state()
            self.operation_failed.emit(str(exc))
            return AdmissionResult(False, AdmissionFormState.ERROR, message=str(exc))

    @Slot(int, object)
    def update(self, attention_id: int, data) -> AdmissionResult:
        try:
            result = self.service.update(attention_id, data)
            self._state()
            if result.ok:
                self.attention_updated.emit(result.data)
                self.service.context.event_bus.attention_updated.emit(result.data)
                self.history_refresh_requested.emit()
            return result
        except Exception as exc:
            self.service.set_state(AdmissionFormState.ERROR)
            self._state()
            self.operation_failed.emit(str(exc))
            return AdmissionResult(False, AdmissionFormState.ERROR, message=str(exc))

    @Slot(int, str)
    def cancel(self, attention_id: int, reason: str) -> AdmissionResult:
        try:
            result = self.service.cancel(attention_id, reason)
            self._state()
            if result.ok:
                self.attention_cancelled.emit(result.data)
                self.service.context.event_bus.attention_cancelled.emit(result.data)
                self.history_refresh_requested.emit()
            return result
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return AdmissionResult(False, self.service.state, message=str(exc))

    @Slot()
    def refresh_shift(self):
        try:
            return self.service.detect_shift()
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return None

    @Slot(object)
    def open_shift(self, data):
        try:
            result = self.service.open_shift(data)
            if result.ok:
                self.shift_opened.emit(result.data)
            return result
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return AdmissionResult(False, self.service.state, message=str(exc))

    @Slot(object)
    def change_shift(self, data):
        try:
            result = self.service.change_shift(data)
            if result.ok:
                self.shift_opened.emit(result.data)
            return result
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return AdmissionResult(False, self.service.state, message=str(exc))

    @Slot()
    def close_shift(self):
        try:
            result = self.service.close_shift(
                actor=self.service.context.username,
                actor_role=self.service.context.role,
                session_id=self.service.context.session_id,
            )
            if result.ok:
                self.shift_closed.emit(result.data)
            return result
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return AdmissionResult(False, self.service.state, message=str(exc))

    @Slot()
    def refresh_shift_summary(self):
        try:
            summary = self.service.shift_summary()
            self.shift_summary_loaded.emit(summary)
            return summary
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return {}

    def load_history(self, filters):
        try:
            rows = self.service.history(**dict(filters or {}))
            self.history_loaded.emit(rows)
            return rows
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            raise

    def get_attention(self, attention_id: int):
        try:
            return self.service.attention(attention_id)
        except Exception as exc:
            self.operation_failed.emit(str(exc))
            return None

    @Slot(int)
    def request_detail_sheet(self, attention_id: int):
        self.detail_sheet_requested.emit(int(attention_id))
        if self.document_service is None:
            return None
        result = self.document_service.open(int(attention_id))
        if result.ok:
            self.detail_sheet_generated.emit(result)
        else:
            self.operation_failed.emit(result.message)
        return result

    def attach_document_service(self, document_service):
        self.document_service = document_service

    @staticmethod
    def _result_attention_id(value):
        if isinstance(value, dict):
            value = value.get("id") or value.get("atencion_id")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def print_detail_sheet(self, attention_id: int):
        if self.document_service is None:
            self.operation_failed.emit("El servicio documental no está conectado.")
            return None
        result = self.document_service.print(int(attention_id))
        if result.ok:
            self.detail_sheet_generated.emit(result)
        else:
            self.operation_failed.emit(result.message)
        return result
