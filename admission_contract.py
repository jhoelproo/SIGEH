"""Contrato versionado entre Emergencias y Facturación.

Este módulo no conoce Qt, Tk ni PostgreSQL. Centraliza las reglas que deciden
si una atención de Emergencia está completa para pasar a Facturación.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Any


CONTRACT_VERSION = 2

SERVICE_EMERGENCY = "EMERGENCIA"
SERVICE_CONSULTATION = "CONSULTA"
SUPPORTED_SERVICE_TYPES = {SERVICE_EMERGENCY, SERVICE_CONSULTATION}

SPECIALTY_EMERGENCY = "EMERGENCIOLOGÍA"
SPECIALTY_PEDIATRICS = "PEDIATRÍA"
SPECIALTY_GYNECOLOGY = "GINECOLOGÍA"
SUPPORTED_SPECIALTIES = {SPECIALTY_EMERGENCY, SPECIALTY_PEDIATRICS, SPECIALTY_GYNECOLOGY}

COVERAGE_INSURED_VERIFIED = "ASEGURADO_VALIDADO"
COVERAGE_UNINSURED_DECLARED = "SIN_SEGURO_DECLARADO"
COVERAGE_INCOMPLETE = "COBERTURA_INCOMPLETA"
COVERAGE_REVIEW = "COBERTURA_EN_REVISION"

READINESS_READY = "LISTA"
READINESS_INCOMPLETE = "INCOMPLETA"
READINESS_REVIEW = "REVISION"


def normalize_key(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().upper())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return re.sub(r"[^A-Z0-9]+", "", text)


def normalize_digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


CANONICAL_ARS_ALIASES = {
    "SENASA SUBSIDIADO": (
        "SUB", "SUBS", "SUBSIDIADO", "SENASA SUB", "SENASA SUBSIDIADO",
        "SANASA SUB", "SUNB",
    ),
    "SENASA CONTRIBUTIVO": (
        "CONT", "CONTR", "CONTRI", "CONTRIB", "CONTRIBUTIVO",
        "SENASA CONT", "SENASA CONTRIBUTIVO",
    ),
    "SENASA PENSIONADOS": (
        "PENS", "PENSIONADO", "PENSIONADOS", "SENASA PENSIONADOS",
        "SENASA JUBILADOS",
    ),
    "APS": ("APS",),
    "ASEMAP": ("ASEMAP", "ARS ASEMAP"),
    "CMD": ("CMD",),
    "GMA": ("GMA", "GRUPO MEDICO"),
    "RENACER": ("RENACER", "ARS RENACER"),
    "RESERVAS": ("RESERVAS", "RESERVA", "BANRESERVA", "BANRESERVAS"),
    "SEMMA": ("SEMMA",),
    "FUTURO": ("FUTURO", "FUT"),
    "HUMANO": ("HUMANO", "HUM", "HUMANO PRIMARIA"),
    "PRIMERA": ("PRIMERA", "ARS PRIMERA"),
    "ABEL GONZALEZ/SIMAG": (
        "ABEL", "ARS ABEL", "SIMAG", "ABEL GONZALEZ/SIMAG",
    ),
    "METASALUD": ("METASALUD", "META SALUD"),
    "MONUMENTAL": ("MONUMENTAL",),
    "MAPFRE/PALIC": ("MAPFRE", "PALIC", "MAPFRE/PALIC", "MAPFRE SALUD"),
    "UNIVERSAL": ("UNIVERSAL",),
    "BANCO CENTRAL": ("BANCO CENTRAL",),
    "YUNEN": ("YUNEN",),
}

ARS_LOOKUP = {
    normalize_key(alias): canonical
    for canonical, aliases in CANONICAL_ARS_ALIASES.items()
    for alias in (canonical, *aliases)
}

DECLARED_UNINSURED_KEYS = {
    normalize_key(value)
    for value in (
        "SIN SEGURO", "N/S", "NS", "NO", "NO TIENE", "NO POSEE",
        "NINGUNO", "NINGUNA", "N/A", "S/N",
    )
}

PENDING_COVERAGE_KEYS = {
    normalize_key(value)
    for value in (
        "", "PEND", "PENDI", "PENDIENTE", "INACTIVO", "INACTIVA",
        "NO VIGENTE", "VENCIDO", "CANCELADO", "DESAFILIADO",
    )
}


@dataclass(frozen=True)
class CoverageAssessment:
    status: str
    canonical_ars: str
    reason: str

    @property
    def uninsured(self) -> bool:
        return self.status == COVERAGE_UNINSURED_DECLARED

    @property
    def complete(self) -> bool:
        return self.status in {
            COVERAGE_INSURED_VERIFIED,
            COVERAGE_UNINSURED_DECLARED,
        }


@dataclass(frozen=True)
class ReadinessAssessment:
    status: str
    reasons: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return self.status == READINESS_READY


def canonicalize_ars(value: Any) -> str:
    return ARS_LOOKUP.get(normalize_key(value), "")


def normalize_service_type(value: Any) -> str:
    key = normalize_key(value)
    if key in {"CONSULTA", "CONSULTAS", "AMBULATORIA", "AMBULATORIO"}:
        return SERVICE_CONSULTATION
    return SERVICE_EMERGENCY


def normalize_specialty(value: Any) -> str:
    key = normalize_key(value)
    if key in {"PEDIATRIA", "PEDIATRICA", "PEDIATRICO"}:
        return SPECIALTY_PEDIATRICS
    if key in {"GINECOLOGIA", "GINECOLOGICA", "GINECOLOGICO"}:
        return SPECIALTY_GYNECOLOGY
    return SPECIALTY_EMERGENCY


def assess_coverage(ars: Any, nss: Any) -> CoverageAssessment:
    ars_key = normalize_key(ars)
    nss_digits = normalize_digits(nss)

    if ars_key in DECLARED_UNINSURED_KEYS:
        return CoverageAssessment(
            COVERAGE_UNINSURED_DECLARED,
            "SIN SEGURO",
            "La condición sin seguro está declarada explícitamente.",
        )

    canonical = canonicalize_ars(ars)
    if canonical:
        if len(nss_digits) >= 6 and set(nss_digits) != {"0"}:
            return CoverageAssessment(
                COVERAGE_INSURED_VERIFIED,
                canonical,
                "ARS reconocida y NSS disponible.",
            )
        return CoverageAssessment(
            COVERAGE_INCOMPLETE,
            canonical,
            "La ARS fue reconocida, pero falta un NSS válido.",
        )

    if ars_key in PENDING_COVERAGE_KEYS:
        return CoverageAssessment(
            COVERAGE_INCOMPLETE,
            "",
            "La cobertura todavía está pendiente o incompleta.",
        )

    return CoverageAssessment(
        COVERAGE_REVIEW,
        "",
        "La ARS no tiene una equivalencia canónica aprobada.",
    )


def assess_billing_readiness(
    *,
    name: Any,
    service_date: Any,
    attention_type: Any,
    coverage: CoverageAssessment,
    cedula: Any = "",
) -> ReadinessAssessment:
    reasons: list[str] = []
    if not str(name or "").strip():
        reasons.append("Falta el nombre del paciente.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(service_date or "")):
        reasons.append("La fecha de servicio no es válida.")
    service_type = normalize_service_type(attention_type)
    if service_type not in SUPPORTED_SERVICE_TYPES:
        reasons.append("La atención no pertenece a Emergencias.")
    if not coverage.complete:
        reasons.append(coverage.reason)
    # La cédula es un identificador auxiliar, no una condición de elegibilidad.
    # Se mantiene el parámetro por compatibilidad con la fuente V15.

    if not reasons:
        return ReadinessAssessment(READINESS_READY, ())
    if coverage.status == COVERAGE_REVIEW:
        return ReadinessAssessment(READINESS_REVIEW, tuple(reasons))
    return ReadinessAssessment(READINESS_INCOMPLETE, tuple(reasons))


def source_metadata(connection, db_path: str | Path) -> tuple[str, int]:
    schema_version = 0
    try:
        row = connection.execute(
            "SELECT version FROM schema_version WHERE id=1"
        ).fetchone()
        schema_version = int(row[0]) if row else 0
    except Exception:
        schema_version = 0

    source_id = ""
    try:
        row = connection.execute(
            "SELECT valor FROM app_metadata "
            "WHERE clave='integration.source_instance_id'"
        ).fetchone()
        source_id = str(row[0] or "").strip() if row else ""
    except Exception:
        source_id = ""

    if not source_id:
        normalized_path = str(Path(db_path).expanduser().resolve()).casefold()
        source_id = str(uuid.uuid5(uuid.NAMESPACE_URL, normalized_path))
    return source_id, schema_version


def stable_snapshot_hash(value: Any) -> str:
    if hasattr(value, "snapshot"):
        value = value.snapshot()
    elif hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
