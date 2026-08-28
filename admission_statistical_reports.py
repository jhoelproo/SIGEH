"""Canonical, side-effect-free dataset for Admission statistical reports.

The database reader decides which central rows exist.  This module only applies
report filters and derives every presentation (cards, preview, PDF and Excel)
from the same normalized tuple of rows.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from types import MappingProxyType
from typing import Any, Iterable, Mapping
import unicodedata


ARS_ALL = "TODAS"
ARS_INCLUDE = "INCLUIR"
ARS_EXCLUDE = "EXCLUIR"
ARS_MODES = frozenset({ARS_ALL, ARS_INCLUDE, ARS_EXCLUDE})

SPECIALTY_ALL = "TODAS"
COVERAGE_ALL = "TODAS"
COVERAGE_INSURED = "ASEGURADOS"
COVERAGE_UNINSURED = "SIN SEGURO"
COVERAGE_MODES = frozenset({COVERAGE_ALL, COVERAGE_INSURED, COVERAGE_UNINSURED})

ACTIVE_STATUSES = frozenset({"ACTIVA", "PENDIENTE"})
UNINSURED_STATUSES = frozenset(
    {"UNINSURED", "UNINSURED_DECLARED", "NO_ASEGURADO", "SIN SEGURO"}
)
# Dominican Republic has remained at UTC-04:00 without daylight-saving time
# since 1969.  A fixed offset avoids an optional ``tzdata`` dependency on
# Windows and preserves the hospital's civil time in frozen installations.
HOSPITAL_TIMEZONE = timezone(timedelta(hours=-4), name="America/Santo_Domingo")
OPERATIONAL_DAY_START = time(8, 0)


def _plain_text(value: Any) -> str:
    return str(value or "").strip()


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", _plain_text(value))
    return "".join(
        char for char in normalized if not unicodedata.combining(char)
    ).upper()


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "si", "sí"}
    return bool(value)


def _normalize_ars(value: Any) -> str:
    text = _plain_text(value).upper()
    return (
        text
        if text and _fold(text) not in {"SIN SEGURO", "NO ASEGURADO"}
        else "SIN SEGURO"
    )


def _normalize_specialty(value: Any) -> str:
    original = _plain_text(value).upper()
    folded = _fold(original)
    if folded.startswith("PED") or " PED" in folded:
        return "PEDIATRIA"
    if folded.startswith("GINE") or " GINE" in folded:
        return "GINECOLOGIA"
    if folded in {"GENERAL", "MEDICINA GENERAL", "EMERGENCIA GENERAL"}:
        return "GENERAL"
    return original or "SIN ESPECIALIDAD"


def _normalize_attention_type(value: Any) -> str:
    normalized = _fold(value or "EMERGENCIA")
    return (
        normalized
        if normalized in {"EMERGENCIA", "URGENCIA", "CONSULTA"}
        else "EMERGENCIA"
    )


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text_value = _plain_text(value)
        if not text_value:
            return None
        try:
            result = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        except ValueError:
            result = None
            for pattern in (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M",
            ):
                try:
                    result = datetime.strptime(text_value, pattern)
                    break
                except ValueError:
                    continue
            if result is None:
                return None
    if result.tzinfo is not None:
        return result.astimezone(HOSPITAL_TIMEZONE).replace(tzinfo=None)
    return result


def coerce_hospital_datetime(value: Any) -> datetime | None:
    """Return a naive hospital civil timestamp for UI/database boundary values."""
    return _parse_datetime(value)


def _record_datetime(row: Mapping[str, Any]) -> datetime | None:
    for key in ("dt_real", "created_at_effective_utc", "created_at"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    service_date = _plain_text(row.get("service_date") or row.get("fecha"))
    service_time = _plain_text(row.get("service_time") or row.get("hora") or "00:00:00")
    return _parse_datetime(f"{service_date} {service_time}")


@dataclass(frozen=True)
class OperationalPeriod:
    start_at: datetime
    end_at: datetime
    label: str


def build_operational_period(
    period_type: str,
    start_date: date,
    end_date: date | None = None,
) -> OperationalPeriod:
    """Resolve every calendar selection to hospital operational days at 08:00."""
    mode = _fold(period_type)
    if mode in {"DIARIO", "DIA", "DAILY"}:
        first = start_date
        last = start_date
        label = f"Día operativo {first:%d/%m/%Y}"
    elif mode in {"SEMANAL", "SEMANA", "WEEKLY"}:
        first = start_date - timedelta(days=start_date.weekday())
        last = first + timedelta(days=6)
        label = f"Semana {first:%d/%m/%Y} a {last:%d/%m/%Y}"
    elif mode in {"MENSUAL", "MES", "MONTHLY"}:
        first = start_date.replace(day=1)
        following = (
            date(first.year + 1, 1, 1)
            if first.month == 12
            else date(first.year, first.month + 1, 1)
        )
        last = following - timedelta(days=1)
        label = f"Mes {first:%m/%Y}"
    elif mode in {"ANUAL", "ANO", "YEARLY"}:
        first = date(start_date.year, 1, 1)
        last = date(start_date.year, 12, 31)
        label = f"Año {start_date.year}"
    else:
        first = start_date
        last = end_date or start_date
        if last < first:
            first, last = last, first
        label = f"Rango {first:%d/%m/%Y} a {last:%d/%m/%Y}"
    return OperationalPeriod(
        start_at=datetime.combine(first, OPERATIONAL_DAY_START),
        end_at=datetime.combine(last + timedelta(days=1), OPERATIONAL_DAY_START),
        label=label,
    )


def build_turn_operational_period(
    started_at: Any,
    *,
    fallback_date: date,
) -> OperationalPeriod:
    """Resolve a persisted turn start to its containing 08:00 operational day."""
    started = coerce_hospital_datetime(started_at)
    if started is None:
        base_day = fallback_date
    else:
        base_day = started.date()
        if started.time() < OPERATIONAL_DAY_START:
            base_day -= timedelta(days=1)
    start = datetime.combine(base_day, OPERATIONAL_DAY_START)
    return OperationalPeriod(
        start_at=start,
        end_at=start + timedelta(days=1),
        label=f"Turno operacional {base_day:%d/%m/%Y}",
    )


@dataclass(frozen=True)
class AdmissionReportFilters:
    start_at: datetime
    end_at: datetime
    period_label: str
    turn_label: str = "Todos los turnos"
    operational_source_id: str = ""
    turn_id: int | None = None
    specialty: str = SPECIALTY_ALL
    coverage: str = COVERAGE_ALL
    ars_mode: str = ARS_ALL
    selected_ars: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.end_at <= self.start_at:
            raise ValueError("El final del período debe ser posterior al inicio.")
        ars_mode = _fold(self.ars_mode)
        coverage = _fold(self.coverage)
        if ars_mode not in ARS_MODES:
            raise ValueError(f"Modo ARS no válido: {self.ars_mode}")
        if coverage not in COVERAGE_MODES:
            raise ValueError(f"Cobertura no válida: {self.coverage}")
        selected = tuple(
            dict.fromkeys(
                _normalize_ars(value)
                for value in self.selected_ars
                if _plain_text(value)
            )
        )
        if ars_mode in {ARS_INCLUDE, ARS_EXCLUDE} and not selected:
            raise ValueError("Seleccione al menos una ARS para incluir o excluir.")
        object.__setattr__(self, "ars_mode", ars_mode)
        object.__setattr__(self, "coverage", coverage)
        object.__setattr__(self, "specialty", _normalize_specialty(self.specialty))
        object.__setattr__(self, "selected_ars", selected)
        object.__setattr__(
            self, "operational_source_id", _plain_text(self.operational_source_id)
        )
        if self.turn_id is not None:
            object.__setattr__(self, "turn_id", int(self.turn_id))


@dataclass(frozen=True)
class AdmissionReportDataset:
    records: tuple[Mapping[str, Any], ...]
    summary: Mapping[str, Any]
    preview_rows: tuple[tuple[str, str, int, str, str, str], ...]
    diagnostics: Mapping[str, int]


def _first_truthy(row: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = row.get(key)
        if value:
            return value
    return default


def _coerce_int(value: Any, *, default: int | None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_record(row: Mapping[str, Any]) -> dict[str, Any] | None:
    occurred_at = _record_datetime(row)
    if occurred_at is None:
        return None
    ars = _normalize_ars(_first_truthy(row, "ars_display", "canonical_ars", "ars"))
    coverage_status = _fold(row.get("coverage_status"))
    uninsured = coverage_status in UNINSURED_STATUSES or ars == "SIN SEGURO"
    if uninsured:
        ars = "SIN SEGURO"
    specialty = _normalize_specialty(
        _first_truthy(row, "hoja_normalizada", "specialty", "hoja")
    )
    attention_id = _coerce_int(
        _first_truthy(row, "attention_id", "id", default=0), default=0
    )
    turn_id = _coerce_int(
        _first_truthy(row, "turn_id", "operational_turn_id"), default=None
    )
    name = _plain_text(_first_truthy(row, "patient_name", "nombre")) or "SIN NOMBRE"
    service_type = _normalize_attention_type(
        _first_truthy(row, "service_type", "tipo_atencion")
    )
    result = dict(row)
    result.update(
        {
            "attention_id": attention_id,
            "id": attention_id,
            "global_attention_id": _plain_text(row.get("global_attention_id")),
            "patient_name": name.upper(),
            "nombre": name.upper(),
            "occurred_at": occurred_at,
            "service_date": occurred_at.strftime("%Y-%m-%d"),
            "service_time": occurred_at.strftime("%H:%M:%S"),
            "fecha": occurred_at.strftime("%Y-%m-%d"),
            "hora": occurred_at.strftime("%H:%M:%S"),
            "canonical_ars": ars,
            "ars_display": ars,
            "ars": ars,
            "coverage_status": "UNINSURED_DECLARED" if uninsured else "INSURED",
            "is_insured": not uninsured,
            "specialty": specialty,
            "hoja_normalizada": specialty,
            "hoja": specialty,
            "service_type": service_type,
            "tipo_atencion": service_type,
            "operational_source_id": _plain_text(row.get("operational_source_id")),
            "turn_id": turn_id,
            "source_status": _fold(
                _first_truthy(row, "source_status", "estado", default="ACTIVA")
            ),
            "is_deleted": _truthy(row.get("is_deleted")),
        }
    )
    return result


def _scope_exclusion_reason(
    record: Mapping[str, Any], filters: AdmissionReportFilters
) -> str:
    if record["is_deleted"] or record["source_status"] not in ACTIVE_STATUSES:
        return "CANCELLED"
    if (
        filters.operational_source_id
        and record["operational_source_id"] != filters.operational_source_id
    ):
        return "WRONG_OPERATIONAL_SOURCE"
    if filters.turn_id is not None:
        return "WRONG_TURN" if record["turn_id"] != filters.turn_id else ""
    occurred_at = record["occurred_at"]
    if occurred_at < filters.start_at or occurred_at >= filters.end_at:
        return "OUTSIDE_PERIOD"
    return ""


def _specialty_exclusion_reason(
    record: Mapping[str, Any], filters: AdmissionReportFilters
) -> str:
    if filters.specialty == "OTRAS":
        if record["specialty"] in {"GENERAL", "PEDIATRIA", "GINECOLOGIA"}:
            return "WRONG_SPECIALTY"
    elif (
        filters.specialty != SPECIALTY_ALL and record["specialty"] != filters.specialty
    ):
        return "WRONG_SPECIALTY"
    return ""


def _coverage_exclusion_reason(
    record: Mapping[str, Any], filters: AdmissionReportFilters
) -> str:
    if filters.coverage == COVERAGE_INSURED and not record["is_insured"]:
        return "WRONG_COVERAGE"
    if filters.coverage == COVERAGE_UNINSURED and record["is_insured"]:
        return "WRONG_COVERAGE"
    return ""


def _ars_exclusion_reason(
    record: Mapping[str, Any], filters: AdmissionReportFilters
) -> str:
    selected = set(filters.selected_ars)
    if filters.ars_mode == ARS_INCLUDE and record["canonical_ars"] not in selected:
        return "ARS_NOT_INCLUDED"
    if filters.ars_mode == ARS_EXCLUDE and record["canonical_ars"] in selected:
        return "ARS_EXCLUDED"
    return ""


def _matches_filters(record: Mapping[str, Any], filters: AdmissionReportFilters) -> str:
    for exclusion in (
        _scope_exclusion_reason,
        _specialty_exclusion_reason,
        _coverage_exclusion_reason,
        _ars_exclusion_reason,
    ):
        reason = exclusion(record, filters)
        if reason:
            return reason
    return "INCLUDED"


def _percentage(value: int, total: int) -> float:
    return round(value * 100.0 / total, 2) if total else 0.0


def _build_summary(
    records: tuple[Mapping[str, Any], ...],
    filters: AdmissionReportFilters,
    generated_at: datetime,
) -> dict[str, Any]:
    by_ars = Counter(str(row["canonical_ars"]) for row in records)
    by_specialty = Counter(str(row["specialty"]) for row in records)
    by_type = Counter(str(row["service_type"]) for row in records)
    total = len(records)
    uninsured = sum(not bool(row["is_insured"]) for row in records)
    insured = total - uninsured
    selected_ars = (
        ", ".join(filters.selected_ars) if filters.selected_ars else "Ninguna"
    )
    summary = {
        "total_patients": total,
        "insured_patients": insured,
        "uninsured_patients": uninsured,
        "insured_percentage": _percentage(insured, total),
        "uninsured_percentage": _percentage(uninsured, total),
        "general_patients": by_specialty.get("GENERAL", 0),
        "pediatric_patients": by_specialty.get("PEDIATRIA", 0),
        "gynecology_patients": by_specialty.get("GINECOLOGIA", 0),
        "by_ars": tuple(sorted(by_ars.items(), key=lambda item: (-item[1], item[0]))),
        "by_specialty": tuple(
            sorted(by_specialty.items(), key=lambda item: (-item[1], item[0]))
        ),
        "by_attention_type": tuple(
            sorted(by_type.items(), key=lambda item: (-item[1], item[0]))
        ),
        "turn_label": filters.turn_label,
        "period_label": filters.period_label,
        "start_at": filters.start_at,
        "end_at": filters.end_at,
        "ars_mode": filters.ars_mode,
        "selected_ars": filters.selected_ars,
        "selected_ars_label": selected_ars,
        "coverage_filter": filters.coverage,
        "specialty_filter": filters.specialty,
        "operational_source_id": filters.operational_source_id,
        "turn_id": filters.turn_id,
        "generated_at": generated_at,
        "records": records,
        # Compatibility keys used by the existing PDF/turn report helpers.
        "registros": records,
        "total_general": total,
        "cantidad_sin_seguro": uninsured,
        "cantidad_urgencias": by_type.get("URGENCIA", 0),
        "cantidad_consultas": by_type.get("CONSULTA", 0),
        "por_seguro": tuple(
            sorted(by_ars.items(), key=lambda item: (-item[1], item[0]))
        ),
        "por_especialidad": tuple(
            sorted(by_specialty.items(), key=lambda item: (-item[1], item[0]))
        ),
        "periodo_texto": filters.period_label,
        "turno_resumen": filters.turn_label,
        "representante": "",
    }
    return summary


def _build_preview(
    summary: Mapping[str, Any],
) -> tuple[tuple[str, str, int, str, str, str], ...]:
    mode = str(summary["ars_mode"])
    turn = str(summary["turn_label"])
    rows = [
        (
            "TOTAL GENERAL",
            "Total pacientes",
            int(summary["total_patients"]),
            mode,
            turn,
            "Dataset filtrado",
        ),
        (
            "COBERTURA",
            "Asegurados",
            int(summary["insured_patients"]),
            mode,
            turn,
            "Con cobertura",
        ),
        (
            "COBERTURA",
            "Sin seguro",
            int(summary["uninsured_patients"]),
            mode,
            turn,
            "Sin cobertura",
        ),
    ]
    rows.extend(
        (
            "ESPECIALIDAD",
            specialty,
            int(count),
            mode,
            turn,
            "Atenciones por especialidad",
        )
        for specialty, count in summary["by_specialty"]
    )
    rows.extend(
        ("ARS", ars, int(count), ars, turn, "Pacientes por ARS")
        for ars, count in summary["by_ars"]
    )
    return tuple(rows)


def build_admission_report_dataset(
    rows: Iterable[Mapping[str, Any]],
    filters: AdmissionReportFilters,
    *,
    generated_at: datetime | None = None,
) -> AdmissionReportDataset:
    diagnostics: Counter[str] = Counter()
    selected: list[dict[str, Any]] = []
    for raw in rows or ():
        normalized = _normalize_record(dict(raw or {}))
        if normalized is None:
            diagnostics["INVALID_TIMESTAMP"] += 1
            continue
        reason = _matches_filters(normalized, filters)
        diagnostics[reason] += 1
        if reason == "INCLUDED":
            selected.append(normalized)
    selected.sort(
        key=lambda row: (
            row["occurred_at"],
            int(row["attention_id"]),
            str(row["global_attention_id"]),
        )
    )
    immutable_records = tuple(MappingProxyType(row) for row in selected)
    summary = _build_summary(
        immutable_records,
        filters,
        generated_at or datetime.now(),
    )
    preview = _build_preview(summary)
    return AdmissionReportDataset(
        records=immutable_records,
        summary=MappingProxyType(summary),
        preview_rows=preview,
        diagnostics=MappingProxyType(dict(diagnostics)),
    )


def search_ars_catalog(catalog: Iterable[str], query: str) -> tuple[str, ...]:
    folded_query = _fold(query)
    values = tuple(sorted({_plain_text(item) for item in catalog if _plain_text(item)}))
    if not folded_query:
        return values
    return tuple(item for item in values if folded_query in _fold(item))


__all__ = [
    "ARS_ALL",
    "ARS_EXCLUDE",
    "ARS_INCLUDE",
    "COVERAGE_ALL",
    "COVERAGE_INSURED",
    "COVERAGE_UNINSURED",
    "SPECIALTY_ALL",
    "AdmissionReportDataset",
    "AdmissionReportFilters",
    "OperationalPeriod",
    "build_admission_report_dataset",
    "build_operational_period",
    "build_turn_operational_period",
    "coerce_hospital_datetime",
    "search_ars_catalog",
]
