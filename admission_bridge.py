"""Acceso de solo lectura a la base local del sistema de Admisión.

La base SQLite pertenece a otra aplicación. Este módulo nunca crea tablas,
ejecuta migraciones ni escribe en ella. La atención (no el NSS) es la unidad
estable que se vincula con un recibo de Facturación.
"""

from __future__ import annotations

import os
import logging
import re
import sqlite3
from contextlib import closing
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
import json

from admission_contract import (
    READINESS_READY,
    assess_billing_readiness,
    assess_coverage,
    canonicalize_ars,
    source_metadata,
    stable_snapshot_hash,
    normalize_specialty,
    normalize_service_type,
)


_LOGGER = logging.getLogger(__name__)


def _default_admission_db() -> Path:
    configured = str(os.environ.get("ADMISSION_DB_PATH", "") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser().resolve(strict=False)
        appdata_roots = [
            Path(str(os.environ.get(name, "") or "")).expanduser().resolve(strict=False)
            for name in ("APPDATA", "LOCALAPPDATA")
            if str(os.environ.get(name, "") or "").strip()
        ]
        if not any(
            configured_path == root or root in configured_path.parents
            for root in appdata_roots
        ):
            return configured_path
        _LOGGER.warning(
            "Se ignoró ADMISSION_DB_PATH heredado de AppData como fuente compartida."
        )
    program_data = str(os.environ.get("PROGRAMDATA", r"C:\ProgramData") or r"C:\ProgramData")
    return Path(program_data) / "Hospital" / "GeneradorHojasEmergencia" / "pacientes.db"


DEFAULT_ADMISSION_DB = _default_admission_db()

class AdmissionBridgeError(RuntimeError):
    """Error recuperable al consultar el sistema de Admisión."""


@dataclass(frozen=True)
class AdmissionAttention:
    attention_id: int
    patient_id: int
    name: str
    service_date: str
    service_time: str
    nss: str
    nss_clean: str
    cedula: str
    cedula_clean: str
    ars: str
    attention_type: str
    source_updated_at: str
    uninsured: bool
    turn_id: int = 0
    source_instance_id: str = ""
    source_schema_version: int = 0
    coverage_status: str = ""
    canonical_ars: str = ""
    billing_readiness: str = ""
    readiness_reasons: tuple[str, ...] = ()
    snapshot_hash: str = ""
    specialty: str = ""
    admission_username: str = ""
    authorization_number: str = ""
    source_status: str = "ACTIVA"
    turn_scope: str = ""
    processing_turn_id: int = 0
    has_detail_sheet: bool = False
    global_attention_id: str = ""
    global_patient_id: str = ""
    operational_source_id: str = ""
    operational_session_id: str = ""
    generation: int = 0
    origin_device_id: str = ""
    version: int = 1

    def snapshot(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class AdmissionTransferEvent:
    event_id: int
    event_uuid: str
    source_instance_id: str
    attention_id: int
    event_type: str
    workflow_status: str
    missing_fields: tuple[str, ...]
    created_at: str
    attention: AdmissionAttention


@dataclass(frozen=True)
class AdmissionEventRef:
    """Referencia estable emitida por V15 sin transportar datos del formulario."""

    source_instance_id: str
    attention_id: int
    event_uuid: str = ""
    event_type: str = ""


@dataclass(frozen=True)
class ShiftEventRef:
    """Referencia de turno compartida entre Admisión y Facturación."""

    source_instance_id: str
    turn_id: int
    operational_day_id: int = 0
    shift_type: str = ""
    representative: str = ""
    closed_at: str = ""
    event_uuid: str = ""


@dataclass(frozen=True)
class AdmissionShiftClosure:
    event_id: int
    event_uuid: str
    source_instance_id: str
    turn_id: int
    operational_day_id: int
    operational_date: str
    started_at: str
    scheduled_end_at: str
    closed_at: str
    representative: str
    shift_type: str
    actor: str
    actor_role: str
    session_id: str
    created_at: str


def normalize_identifier(value: str) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalize_service_date(value: str) -> str:
    text = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], date_format).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().upper())


def is_uninsured(ars: str, nss: str = "") -> bool:
    return assess_coverage(ars, nss).uninsured


