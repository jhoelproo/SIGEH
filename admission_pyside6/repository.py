from __future__ import annotations

from typing import Any, Iterable, Mapping


class AdmissionRepositoryError(RuntimeError):
    pass


class AdmissionRepository:
    """Adaptador sin SQL para los contratos estables de la Admisión original."""

    def __init__(self, backend: Any):
        if backend is None:
            raise ValueError("AdmissionRepository requiere un backend explícito.")
        self.backend = backend

    def _call(self, names: Iterable[str], *args, **kwargs):
        for name in names:
            method = getattr(self.backend, name, None)
            if callable(method):
                try:
                    return method(*args, **kwargs)
                except AdmissionRepositoryError:
                    raise
                except Exception as exc:
                    raise AdmissionRepositoryError(str(exc)) from exc
        raise AdmissionRepositoryError(
            "El backend no implementa el contrato: " + "/".join(names)
        )

    def register_attention(self, data: Mapping[str, Any]) -> Any:
        return self._call(("register_attention", "guardar_atencion"), dict(data))

    def update_attention(self, attention_id: int, data: Mapping[str, Any]) -> Any:
        return self._call(
            ("update_attention", "actualizar_atencion_especifica"),
            int(attention_id),
            dict(data),
        )

    def cancel_attention(self, attention_id: int, actor: str, reason: str) -> Any:
        return self._call(
            ("cancel_attention", "borrar_atencion"),
            int(attention_id),
            str(actor),
            str(reason),
        )

    def search_patients(self, text: str, *, limit: int = 80, offset: int = 0) -> list[Any]:
        result = self._call(
            ("search_patients", "buscar_pacientes_avanzado"),
            str(text),
            limit=int(limit),
            offset=int(offset),
        )
        return list(result or [])

    def get_attention(self, attention_id: int) -> Any:
        return self._call(
            ("get_attention", "obtener_atencion_por_id"), int(attention_id)
        )

    def list_history(self, **filters) -> list[Any]:
        result = self._call(
            ("list_history", "listar_atenciones_filtradas"), **filters
        )
        return list(result or [])

    def find_duplicate(self, data: Mapping[str, Any], shift: Mapping[str, Any]) -> Any:
        return self._call(
            ("find_duplicate", "buscar_atencion_en_turno"),
            dict(data),
            dict(shift or {}),
        )

    def detect_shift(self) -> Any:
        return self._call(("detect_shift", "obtener_turno_actual"))

    def open_shift(self, data: Mapping[str, Any]) -> Any:
        return self._call(("open_shift", "obtener_o_crear_turno"), dict(data))

    def close_shift(self, shift_id: int, **metadata) -> Any:
        return self._call(
            ("close_shift", "cerrar_turno_existente"), int(shift_id), **metadata
        )

    def change_shift(self, current_shift: Mapping[str, Any], new_shift: Mapping[str, Any]) -> Any:
        return self._call(
            ("change_shift",), dict(current_shift or {}), dict(new_shift or {})
        )

    def shift_summary(self, shift_id: int | None = None) -> Any:
        return self._call(("shift_summary", "resumen_turno_actual"), shift_id)

    def list_representatives(self) -> list[Any]:
        return list(
            self._call(("list_representatives", "listar_representantes")) or []
        )

    def list_pending_prints(self) -> list[Any]:
        return list(
            self._call(
                ("list_pending_prints", "listar_trabajos_salida_pendientes")
            )
            or []
        )

    def generate_detail_sheet(self, attention_id: int) -> str:
        return str(self._call(("generate_detail_sheet",), int(attention_id)) or "")

    def open_document(self, path: str) -> bool:
        return bool(self._call(("open_document",), str(path)))

    def print_document(self, path: str, copies: int = 1) -> bool:
        return bool(self._call(("print_document",), str(path), copies=max(1, int(copies))))

    def schedule_document_cleanup(self, path: str, *, delay: int = 900) -> Any:
        return self._call(("schedule_document_cleanup",), str(path), delay=int(delay))

    def update_print_state(self, attention_id: int, state: str, **metadata) -> Any:
        return self._call(
            ("update_print_state",), int(attention_id), str(state), **metadata
        )

    def report_summary(self, start, end, label: str) -> Mapping[str, Any]:
        return dict(self._call(("report_summary",), start, end, str(label)) or {})

    def generate_report_pdf(self, summary: Mapping[str, Any]) -> str:
        return str(self._call(("generate_report_pdf",), dict(summary or {})) or "")

    def generate_report_excel(self, summary: Mapping[str, Any]) -> str:
        return str(self._call(("generate_report_excel",), dict(summary or {})) or "")

    def open_current_excel(self) -> str:
        return str(self._call(("open_current_excel",)) or "")

    def load_preferences(self) -> Mapping[str, Any]:
        return dict(self._call(("load_preferences",)) or {})

    def save_preferences(self, values: Mapping[str, Any]) -> Any:
        return self._call(("save_preferences",), dict(values or {}))

    def configuration_snapshot(self) -> Mapping[str, Any]:
        return dict(self._call(("configuration_snapshot",)) or {})
