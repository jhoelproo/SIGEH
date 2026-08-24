from __future__ import annotations

import re
import uuid
from typing import Any, Mapping

from .context import AppContext
from .models import AdmissionFormState, AdmissionInput, AdmissionResult
from .repository import AdmissionRepository


class AdmissionValidationError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(str(item) for item in errors)
        super().__init__("; ".join(self.errors))


class AdmissionService:
    def __init__(self, context: AppContext, repository: AdmissionRepository):
        self.context = context
        self.repository = repository
        self.state = AdmissionFormState.NEW

    @staticmethod
    def _digits(value: Any) -> str:
        return re.sub(r"\D", "", str(value or ""))

    def set_state(self, state: AdmissionFormState) -> AdmissionFormState:
        self.state = AdmissionFormState(state)
        return self.state

    def _require_write(self) -> None:
        decision = self.context.can_write_admission()
        if decision is not None and not decision.allowed:
            self.set_state(AdmissionFormState.READ_ONLY)
            raise PermissionError(decision.message)
        store = self.context.sync_store
        session = self.context.operational_session
        if store is not None and session is not None:
            store.configure_runtime_context(session, device_id=self.context.device_id)

    def _queue_sync(self, operation: str, result: Any, data: Mapping[str, Any]) -> None:
        """Encola toda mutación dentro del flujo local; fallar la red no la pierde."""
        store = self.context.sync_store
        session = self.context.operational_session
        if store is None or session is None:
            return
        # La SQLite V15 usa triggers: atención y outbox se confirman juntas.
        if bool(getattr(store, "automatic_outbox", False)):
            return
        record = dict(result or {}) if isinstance(result, Mapping) else {}
        attention_id = record.get("id") or record.get("atencion_id") or result
        if attention_id in (None, "", False):
            return
        entity_uuid = str(
            record.get("global_attention_id")
            or uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hospital-admission:{getattr(session, 'operational_source_id', '')}:{attention_id}",
            )
        )
        payload = dict(data or {})
        payload.update({
            "attention_id": attention_id,
            "global_attention_id": entity_uuid,
            "operational_source_id": getattr(session, "operational_source_id", ""),
            "origin_device_id": self.context.device_id,
        })
        event = store.make_event(
            entity_type="attention",
            entity_uuid=entity_uuid,
            operation=operation,
            payload=payload,
            session=session,
            device_id=self.context.device_id,
            base_version=int(record.get("version") or 0),
        )
        store.queue_sync_event(event)

    def validate(self, value: AdmissionInput | Mapping[str, Any]) -> AdmissionInput:
        data = value if isinstance(value, AdmissionInput) else AdmissionInput.from_mapping(value)
        errors: list[str] = []
        data.name = re.sub(r"\s+", " ", str(data.name or "").strip())
        if not data.name:
            errors.append("El nombre del paciente es obligatorio.")
        if data.age is None or not 0 <= int(data.age) <= 130:
            errors.append("La edad debe estar entre 0 y 130.")
        if data.sex not in {"Femenino", "Masculino"}:
            errors.append("El sexo no es válido.")
        if str(data.attention_type).upper() not in {"EMERGENCIA", "CONSULTA"}:
            errors.append("El tipo de atención no es válido.")
        cedula = self._digits(data.cedula)
        allow_missing_cedula = bool(
            self.context.configuration.get("validation_allow_missing_cedula", True)
        )
        if not cedula and not allow_missing_cedula:
            errors.append("La cédula es obligatoria.")
        elif cedula and (len(cedula) != 11 or cedula == "00000000000"):
            errors.append("La cédula debe contener 11 dígitos válidos.")
        phone = self._digits(data.phone)
        allow_missing_phone = bool(
            self.context.configuration.get("validation_allow_missing_phone", False)
        )
        if not phone and not allow_missing_phone:
            errors.append("El teléfono es obligatorio.")
        elif phone and len(phone) != 10:
            errors.append("El teléfono debe contener 10 dígitos.")
        nss = self._digits(data.nss)
        if data.nss and not nss:
            errors.append("El NSS no es válido.")
        if nss and set(nss) == {"0"}:
            errors.append("El NSS no puede contener solo ceros.")
        if not str(data.ars or "").strip():
            errors.append("La ARS o condición sin seguro es obligatoria.")
        if errors:
            self.set_state(AdmissionFormState.ERROR)
            raise AdmissionValidationError(errors)
        return data

    def search_patients(self, text: str, *, limit=80, offset=0) -> list[Any]:
        return self.repository.search_patients(text.strip(), limit=limit, offset=offset)

    def register(self, value: AdmissionInput | Mapping[str, Any]) -> AdmissionResult:
        self._require_write()
        data = self.validate(value)
        shift = dict(self.context.current_shift or {})
        if not shift:
            self.set_state(AdmissionFormState.ERROR)
            return AdmissionResult(False, self.state, message="No existe un turno vigente.")
        duplicate = self.repository.find_duplicate(data.as_mapping(), shift)
        if duplicate:
            self.set_state(AdmissionFormState.READ_ONLY)
            return AdmissionResult(
                False,
                self.state,
                data=duplicate,
                message="La atención ya está registrada en el turno.",
            )
        self.set_state(AdmissionFormState.SAVING)
        result = self.repository.register_attention(data.as_mapping())
        self._queue_sync("CREATE", result, data.as_mapping())
        self.set_state(AdmissionFormState.NEW)
        return AdmissionResult(True, self.state, data=result)

    def update(self, attention_id: int, value) -> AdmissionResult:
        self._require_write()
        data = self.validate(value)
        self.set_state(AdmissionFormState.SAVING)
        result = self.repository.update_attention(attention_id, data.as_mapping())
        self._queue_sync("UPDATE", result or {"id": attention_id}, data.as_mapping())
        self.set_state(AdmissionFormState.EDITING)
        return AdmissionResult(True, self.state, data=result)

    def cancel(self, attention_id: int, reason: str) -> AdmissionResult:
        self._require_write()
        if not self.context.has_permission("admission.cancel"):
            return AdmissionResult(False, self.state, message="Permiso insuficiente.")
        if len(str(reason or "").strip()) < 5:
            return AdmissionResult(False, self.state, message="Indique un motivo válido.")
        result = self.repository.cancel_attention(
            attention_id, self.context.username, reason.strip()
        )
        self._queue_sync("CANCEL", result or {"id": attention_id}, {"reason": reason.strip()})
        self.set_state(AdmissionFormState.READ_ONLY)
        return AdmissionResult(True, self.state, data=result)

    def detect_shift(self) -> Any:
        shift = self.repository.detect_shift()
        self.context.set_shift(shift or {})
        return shift

    def open_shift(self, data: Mapping[str, Any]) -> AdmissionResult:
        result = self.repository.open_shift(dict(data or {}))
        self.context.set_shift(result or data or {})
        return AdmissionResult(True, self.state, data=result)

    def change_shift(self, data: Mapping[str, Any]) -> AdmissionResult:
        decision = self.context.can_write_admission()
        if decision is not None:
            self.context.write_guard.require_primary_turn_change(
                login_user=self.context.username,
                device_id=self.context.device_id,
                session=self.context.operational_session,
                generation=getattr(self.context.operational_session, "generation", None),
                role=self.context.station_role,
                offline=bool(self.context.offline),
                offline_lease_valid=bool(self.context.offline_lease_valid),
            )
        current = dict(self.context.current_shift or {})
        result = self.repository.change_shift(current, dict(data or {}))
        self.context.set_shift(result or data or {})
        return AdmissionResult(True, self.state, data=result)

    def close_shift(self, **metadata) -> AdmissionResult:
        decision = self.context.can_write_admission()
        if decision is not None:
            self.context.write_guard.require_primary_turn_change(
                login_user=self.context.username,
                device_id=self.context.device_id,
                session=self.context.operational_session,
                generation=getattr(self.context.operational_session, "generation", None),
                role=self.context.station_role,
                offline=bool(self.context.offline),
                offline_lease_valid=bool(self.context.offline_lease_valid),
            )
        current = dict(self.context.current_shift or {})
        shift_id = current.get("turno_id") or current.get("id")
        if not shift_id:
            return AdmissionResult(False, self.state, message="No existe un turno abierto.")
        result = self.repository.close_shift(int(shift_id), shift=current, **metadata)
        if result:
            self.context.set_shift({})
        return AdmissionResult(bool(result), self.state, data=result)

    def shift_summary(self) -> Mapping[str, Any]:
        current = dict(self.context.current_shift or {})
        shift_id = current.get("turno_id") or current.get("id")
        return dict(self.repository.shift_summary(shift_id) or {})

    def representatives(self) -> list[Any]:
        return self.repository.list_representatives()

    def history(self, **filters) -> list[Any]:
        filters = dict(filters or {})
        filters["text"] = str(filters.get("text") or "").strip()
        filters["mode"] = str(filters.get("mode") or "Turno actual").strip()
        filters["limit"] = max(1, min(int(filters.get("limit") or 100), 250))
        filters["offset"] = max(0, int(filters.get("offset") or 0))
        return self.repository.list_history(**filters)

    def attention(self, attention_id: int) -> Any:
        return self.repository.get_attention(int(attention_id))