class AdmissionReadOnlyRepository:
    REQUIRED_ATTENTION_COLUMNS = {
        "id",
        "paciente_id",
        "nombre",
        "fecha",
        "hora",
        "nss",
        "nss_clean",
        "cedula",
        "cedula_clean",
        "ars",
        "tipo_atencion",
        "estado",
        "identidad_estado",
        "requiere_revision",
        "created_at",
        "updated_at",
        "turno_id",
    }

    def __init__(self, db_path: os.PathLike | str = DEFAULT_ADMISSION_DB):
        self.db_path = Path(db_path)
        self._source_instance_id = ""
        self._source_schema_version = 0

    def _connect(self) -> sqlite3.Connection:
        if not self.db_path.is_file():
            raise AdmissionBridgeError(
                "No se encontró la base local de Admisión. "
                "Verifica que el sistema de Admisión esté instalado en este equipo."
            )
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection = None
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            # Older Admission databases do not yet have the audit table. A
            # temporary empty table keeps the read-only bridge compatible
            # while newer installations provide the real creation user.
            audit_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='atenciones_auditoria'"
            ).fetchone()
            if not audit_table:
                connection.execute(
                    "CREATE TEMP TABLE atenciones_auditoria(id INTEGER, atencion_id INTEGER, usuario TEXT, accion TEXT)"
                )
            connection.execute("PRAGMA query_only=ON")
            self._attention_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(atenciones)")
            }
            self._has_hoja = "hoja" in self._attention_columns
            self._has_authorization = "numero_autorizacion" in self._attention_columns
            try:
                self._validate_schema(connection)
                (
                    self._source_instance_id,
                    self._source_schema_version,
                ) = source_metadata(connection, self.db_path)
            except Exception:
                connection.close()
                raise
            return connection
        except sqlite3.Error as exc:
            if connection is not None:
                connection.close()
            raise AdmissionBridgeError(
                "No fue posible consultar Admisión en modo solo lectura."
            ) from exc

    def _adapt_query(self, query: str) -> str:
        if getattr(self, "_has_hoja", True):
            adapted = query
        else:
            adapted = query.replace("a.hoja", "NULL")
        if not getattr(self, "_has_authorization", False):
            adapted = adapted.replace(
                "a.numero_autorizacion,", "NULL AS numero_autorizacion,"
            )
        optional_columns = (
            "global_attention_id",
            "global_patient_id",
            "operational_source_id",
            "operational_session_id",
            "generation",
            "origin_device_id",
            "version",
        )
        available = getattr(self, "_attention_columns", set())
        for column in optional_columns:
            if column not in available:
                adapted = adapted.replace(
                    f"a.{column},", f"NULL AS {column},"
                )
        return adapted

    def get_current_shift_context(self) -> dict:
        """Return the open V15 shift used by Admission itself.

        Billing must not infer the operational shift from the greatest
        projected identifier: V15 defines it as the most recently opened row
        whose state is still ``ABIERTO``.
        """
        query = """
            SELECT t.id,t.dia_operativo_id,
                   COALESCE(t.fecha_inicio_real,t.fecha_inicio,'') AS started_at,
                   COALESCE(t.fecha_fin,t.fecha_cierre,'') AS ended_at,
                   COALESCE(t.tipo_turno,'') AS shift_type
            FROM turnos t
            WHERE UPPER(TRIM(COALESCE(t.estado,'')))='ABIERTO'
            ORDER BY COALESCE(t.fecha_inicio_real,t.fecha_inicio,'') DESC,
                     t.id DESC
            LIMIT 1
        """
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(query).fetchone()
                source_instance_id = self._source_instance_id or "LEGACY"
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo determinar el turno abierto de Admisión."
            ) from exc
        if not row:
            raise AdmissionBridgeError(
                "Admisión no tiene un turno abierto para iniciar facturación."
            )
        return {
            "source_instance_id": source_instance_id,
            "turn_id": int(row["id"]),
            "operational_day_id": int(row["dia_operativo_id"] or 0),
            "started_at": str(row["started_at"] or ""),
            "ended_at": str(row["ended_at"] or ""),
            "shift_type": str(row["shift_type"] or ""),
        }

    def history_source_state(self) -> dict:
        """Return a non-clinical signature of the V15 history source.

        The signature lets Billing determine whether its PostgreSQL read
        projection needs reconciliation without loading the history itself.
        No patient identifiers or clinical values leave this method.
        """
        query = """
            SELECT COUNT(*) AS total_count,
                   SUM(CASE WHEN UPPER(TRIM(COALESCE(estado,'')))='ACTIVA'
                            THEN 1 ELSE 0 END) AS active_count,
                   COALESCE(MAX(id),0) AS max_attention_id,
                   COALESCE(MAX(COALESCE(updated_at,created_at,'')),'') AS max_updated_at
            FROM atenciones
        """
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(query).fetchone()
                source_instance_id = self._source_instance_id or "LEGACY"
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo verificar el estado del historial de Admisión."
            ) from exc
        return {
            "source_instance_id": source_instance_id,
            "total_count": int(row["total_count"] or 0),
            "active_count": int(row["active_count"] or 0),
            "max_attention_id": int(row["max_attention_id"] or 0),
            "max_updated_at": str(row["max_updated_at"] or ""),
        }

    def list_history_projection_batch(
        self,
        *,
        after_attention_id: int = 0,
        limit: int = 500,
    ) -> list[AdmissionAttention]:
        """Read one bounded V15 batch for the PostgreSQL history projection.

        All source states are transferred so a cancellation that happened
        while Billing was offline cannot remain active in the projection.
        The user-facing history still selects only the ACTIVE V15 universe.
        """
        query = """
            SELECT
                a.id, a.paciente_id, a.turno_id, a.nombre, a.fecha, a.hora,
                a.nss, a.nss_clean, a.cedula, a.cedula_clean, a.ars,
                a.tipo_atencion, a.estado, a.updated_at, a.created_at, a.hoja,
                a.numero_autorizacion, a.global_attention_id,
                a.global_patient_id, a.operational_source_id,
                a.operational_session_id, a.generation,
                a.origin_device_id, a.version,
                COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa
                          WHERE aa.atencion_id=a.id AND aa.accion='CREACION'
                          ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            WHERE a.id>?
            ORDER BY a.id ASC
            LIMIT ?
        """
        batch_limit = max(1, min(int(limit), 1000))
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    self._adapt_query(query),
                    (max(0, int(after_attention_id)), batch_limit),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo reconciliar el historial de Admisión."
            ) from exc
        return [self._row_to_attention(row) for row in rows]

    def list_monthly_ars_candidates(
        self,
        ars: str,
        date_from: str | None,
        date_to: str | None,
        *,
        limit: int = 5000,
    ) -> list[dict]:
        """Transición aislada para Listados ARS; lee atenciones sin exigir completitud."""
        query = """
            SELECT a.id, a.paciente_id, a.turno_id, a.nombre, a.fecha, a.hora,
                   a.nss, a.nss_clean, a.cedula, a.cedula_clean, a.ars,
                   a.tipo_atencion, a.updated_at, a.created_at, a.hoja,
                   a.numero_autorizacion,
                   COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa
                             WHERE aa.atencion_id=a.id AND aa.accion='CREACION'
                             ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            WHERE UPPER(TRIM(COALESCE(a.estado,'')))<>'ANULADA'
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,'')))='EMERGENCIA'
              AND (? IS NULL OR a.fecha>=?)
              AND (? IS NULL OR a.fecha<=?)
            ORDER BY a.fecha DESC, a.hora DESC, a.id DESC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    self._adapt_query(query),
                    (
                        date_from, date_from, date_to, date_to,
                        max(1, min(int(limit), 10000)),
                    ),
                ).fetchall()
                source_instance_id = self._source_instance_id or "LEGACY"
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudieron consultar los pacientes de Admisión."
            ) from exc
        target = canonicalize_ars(ars)
        candidates = []
        for row in rows:
            if canonicalize_ars(row["ars"]) != target:
                continue
            attention = self._row_to_attention(row)
            candidates.append({
                "source_instance_id": source_instance_id,
                "attention_id": attention.attention_id,
                "patient_id": attention.patient_id,
                "nombre": attention.name,
                "nss_snapshot": attention.nss_clean,
                "cedula_snapshot": attention.cedula_clean,
                "authorization_snapshot": str(row["numero_autorizacion"] or "").strip(),
                "service_date_snapshot": attention.service_date,
                "specialty_snapshot": attention.specialty,
                "ars_snapshot": attention.canonical_ars or attention.ars,
            })
        return candidates

    def _validate_schema(self, connection: sqlite3.Connection) -> None:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='atenciones'"
        ).fetchone()
        if not table:
            raise AdmissionBridgeError(
                "La base seleccionada no contiene la estructura actual de Admisión."
            )
        turn_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='turnos'"
        ).fetchone()
        if not turn_table:
            raise AdmissionBridgeError(
                "La base de Admisión no contiene la estructura de turnos."
            )
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(atenciones)")
        }
        missing = self.REQUIRED_ATTENTION_COLUMNS - columns
        if missing:
            raise AdmissionBridgeError(
                "La base de Admisión está desactualizada para esta integración."
            )

    def _row_to_attention(self, row: sqlite3.Row) -> AdmissionAttention:
        service_date = normalize_service_date(row["fecha"])
        attention_type = normalize_service_type(row["tipo_atencion"])
        keys = set(row.keys())
        specialty = normalize_specialty(row["hoja"] if "hoja" in keys else "")
        has_detail_sheet = bool(str(row["hoja"] if "hoja" in keys else "").strip())
        admission_username = str(row["admission_username"] if "admission_username" in keys else "").strip()
        authorization_number = str(
            row["numero_autorizacion"] if "numero_autorizacion" in keys else ""
        ).strip()
        source_status = str(
            row["estado"] if "estado" in keys else "ACTIVA"
        ).strip().upper()
        global_attention_id = str(
            row["global_attention_id"] if "global_attention_id" in keys else ""
        ).strip()
        global_patient_id = str(
            row["global_patient_id"] if "global_patient_id" in keys else ""
        ).strip()
        operational_source_id = str(
            row["operational_source_id"] if "operational_source_id" in keys else ""
        ).strip()
        operational_session_id = str(
            row["operational_session_id"] if "operational_session_id" in keys else ""
        ).strip()
        origin_device_id = str(
            row["origin_device_id"] if "origin_device_id" in keys else ""
        ).strip()
        try:
            generation = int(row["generation"] or 0) if "generation" in keys else 0
        except (TypeError, ValueError):
            generation = 0
        try:
            version = max(1, int(row["version"] or 1)) if "version" in keys else 1
        except (TypeError, ValueError):
            version = 1
        coverage = assess_coverage(row["ars"], row["nss"])
        readiness = assess_billing_readiness(
            name=row["nombre"],
            service_date=service_date,
            attention_type=attention_type,
            coverage=coverage,
            cedula=row["cedula"],
        )
        snapshot_values = {
            "attention_id": int(row["id"]),
            "patient_id": int(row["paciente_id"]),
            "turn_id": int(row["turno_id"]),
            "name": str(row["nombre"] or "").strip(),
            "service_date": service_date,
            "service_time": str(row["hora"] or "").strip(),
            "nss_clean": str(row["nss_clean"] or "").strip(),
            "cedula_clean": str(row["cedula_clean"] or "").strip(),
            "ars": str(row["ars"] or "").strip(),
            "attention_type": attention_type,
            "source_updated_at": str(
                row["updated_at"] or row["created_at"] or ""
            ).strip(),
            "source_instance_id": self._source_instance_id,
            "source_schema_version": self._source_schema_version,
            "coverage_status": coverage.status,
            "canonical_ars": coverage.canonical_ars,
            "billing_readiness": readiness.status,
            "readiness_reasons": readiness.reasons,
            "specialty": specialty,
            "admission_username": admission_username,
            "authorization_number": authorization_number,
            "source_status": source_status,
            "has_detail_sheet": has_detail_sheet,
            "global_attention_id": global_attention_id,
            "global_patient_id": global_patient_id,
            "operational_source_id": operational_source_id,
            "operational_session_id": operational_session_id,
            "generation": generation,
            "origin_device_id": origin_device_id,
            "version": version,
        }
        return AdmissionAttention(
            attention_id=int(row["id"]),
            patient_id=int(row["paciente_id"]),
            turn_id=int(row["turno_id"]),
            name=str(row["nombre"] or "").strip(),
            service_date=service_date,
            service_time=str(row["hora"] or "").strip(),
            nss=str(row["nss"] or "").strip(),
            nss_clean=str(row["nss_clean"] or "").strip(),
            cedula=str(row["cedula"] or "").strip(),
            cedula_clean=str(row["cedula_clean"] or "").strip(),
            ars=str(row["ars"] or "").strip(),
            attention_type=attention_type,
            source_updated_at=snapshot_values["source_updated_at"],
            uninsured=coverage.uninsured,
            source_instance_id=self._source_instance_id,
            source_schema_version=self._source_schema_version,
            coverage_status=coverage.status,
            canonical_ars=coverage.canonical_ars,
            billing_readiness=readiness.status,
            readiness_reasons=readiness.reasons,
            snapshot_hash=stable_snapshot_hash(snapshot_values),
            specialty=specialty,
            admission_username=admission_username,
            authorization_number=authorization_number,
            source_status=source_status,
            processing_turn_id=int(row["turno_id"]),
            has_detail_sheet=has_detail_sheet,
            global_attention_id=global_attention_id,
            global_patient_id=global_patient_id,
            operational_source_id=operational_source_id,
            operational_session_id=operational_session_id,
            generation=generation,
            origin_device_id=origin_device_id,
            version=version,
        )

    def list_canonical_ars(self) -> list[str]:
        """Return the validated ARS catalog currently present in Admission.

        Raw historical spellings are deliberately excluded. Billing receives
        only canonical names, so its existing emergency and section prices can
        continue to be resolved without copying or altering either database.
        """
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    """SELECT DISTINCT ars FROM atenciones
                       WHERE LENGTH(TRIM(COALESCE(ars, ''))) > 0"""
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No fue posible consultar las ARS registradas en Admisión."
            ) from exc
        canonical = {
            canonicalize_ars(row["ars"])
            for row in rows
            if canonicalize_ars(row["ars"])
        }
        return sorted(canonical, key=str.casefold)

    def find_eligible_by_identifier(
        self,
        identifier: str,
        *,
        limit: int = 20,
        include_consultas: bool = False,
    ) -> list[AdmissionAttention]:
        normalized = normalize_identifier(identifier)
        if len(normalized) < 3:
            raise AdmissionBridgeError(
                "Escribe al menos 3 números del NSS o la cédula."
            )
        service_filter = "IN ('EMERGENCIA','CONSULTA')" if include_consultas else "= 'EMERGENCIA'"
        query = f"""
            SELECT DISTINCT
                a.id, a.paciente_id, a.turno_id, a.nombre, a.fecha, a.hora,
                a.nss, a.nss_clean, a.cedula, a.cedula_clean, a.ars,
                a.tipo_atencion, a.updated_at, a.created_at, a.hoja,
                COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa
                          WHERE aa.atencion_id=a.id AND aa.accion='CREACION'
                          ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            LEFT JOIN paciente_identificadores i
              ON i.paciente_id=a.paciente_id AND i.activo=1 AND i.conflicto=0
            WHERE a.estado='ACTIVA'
              AND a.turno_id = (
                    SELECT t.id FROM turnos t
                    WHERE UPPER(TRIM(COALESCE(t.estado,'')))='ABIERTO'
                    ORDER BY COALESCE(t.fecha_inicio_real,t.fecha_inicio,'') DESC,
                             t.id DESC
                    LIMIT 1
              )
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,''))) {service_filter}
              AND COALESCE(a.requiere_revision,0)=0
              AND UPPER(TRIM(COALESCE(a.identidad_estado,'VALIDADA')))='VALIDADA'
              AND (
                    a.nss_clean=?
                 OR a.cedula_clean=?
                 OR i.valor_normalizado=?
              )
            ORDER BY a.fecha DESC, a.hora DESC, a.id DESC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                query = self._adapt_query(query)
                rows = connection.execute(
                    query,
                    (normalized, normalized, normalized, max(1, int(limit))),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo completar la búsqueda en Admisión."
            ) from exc
        return [self._row_to_attention(row) for row in rows]

    def get_eligible_attention(self, attention_id: int) -> AdmissionAttention | None:
        query = """
            SELECT
                id, paciente_id, turno_id, nombre, fecha, hora, nss, nss_clean,
                cedula, cedula_clean, ars, tipo_atencion, updated_at, created_at
            FROM atenciones
            WHERE id=?
              AND turno_id = (
                    SELECT t.id FROM turnos t
                    WHERE UPPER(TRIM(COALESCE(t.estado,'')))='ABIERTO'
                    ORDER BY COALESCE(t.fecha_inicio_real,t.fecha_inicio,'') DESC,
                             t.id DESC
                    LIMIT 1
              )
              AND estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(tipo_atencion,''))) = 'EMERGENCIA'
              AND COALESCE(requiere_revision,0)=0
              AND UPPER(TRIM(COALESCE(identidad_estado,'VALIDADA')))='VALIDADA'
            LIMIT 1
        """
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(query, (int(attention_id),)).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo verificar la atención en Admisión."
            ) from exc
        return self._row_to_attention(row) if row else None

    def get_billable_attention(self, attention_id: int) -> AdmissionAttention | None:
        attention = self.get_eligible_attention(attention_id)
        if attention is None or attention.billing_readiness != READINESS_READY:
            return None
        return attention

    def get_attention_by_identity(
        self,
        source_instance_id: str,
        attention_id: int,
    ) -> AdmissionAttention | None:
        """Obtiene la fuente de verdad por su clave compuesta, incluso anulada."""
        expected_source = str(source_instance_id or "").strip()
        if not expected_source or int(attention_id or 0) <= 0:
            return None
        try:
            with closing(self._connect()) as connection:
                if self._source_instance_id != expected_source:
                    return None
                row = connection.execute(
                    "SELECT * FROM atenciones WHERE id=? LIMIT 1",
                    (int(attention_id),),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo volver a leer la atención indicada por V15."
            ) from exc
        return self._row_to_attention(row) if row else None

    def list_transfer_events(
        self,
        after_event_id: int = 0,
        *,
        limit: int = 20,
    ) -> list[AdmissionTransferEvent]:
        """Read new outbox events without ever acknowledging them in SQLite."""
        query = """
            SELECT
                e.id AS event_id,e.event_uuid,e.source_instance_id,
                e.atencion_id,e.tipo,e.estado_flujo,
                e.campos_faltantes_json,e.created_at AS event_created_at,
                a.id,a.paciente_id,a.turno_id,a.nombre,a.fecha,a.hora,a.nss,a.nss_clean,
                a.cedula,a.cedula_clean,a.ars,a.tipo_atencion,a.estado,
                a.updated_at,a.created_at
            FROM integracion_eventos e
            JOIN atenciones a ON a.id=e.atencion_id
            WHERE e.id>?
            ORDER BY e.id ASC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='integracion_eventos'"
                ).fetchone()
                if not table:
                    return []
                rows = connection.execute(
                    query,
                    (max(0, int(after_event_id)), max(1, min(int(limit), 100))),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudieron recibir los pacientes nuevos de Admisión."
            ) from exc

        events = []
        for row in rows:
            try:
                raw_missing = json.loads(row["campos_faltantes_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_missing = ["datos pendientes"]
            missing = tuple(
                str(item).strip() for item in raw_missing
                if str(item).strip()
            )
            events.append(
                AdmissionTransferEvent(
                    event_id=int(row["event_id"]),
                    event_uuid=str(row["event_uuid"] or ""),
                    source_instance_id=str(row["source_instance_id"] or ""),
                    attention_id=int(row["atencion_id"]),
                    event_type=str(row["tipo"] or ""),
                    workflow_status=str(row["estado_flujo"] or ""),
                    missing_fields=missing,
                    created_at=str(row["event_created_at"] or ""),
                    attention=self._row_to_attention(row),
                )
            )
        return events

    def latest_transfer_event_id(self) -> int:
        try:
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='integracion_eventos'"
                ).fetchone()
                if not table:
                    return 0
                row = connection.execute(
                    "SELECT COALESCE(MAX(id),0) FROM integracion_eventos"
                ).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo consultar el estado de Admisión."
            ) from exc
        return int(row[0] or 0) if row else 0

    def list_shift_closure_events(
        self, after_event_id: int = 0, *, limit: int = 500
    ) -> list[AdmissionShiftClosure]:
        query = """
            SELECT id,event_uuid,source_instance_id,turno_id,dia_operativo_id,
                   fecha_base,fecha_inicio,fecha_fin_programada,fecha_cierre_real,
                   representante,tipo_turno,actor,actor_rol,session_id,created_at
            FROM turno_cierre_eventos
            WHERE id>?
            ORDER BY id
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='turno_cierre_eventos'"
                ).fetchone()
                if not table:
                    return []
                rows = connection.execute(
                    query,
                    (max(0, int(after_event_id)), max(1, min(int(limit), 5000))),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudieron consultar los cierres de turno de Admisión."
            ) from exc
        return [
            AdmissionShiftClosure(
                event_id=int(row["id"]),
                event_uuid=str(row["event_uuid"] or ""),
                source_instance_id=str(row["source_instance_id"] or ""),
                turn_id=int(row["turno_id"]),
                operational_day_id=int(row["dia_operativo_id"]),
                operational_date=str(row["fecha_base"] or ""),
                started_at=str(row["fecha_inicio"] or ""),
                scheduled_end_at=str(row["fecha_fin_programada"] or ""),
                closed_at=str(row["fecha_cierre_real"] or ""),
                representative=str(row["representante"] or ""),
                shift_type=str(row["tipo_turno"] or ""),
                actor=str(row["actor"] or ""),
                actor_role=str(row["actor_rol"] or ""),
                session_id=str(row["session_id"] or ""),
                created_at=str(row["created_at"] or ""),
            )
            for row in rows
        ]

    def get_shift_closure_by_identity(
        self,
        source_instance_id: str,
        turn_id: int,
    ) -> AdmissionShiftClosure | None:
        expected_source = str(source_instance_id or "").strip()
        if not expected_source or int(turn_id or 0) <= 0:
            return None
        query = """SELECT id,event_uuid,source_instance_id,turno_id,dia_operativo_id,
                          fecha_base,fecha_inicio,fecha_fin_programada,fecha_cierre_real,
                          representante,tipo_turno,actor,actor_rol,session_id,created_at
                   FROM turno_cierre_eventos
                   WHERE source_instance_id=? AND turno_id=? LIMIT 1"""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    query,
                    (expected_source, int(turn_id)),
                ).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo volver a leer el cierre indicado por V15."
            ) from exc
        if not row:
            return None
        return AdmissionShiftClosure(
            event_id=int(row["id"]),
            event_uuid=str(row["event_uuid"] or ""),
            source_instance_id=str(row["source_instance_id"] or ""),
            turn_id=int(row["turno_id"]),
            operational_day_id=int(row["dia_operativo_id"]),
            operational_date=str(row["fecha_base"] or ""),
            started_at=str(row["fecha_inicio"] or ""),
            scheduled_end_at=str(row["fecha_fin_programada"] or ""),
            closed_at=str(row["fecha_cierre_real"] or ""),
            representative=str(row["representante"] or ""),
            shift_type=str(row["tipo_turno"] or ""),
            actor=str(row["actor"] or ""),
            actor_role=str(row["actor_rol"] or ""),
            session_id=str(row["session_id"] or ""),
            created_at=str(row["created_at"] or ""),
        )

    def latest_shift_closure_event_id(self) -> int:
        """Punto de partida para no reproducir cierres históricos al iniciar."""
        try:
            with closing(self._connect()) as connection:
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='turno_cierre_eventos'"
                ).fetchone()
                if not table:
                    return 0
                row = connection.execute(
                    "SELECT COALESCE(MAX(id),0) FROM turno_cierre_eventos"
                ).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo establecer el punto inicial de cierres de turno."
            ) from exc
        return int(row[0] or 0) if row else 0

    def list_turn_attentions(
        self, turn_id: int, *, limit: int = 50000
    ) -> list[AdmissionAttention]:
        query = """
            SELECT a.id,a.paciente_id,a.turno_id,a.nombre,a.fecha,a.hora,
                   a.nss,a.nss_clean,a.cedula,a.cedula_clean,a.ars,
                   a.tipo_atencion,a.updated_at,a.created_at,a.hoja,
                   a.numero_autorizacion,
                   COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa WHERE aa.atencion_id=a.id AND aa.accion='CREACION' ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            WHERE a.turno_id=? AND a.estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,''))) IN ('EMERGENCIA','CONSULTA')
            ORDER BY a.id
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                query = self._adapt_query(query)
                rows = connection.execute(
                    query, (int(turn_id), max(1, min(int(limit), 50000)))
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudieron consultar los pacientes del turno cerrado."
            ) from exc
        return [self._row_to_attention(row) for row in rows]

    @staticmethod
    def _date_expression(alias: str = "a") -> str:
        return (
            f"CASE "
            f"WHEN {alias}.fecha GLOB '[0-9][0-9]/[0-9][0-9]/[0-9][0-9][0-9][0-9]' "
            f"THEN substr({alias}.fecha,7,4)||'-'||substr({alias}.fecha,4,2)||'-'||substr({alias}.fecha,1,2) "
            f"WHEN {alias}.fecha GLOB '[0-9][0-9]-[0-9][0-9]-[0-9][0-9][0-9][0-9]' "
            f"THEN substr({alias}.fecha,7,4)||'-'||substr({alias}.fecha,4,2)||'-'||substr({alias}.fecha,1,2) "
            f"ELSE substr(COALESCE({alias}.fecha,''),1,10) END"
        )

    def latest_eligible_date(self) -> str:
        date_expr = self._date_expression("a")
        query = f"""
            SELECT MAX({date_expr})
            FROM atenciones a
            WHERE a.estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,''))) IN ('EMERGENCIA','CONSULTA')
              AND COALESCE(a.requiere_revision,0)=0
              AND UPPER(TRIM(COALESCE(a.identidad_estado,'VALIDADA')))='VALIDADA'
        """
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(query).fetchone()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo determinar la fecha más reciente de Admisión."
            ) from exc
        return normalize_service_date(row[0]) if row and row[0] else ""

    def list_current_turn_attentions(self, *, limit: int = 10000) -> list[AdmissionAttention]:
        """Return every active Emergency attention from the latest open shift.

        Incomplete records are included so the shared operational report can
        distinguish pending correction from pending billing.
        """
        query = """
            SELECT
                a.id, a.paciente_id, a.turno_id, a.nombre, a.fecha, a.hora,
                a.nss, a.nss_clean, a.cedula, a.cedula_clean, a.ars,
                a.tipo_atencion, a.updated_at, a.created_at, a.hoja,
                COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa WHERE aa.atencion_id=a.id AND aa.accion='CREACION' ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            WHERE a.estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,''))) IN ('EMERGENCIA','CONSULTA')
              AND a.turno_id=(
                    SELECT t.id FROM turnos t
                    WHERE UPPER(TRIM(COALESCE(t.estado,'')))='ABIERTO'
                    ORDER BY COALESCE(t.fecha_inicio_real,t.fecha_inicio,'') DESC,
                             t.id DESC
                    LIMIT 1
              )
            ORDER BY a.id
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                query = self._adapt_query(query)
                rows = connection.execute(
                    query, (max(1, min(int(limit), 50000)),)
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo consultar el turno actual de Admisión."
            ) from exc
        return [self._row_to_attention(row) for row in rows]

    def list_current_billable_attentions(
        self, *, limit: int = 500
    ) -> list[AdmissionAttention]:
        """Devuelve las Emergencias activas y listas del turno abierto actual."""
        query = """
            SELECT
                a.id, a.paciente_id, a.turno_id, a.nombre, a.fecha, a.hora,
                a.nss, a.nss_clean, a.cedula, a.cedula_clean, a.ars,
                a.tipo_atencion, a.updated_at, a.created_at, a.hoja,
                COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa
                          WHERE aa.atencion_id=a.id AND aa.accion='CREACION'
                          ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            WHERE a.estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,'')))='EMERGENCIA'
              AND COALESCE(a.requiere_revision,0)=0
              AND UPPER(TRIM(COALESCE(a.identidad_estado,'VALIDADA')))='VALIDADA'
              AND a.turno_id=(
                    SELECT t.id FROM turnos t
                    WHERE UPPER(TRIM(COALESCE(t.estado,'')))='ABIERTO'
                    ORDER BY COALESCE(t.fecha_inicio_real,t.fecha_inicio,'') DESC,
                             t.id DESC
                    LIMIT 1
              )
            ORDER BY COALESCE(a.updated_at,a.created_at,'') DESC, a.id DESC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    self._adapt_query(query),
                    (max(1, min(int(limit), 5000)),),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudieron cargar los pacientes facturables del turno actual."
            ) from exc
        attentions = [self._row_to_attention(row) for row in rows]
        return [
            attention
            for attention in attentions
            if attention.billing_readiness == READINESS_READY
        ]

    def list_eligible(
        self,
        start_date: str,
        end_date: str,
        *,
        search: str = "",
        limit: int = 2000,
    ) -> list[AdmissionAttention]:
        start = normalize_service_date(start_date)
        end = normalize_service_date(end_date)
        if not start or not end:
            raise AdmissionBridgeError("Selecciona un rango de fechas válido.")
        if start > end:
            raise AdmissionBridgeError("La fecha inicial no puede superar la final.")
        term = str(search or "").strip()
        digits = normalize_identifier(term)
        text_pattern = f"%{_normalize_text(term)}%"
        digit_pattern = f"%{digits}%"
        date_expr = self._date_expression("a")
        query = f"""
            SELECT
                a.id, a.paciente_id, a.turno_id, a.nombre, a.fecha, a.hora,
                a.nss, a.nss_clean, a.cedula, a.cedula_clean, a.ars,
                a.tipo_atencion, a.updated_at, a.created_at, a.hoja,
                COALESCE((SELECT aa.usuario FROM atenciones_auditoria aa WHERE aa.atencion_id=a.id AND aa.accion='CREACION' ORDER BY aa.id LIMIT 1),'') AS admission_username
            FROM atenciones a
            WHERE a.estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(a.tipo_atencion,''))) = 'EMERGENCIA'
              AND COALESCE(a.requiere_revision,0)=0
              AND UPPER(TRIM(COALESCE(a.identidad_estado,'VALIDADA')))='VALIDADA'
              AND {date_expr} BETWEEN ? AND ?
              AND (
                    ?=''
                 OR UPPER(COALESCE(a.nombre,'')) LIKE ?
                 OR (
                       ?<>''
                   AND (
                          COALESCE(a.nss_clean,'') LIKE ?
                       OR COALESCE(a.cedula_clean,'') LIKE ?
                       OR CAST(a.id AS TEXT)=?
                   )
                 )
              )
            ORDER BY {date_expr} DESC, a.hora DESC, a.id DESC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                query = self._adapt_query(query)
                rows = connection.execute(
                    query,
                    (
                        start,
                        end,
                        term,
                        text_pattern,
                        digits,
                        digit_pattern,
                        digit_pattern,
                        digits,
                        max(1, min(int(limit), 10000)),
                    ),
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudo consultar el módulo central de Admisión."
            ) from exc
        return [self._row_to_attention(row) for row in rows]

    def iter_uninsured_since(
        self,
        minimum_attention_id: int,
        *,
        limit: int = 500,
    ) -> Iterable[AdmissionAttention]:
        """Devuelve no asegurados nuevos sin escribir ni marcar la fuente."""
        query = """
            SELECT
                id, paciente_id, turno_id, nombre, fecha, hora, nss, nss_clean,
                cedula, cedula_clean, ars, tipo_atencion, updated_at, created_at
            FROM atenciones
            WHERE id>?
              AND estado='ACTIVA'
              AND UPPER(TRIM(COALESCE(tipo_atencion,''))) IN ('EMERGENCIA','CONSULTA')
              AND COALESCE(requiere_revision,0)=0
              AND UPPER(TRIM(COALESCE(identidad_estado,'VALIDADA')))='VALIDADA'
            ORDER BY id
            LIMIT ?
        """
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    query, (int(minimum_attention_id), max(1, int(limit)))
                ).fetchall()
        except sqlite3.Error as exc:
            raise AdmissionBridgeError(
                "No se pudieron consultar las atenciones nuevas de Admisión."
            ) from exc
        return (
            attention
            for attention in (self._row_to_attention(row) for row in rows)
            if attention.uninsured
        )
