from __future__ import annotations

from datetime import datetime
import os
import re
import unicodedata
from typing import Any, Callable, Mapping


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().upper())
    return "".join(char for char in text if unicodedata.category(char) != "Mn")


class LegacyAdmissionBackend:
    """Adapta DatabaseManager original sin crear UI Tk ni reescribir sus consultas."""

    def __init__(
        self,
        database_manager: Any,
        *,
        shift_provider: Callable[[], Mapping[str, Any] | None],
        username_provider: Callable[[], str],
        module: Any | None = None,
    ):
        self.database = database_manager
        self.shift_provider = shift_provider
        self.username_provider = username_provider
        self.module = module

    @staticmethod
    def _sheet(value: Any) -> str:
        value = _plain(value)
        return {
            "PEDIATRIA": "PEDIATRIA",
            "GINECOLOGIA": "GINECOLOGIA",
        }.get(value, "GENERAL")

    def _legacy_data(self, data: Mapping[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        return {
            "Nombre": str(data.get("name") or "").strip(),
            "Sexo": str(data.get("sex") or "Femenino"),
            "Edad_num": int(data.get("age") or 0),
            "Unidad": str(data.get("age_unit") or "Años"),
            "Cédula": str(data.get("cedula") or ""),
            "Teléfono": str(data.get("phone") or ""),
            "Dirección": str(data.get("address") or ""),
            "Nacionalidad": str(data.get("nationality") or ""),
            "Aseguradora (ARS)": str(data.get("ars") or ""),
            "NSS": str(data.get("nss") or ""),
            "TipoAtencion": str(data.get("attention_type") or "EMERGENCIA").upper(),
            "Hoja": self._sheet(data.get("specialty")),
            "Fecha": str(data.get("service_date") or now.strftime("%d/%m/%Y")),
            "Hora": str(data.get("service_time") or now.strftime("%I:%M %p")),
        }

    def search_patients(self, text: str, *, limit=80, offset=0):
        rows = self.database.buscar_pacientes_avanzado(str(text), limite=limit + offset)
        return list(rows or [])[int(offset): int(offset) + int(limit)]

    def find_duplicate(self, data: Mapping[str, Any], shift: Mapping[str, Any]):
        shift = dict(shift or self.shift_provider() or {})
        if not shift:
            return None
        if self.module and hasattr(self.module, "obtener_rango_turno_efectivo"):
            start, end = self.module.obtener_rango_turno_efectivo(shift)
        else:
            start = shift.get("fecha_inicio") or shift.get("start")
            end = shift.get("fecha_fin") or shift.get("end")
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            return None
        return self.database.buscar_atencion_en_turno(
            data.get("nss", ""),
            data.get("cedula", ""),
            start,
            end,
            turno_id=shift.get("turno_id") or shift.get("id"),
            nombre=data.get("name", ""),
            telefono=data.get("phone", ""),
            dia_operativo_id=shift.get("dia_operativo_id"),
        )

    def register_attention(self, data: Mapping[str, Any]):
        shift = dict(self.shift_provider() or {})
        legacy = self._legacy_data(data)
        return self.database.guardar_atencion(
            legacy, legacy["Hoja"], turno_cfg=shift
        )

    def update_attention(self, attention_id: int, data: Mapping[str, Any]):
        legacy = self._legacy_data(data)
        return self.database.actualizar_atencion_especifica(
            int(attention_id),
            legacy,
            actualizar_ficha=False,
            usuario=self.username_provider(),
            motivo="Corrección desde Admisión PySide6",
        )

    def cancel_attention(self, attention_id: int, actor: str, reason: str):
        return self.database.borrar_atencion(
            int(attention_id), motivo=str(reason), usuario=str(actor)
        )

    def get_attention(self, attention_id: int):
        return self.database.obtener_atencion_por_id(int(attention_id))

    def list_history(self, **filters):
        return self.database.listar_atenciones_filtradas(
            filtro_texto=filters.get("text"),
            modo=filters.get("mode", "Todos"),
            ars=filters.get("ars"),
            especialidad=filters.get("specialty"),
            fecha_txt=filters.get("date"),
            limite=filters.get("limit", 100),
            offset=filters.get("offset", 0),
            turno_id=filters.get("shift_id"),
        )

    def detect_shift(self):
        return dict(self.shift_provider() or {})

    def open_shift(self, data: Mapping[str, Any]):
        return self.database.obtener_o_crear_turno(dict(data))

    def close_shift(self, shift_id: int, **metadata):
        shift = dict(metadata.pop("shift", None) or self.shift_provider() or {})
        return self.database.cerrar_turno_existente(shift, **metadata)

    def change_shift(self, current_shift: Mapping[str, Any], new_shift: Mapping[str, Any]):
        change_method = getattr(self.database, "cambiar_turno", None)
        if callable(change_method):
            return change_method(dict(current_shift or {}), dict(new_shift or {}))
        # obtener_o_crear_turno conserva la lógica de unicidad del repositorio
        # original. El cierre documental completo se mantiene como una operación
        # separada y explícita para no ejecutarlo accidentalmente desde la vista.
        return self.database.obtener_o_crear_turno(dict(new_shift or {}))

    def shift_summary(self, shift_id=None):
        shift = dict(self.shift_provider() or {})
        method = self.database.resumen_turno_actual
        try:
            return method(shift)
        except TypeError:
            return method()

    def list_representatives(self):
        return self.database.listar_representantes()

    def list_pending_prints(self):
        return self.database.listar_trabajos_salida_pendientes()

    @staticmethod
    def _snapshot_data(attention: Mapping[str, Any]) -> dict[str, Any]:
        value = dict(attention or {})
        return {
            "Nombre": value.get("nombre", ""),
            "Sexo": value.get("sexo", ""),
            "Edad_num": value.get("edad_num", 0),
            "Unidad": value.get("unidad", "Años"),
            "Cédula": value.get("cedula", ""),
            "Teléfono": value.get("telefono", "") or "",
            "Dirección": value.get("direccion", ""),
            "Nacionalidad": value.get("nacionalidad", ""),
            "Aseguradora (ARS)": value.get("ars", ""),
            "NSS": value.get("nss", ""),
            "TipoAtencion": value.get("tipo_atencion", "EMERGENCIA"),
            "Fecha": value.get("fecha", ""),
            "Hora": value.get("hora", ""),
        }

    def _require_module_function(self, name: str):
        function = getattr(self.module, name, None) if self.module else None
        if not callable(function):
            raise RuntimeError(f"El motor original no expone {name}.")
        return function

    def generate_detail_sheet(self, attention_id: int):
        attention = self.database.obtener_atencion_por_id(int(attention_id))
        if not attention:
            raise RuntimeError("No se encontró la atención seleccionada.")
        sheet = self._sheet(attention.get("hoja") or "GENERAL")
        renderer = self._require_module_function("crear_pdf_temporal")
        path = renderer(sheet, self._snapshot_data(attention), mostrar_error=False)
        if not path:
            raise RuntimeError("No fue posible generar temporalmente la hoja.")
        return path

    def open_document(self, path: str):
        result = self._require_module_function("abrir_pdf")(str(path))
        return result is not False

    def print_document(self, path: str, copies: int = 1):
        return bool(
            self._require_module_function("imprimir_pdf")(
                str(path), copias=max(1, int(copies)), mostrar_error=False
            )
        )

    def schedule_document_cleanup(self, path: str, delay: int = 900):
        return self._require_module_function("programar_limpieza_pdf_temporal")(
            str(path), espera_segundos=max(1, int(delay))
        )

    def update_print_state(self, attention_id: int, state: str, **metadata):
        return self.database.actualizar_trabajo_salida(
            int(attention_id),
            "impresion",
            str(state).upper(),
            error=str(metadata.get("error") or ""),
            incrementar_intento=bool(metadata.get("increment_attempt")),
        )

    def report_summary(self, start, end, label: str):
        records = self.database.obtener_atenciones_para_reporte(start, end)
        shift_summary, representative = self.database.obtener_metadatos_reporte(records)
        builder = self._require_module_function("construir_resumen_desde_registros")
        return builder(
            records,
            str(label),
            turno_resumen=shift_summary,
            representante=representative,
        )

    def generate_report_pdf(self, summary: Mapping[str, Any]):
        return self._require_module_function("crear_pdf_reporte")(dict(summary or {}))

    def generate_report_excel(self, summary: Mapping[str, Any]):
        return self._require_module_function("crear_excel_reporte_estadistico")(
            dict(summary or {})
        )

    def open_current_excel(self):
        self._require_module_function("verificar_o_crear_excel")()
        path = str(getattr(self.module, "EXCEL_PATH", "") or "")
        if not path or not os.path.isfile(path):
            raise RuntimeError("No se encontró el listado Excel operativo.")
        if hasattr(os, "startfile"):
            os.startfile(path)
        else:
            self._require_module_function("abrir_pdf")(path)
        return path

    def load_preferences(self):
        return dict(self._require_module_function("cargar_app_settings")() or {})

    def save_preferences(self, values: Mapping[str, Any]):
        return self._require_module_function("guardar_app_settings")(
            dict(values or {})
        )

    def configuration_snapshot(self):
        ars = list(self.database.listar_ars_conteo() or [])
        representatives = list(self.database.listar_representantes() or [])
        reviews = list(self.database.listar_revisiones_nss(solo_pendientes=True, limite=250) or [])
        backups = []
        manager = getattr(self.database, "backup_manager", None)
        if manager is not None:
            for folder in manager.list_backups():
                try:
                    manifest = manager.verify(folder)
                    backups.append(
                        {
                            "path": str(folder),
                            "created_at": manifest.get("created_at", ""),
                            "reason": manifest.get("reason", ""),
                            "status": "Válido",
                        }
                    )
                except Exception as exc:
                    backups.append(
                        {"path": str(folder), "reason": str(exc), "status": "Inválido"}
                    )
        return {
            "ars": ars,
            "representatives": representatives,
            "nss_reviews": reviews,
            "backups": backups,
        }
