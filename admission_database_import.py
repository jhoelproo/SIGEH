"""Controlled SQLite preview/merge into the central Admission event model."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from admission_hybrid import (
    AdmissionCloudRepository,
    SyncEvent,
    canonical_role,
    canonical_user_id,
    canonical_username,
    ensure_admission_import_progress_schema,
)
from patient_seed_tool import seed_admission_database_to_cloud, source_identity

IMPORT_MODES = frozenset({"SEED", "MERGE"})
ROLE_ADMIN = "administrador"
READ_CHUNK_SIZE = 500
STAGING_BATCH_SIZE = 250
APPLY_BATCH_SIZE = 100
ACTIVE_IMPORT_STATUSES = frozenset({"ANALYZING", "APPLYING"})
IMPORT_STALE_SECONDS = 180
IMPORT_LOG = logging.getLogger("hospital.admission.import")

IMPORT_PHASE_LABELS = {
    "VALIDATE_SOURCE": "Validando integridad SQLite",
    "HASH_SOURCE": "Verificando archivo fuente",
    "READ_SQLITE": "Leyendo atenciones SQLite",
    "NORMALIZE_ROWS": "Normalizando registros",
    "COMPARE_CLOUD": "Comparando con PostgreSQL",
    "CLASSIFY_ROWS": "Clasificando registros",
    "STAGE_ROWS": "Preparando vista previa",
    "FINALIZE_ANALYSIS": "Finalizando análisis",
    "VERIFY_SOURCE": "Comprobando que la fuente no cambió",
    "PREPARE_APPLY": "Preparando actualización central",
    "APPLY_ATTENTIONS": "Aplicando atenciones históricas",
    "SYNC_PATIENT_DIRECTORY": "Actualizando directorio de pacientes",
    "FINALIZE_APPLY": "Finalizando actualización central",
}


class AdmissionImportTaskActiveError(RuntimeError):
    """Raised when another station owns the single central import slot."""

    def __init__(self, import_batch_id: str):
        self.import_batch_id = str(import_batch_id or "")
        super().__init__(
            "Ya hay una importación de Admisión en curso. "
            "Espere a que termine o vuelva a abrir esta ventana."
        )


class AdmissionImportSchemaError(RuntimeError):
    """Raised before task queries when durable import schema cannot be prepared."""

    def __init__(self, cause: Exception):
        self.cause = cause
        super().__init__(
            "No se pudo actualizar la estructura del módulo de importación."
        )


def import_progress_percent(
    operation: str, phase: str, current: int, total: int
) -> int:
    """Map real completed work to a stable, monotonic global percentage."""
    ranges = {
        "ANALYZE": {
            "VALIDATE_SOURCE": (0, 5), "HASH_SOURCE": (5, 12),
            "READ_SQLITE": (12, 30), "NORMALIZE_ROWS": (30, 42),
            "COMPARE_CLOUD": (42, 65), "CLASSIFY_ROWS": (65, 75),
            "STAGE_ROWS": (75, 97), "FINALIZE_ANALYSIS": (97, 100),
        },
        "APPLY": {
            "VERIFY_SOURCE": (0, 5), "PREPARE_APPLY": (5, 10),
            "APPLY_ATTENTIONS": (10, 82), "SYNC_PATIENT_DIRECTORY": (82, 96),
            "FINALIZE_APPLY": (96, 100),
        },
    }
    start, end = ranges.get(str(operation).upper(), {}).get(phase, (0, 100))
    ratio = 1.0 if total <= 0 else min(1.0, max(0.0, current / total))
    return round(start + ((end - start) * ratio))


def _is_admin(user: Mapping[str, Any] | Any) -> bool:
    return canonical_role(user) in {ROLE_ADMIN, "admin", "administrator"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid(value: Any, fallback: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, fallback))


def _effective_timestamp(service_date: Any, service_time: Any, fallback: Any) -> str:
    raw = str(fallback or "").strip()
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    date_text = str(service_date or "").strip()
    time_text = str(service_time or "00:00:00").strip() or "00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return (
                datetime.strptime(f"{date_text} {time_text}", fmt)
                .replace(tzinfo=timezone.utc)
                .isoformat()
            )
        except ValueError:
            continue
    return _utc_now()


def _stream_sha256(
    path: Path,
    *,
    chunk_size: int = 1024 * 1024,
    progress: Callable[[str, int, int], None] | None = None,
    phase: str = "HASH_SOURCE",
) -> str:
    digest = hashlib.sha256()
    total = max(0, int(path.stat().st_size))
    processed = 0
    _emit_progress(progress, phase, 0, total)
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
            processed += len(chunk)
            _emit_progress(progress, phase, processed, total)
    return digest.hexdigest()


def _verified_sqlite_backup(path: Path, source_sha256: str) -> tuple[str, str]:
    """Create and verify a recoverable SQLite backup before central mutation."""
    backup_directory = path.parent / "BACKUPS"
    backup_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_directory / (
        f"{path.stem}_pre_import_{timestamp}_{source_sha256[:12]}.sqlite3"
    )
    temporary_path = backup_path.with_suffix(".sqlite3.partial")
    source_uri = f"file:{path.resolve().as_posix()}?mode=ro"
    source = sqlite3.connect(source_uri, uri=True)
    target = sqlite3.connect(temporary_path)
    try:
        source.backup(target)
        target.commit()
        quick_check = target.execute("PRAGMA quick_check").fetchone()
        if not quick_check or str(quick_check[0]).strip().casefold() != "ok":
            raise ValueError("La copia de seguridad SQLite no superó PRAGMA quick_check.")
    except Exception:
        target.close()
        source.close()
        temporary_path.unlink(missing_ok=True)
        raise
    target.close()
    source.close()
    temporary_path.replace(backup_path)
    backup_sha256 = _stream_sha256(backup_path, phase="VERIFY_BACKUP")
    if not backup_sha256:
        backup_path.unlink(missing_ok=True)
        raise ValueError("No se pudo verificar la copia de seguridad SQLite.")
    return str(backup_path), backup_sha256


def _require_compatible_initial_baseline(
    *,
    existing_fingerprint: str,
    existing_source_id: str,
    source_sha256: str,
    source_id: str,
) -> None:
    if (
        str(existing_fingerprint) != str(source_sha256)
        or str(existing_source_id) != str(source_id)
    ):
        raise ValueError(
            "El baseline inicial central ya fue aplicado desde otra fuente."
        )


def _emit_progress(
    callback: Callable[[str, int, int], None] | None,
    phase: str,
    current: int,
    total: int,
) -> None:
    if callback is not None:
        callback(str(phase), max(0, int(current)), max(0, int(total)))


def _historical_context(
    payload: Mapping[str, Any],
    source_id: str,
    *,
    baseline_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if baseline_context is not None:
        operational_source_id = str(
            uuid.UUID(str(baseline_context.get("operational_source_id") or ""))
        )
        operational_session_id = str(
            uuid.UUID(str(baseline_context.get("operational_session_id") or ""))
        )
        turn_id = int(baseline_context.get("turn_id") or 0)
        generation = int(baseline_context.get("generation") or 0)
        active_username = str(
            baseline_context.get("active_username") or ""
        ).strip()
        if turn_id <= 0 or generation <= 0 or not active_username:
            raise ValueError(
                "El turno central activo no tiene un contexto operacional válido."
            )
        return {
            "operational_source_id": operational_source_id,
            "operational_session_id": operational_session_id,
            "turn_id": turn_id,
            "generation": generation,
            "origin_device_id": str(
                baseline_context.get("primary_device_id") or "CENTRAL_BASELINE"
            ),
            "admission_username": active_username,
            "reconciliation_status": "INITIAL_BASELINE",
        }
    operational_source_id = _uuid(
        payload.get("operational_source_id"),
        f"hospital-admission-import-source:{source_id}",
    )
    operational_session_id = _uuid(
        payload.get("operational_session_id"),
        f"hospital-admission-import-session:{source_id}",
    )
    return {
        "operational_source_id": operational_source_id,
        "operational_session_id": operational_session_id,
        "generation": max(1, int(payload.get("generation") or 1)),
        "origin_device_id": str(
            payload.get("origin_device_id") or f"IMPORT:{source_id}"
        ),
        "admission_username": str(
            payload.get("admission_username")
            or payload.get("representative_username")
            or "IMPORTACION HISTORICA"
        ),
    }


def _insert_staging_batch(connection: Any, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    query = """INSERT INTO admission_import_staging(
                   import_batch_id,row_number,global_attention_id,
                   legacy_source_instance_id,legacy_attention_id,
                   local_revision,cloud_revision,classification,payload_json
               ) VALUES %s"""
    raw_connection = getattr(connection, "con", None)
    if raw_connection is not None and hasattr(raw_connection, "cursor"):
        from psycopg2.extras import execute_values

        with raw_connection.cursor() as cursor:
            execute_values(cursor, query, rows, page_size=STAGING_BATCH_SIZE)
        return
    fallback_query = query.replace(
        "VALUES %s", "VALUES(%s::UUID,%s,%s::UUID,%s,%s,%s,%s,%s,%s::JSONB)"
    )
    for row in rows:
        connection.execute(fallback_query, row)


def _classify_import_row(
    payload: dict[str, Any],
    cloud: Mapping[str, Any] | None,
    projection_matches: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
    *,
    allow_updates: bool = True,
) -> tuple[str, int, int]:
    local_revision = int(payload.get("version") or 0)
    cloud_revision = int((cloud or {}).get("server_revision") or 0)
    if cloud and bool(cloud.get("is_deleted")):
        return "SKIP_TOMBSTONED", local_revision, cloud_revision
    if cloud and projection_matches(payload, cloud):
        return "EXISTING", local_revision, cloud_revision
    if cloud and cloud_revision > local_revision:
        return "SKIPPED_CLOUD_NEWER", local_revision, cloud_revision
    if cloud:
        return ("UPDATE" if allow_updates else "CONFLICT"), local_revision, cloud_revision
    if bool(payload.get("is_deleted")):
        return "SKIP_ORPHAN_TOMBSTONE", local_revision, cloud_revision
    return "INSERT", local_revision, cloud_revision


class AdmissionDatabaseImporter:
    """Admin-only staging importer. Preview never mutates clinical projections."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        audit: Callable[[str, Mapping[str, Any]], None] | None = None,
    ):
        self.connection_factory = connection_factory
        self.audit = audit
        self._schema_ready = False

    def ensure_schema(self) -> None:
        """Guarantee progress columns exist before querying an import batch."""
        if self._schema_ready:
            return
        try:
            with self.connection_factory() as con:
                ensure_admission_import_progress_schema(con)
        except Exception as exc:
            raise AdmissionImportSchemaError(exc) from exc
        self._schema_ready = True

    @staticmethod
    def _require_admin(user: Mapping[str, Any] | Any) -> None:
        if not _is_admin(user):
            raise PermissionError(
                "Solo Administrador puede importar la base de Admision."
            )

    @staticmethod
    def _payloads(
        path: Path,
        *,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> tuple[str, str, int, list[dict[str, Any]]]:
        _emit_progress(progress, "VALIDATE_SOURCE", 0, 1)
        source_sha256 = _stream_sha256(path, progress=progress)
        uri = f"file:{path.resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            quick_check = connection.execute("PRAGMA quick_check").fetchone()
            if not quick_check or str(quick_check[0]).strip().casefold() != "ok":
                raise ValueError("La base SQLite no superó PRAGMA quick_check.")
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if "atenciones" not in tables:
                raise ValueError(
                    "La base seleccionada no contiene la tabla atenciones."
                )
            if "pacientes" not in tables:
                raise ValueError("La base seleccionada no contiene la tabla pacientes.")
            _emit_progress(progress, "VALIDATE_SOURCE", 1, 1)
            source_id = source_identity(connection)
            patient_count = int(
                connection.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0]
            )
            attention_count = int(
                connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0]
            )
            cursor = connection.execute("SELECT * FROM atenciones ORDER BY id")
            rows: list[sqlite3.Row] = []
            while chunk := cursor.fetchmany(READ_CHUNK_SIZE):
                rows.extend(chunk)
                _emit_progress(
                    progress,
                    "READ_SQLITE",
                    len(rows),
                    attention_count,
                )
        finally:
            connection.close()
        payloads: list[dict[str, Any]] = []
        for index, raw in enumerate(rows, start=1):
            data = dict(raw)
            legacy_attention_id = int(
                data.get("legacy_attention_id") or data.get("id") or 0
            )
            legacy_patient_id = int(
                data.get("legacy_patient_id") or data.get("paciente_id") or 0
            )
            global_attention_id = _uuid(
                data.get("global_attention_id"),
                f"hospital-admission-legacy:{source_id}:{legacy_attention_id}",
            )
            global_patient_id = _uuid(
                data.get("global_patient_id"),
                f"hospital-patient-legacy:{source_id}:{legacy_patient_id}",
            )
            effective = _effective_timestamp(
                data.get("fecha"),
                data.get("hora"),
                data.get("created_at_effective_utc"),
            )
            deleted = (
                bool(data.get("is_deleted"))
                or str(data.get("estado") or "").strip().upper() == "ANULADA"
            )
            payloads.append(
                {
                    "global_attention_id": global_attention_id,
                    "attention_id": legacy_attention_id,
                    "legacy_source_instance_id": source_id,
                    "legacy_attention_id": legacy_attention_id,
                    "source_instance_id": source_id,
                    "global_patient_id": global_patient_id,
                    "patient_id": legacy_patient_id,
                    "legacy_patient_id": legacy_patient_id,
                    "turn_id": int(
                        data.get("operational_turn_id") or data.get("turno_id") or 0
                    ),
                    "name": str(data.get("nombre") or "SIN NOMBRE"),
                    "sex": str(data.get("sexo") or ""),
                    "age": int(data.get("edad_num") or 0),
                    "age_unit": str(data.get("unidad") or "Años"),
                    "cedula": str(data.get("cedula") or ""),
                    "phone": str(data.get("telefono") or ""),
                    "address": str(data.get("direccion") or ""),
                    "nationality": str(data.get("nacionalidad") or ""),
                    "ars": str(data.get("ars") or ""),
                    "nss": str(data.get("nss") or ""),
                    "detail_sheet": str(data.get("hoja") or ""),
                    "specialty": str(
                        data.get("especialidad") or data.get("hoja") or ""
                    ),
                    "service_date": str(data.get("fecha") or ""),
                    "service_time": str(data.get("hora") or ""),
                    "service_type": str(data.get("tipo_atencion") or "EMERGENCIA"),
                    "source_status": "ANULADA"
                    if deleted
                    else str(data.get("estado") or "ACTIVA"),
                    "created_at_device": str(
                        data.get("created_at_device") or effective
                    ),
                    "created_at_effective_utc": effective,
                    "device_local_sequence": int(
                        data.get("device_local_sequence") or legacy_attention_id
                    ),
                    "version": int(
                        data.get("server_revision") or data.get("version") or 0
                    ),
                    "operational_session_id": str(
                        data.get("operational_session_id") or ""
                    ),
                    "operational_source_id": str(
                        data.get("operational_source_id") or ""
                    ),
                    "generation": int(data.get("generation") or 1),
                    "origin_device_id": str(data.get("origin_device_id") or ""),
                    "admission_username": str(
                        data.get("admission_username")
                        or data.get("representante")
                        or ""
                    ),
                    "is_deleted": deleted,
                    "deleted_at": str(
                        data.get("deleted_at") or data.get("anulada_at") or ""
                    ),
                    "deleted_by_user_id": str(
                        data.get("deleted_by_user_id") or data.get("anulada_por") or ""
                    ),
                    "delete_event_uuid": str(data.get("delete_event_uuid") or ""),
                    "delete_reason": str(
                        data.get("delete_reason") or data.get("anulada_motivo") or ""
                    ),
                    "reconciliation_status": "ADMIN_IMPORT",
                }
            )
            if index % READ_CHUNK_SIZE == 0 or index == len(rows):
                _emit_progress(progress, "NORMALIZE_ROWS", index, len(rows))
        _emit_progress(
            progress, "NORMALIZE_ROWS", attention_count, attention_count
        )
        return source_sha256, source_id, patient_count, payloads

    @staticmethod
    def _projection_matches(
        payload: Mapping[str, Any], cloud: Mapping[str, Any]
    ) -> bool:
        pairs = (
            ("patient_name", "name"),
            ("service_date", "service_date"),
            ("service_time", "service_time"),
            ("canonical_ars", "ars"),
            ("nss_snapshot", "nss"),
            ("cedula_snapshot", "cedula"),
            ("service_type", "service_type"),
            ("source_status", "source_status"),
        )
        return all(
            str(cloud.get(cloud_key) or "").strip().casefold()
            == str(payload.get(local_key) or "").strip().casefold()
            for cloud_key, local_key in pairs
        )

    def _cloud_rows(
        self,
        payloads: list[Mapping[str, Any]],
        *,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, dict[str, Any]]:
        if not payloads:
            return {}
        source_id = str(payloads[0]["legacy_source_instance_id"])
        result: dict[str, dict[str, Any]] = {}
        total = len(payloads)
        for offset in range(0, total, READ_CHUNK_SIZE):
            chunk = payloads[offset : offset + READ_CHUNK_SIZE]
            identities = [str(payload["global_attention_id"]) for payload in chunk]
            legacy_ids = [int(payload["legacy_attention_id"]) for payload in chunk]
            with self.connection_factory() as con:
                rows = con.execute(
                    """SELECT * FROM admission_attention_projection
                        WHERE global_attention_id=ANY(%s::UUID[])
                           OR (source_instance_id=%s AND attention_id=ANY(%s::BIGINT[]))""",
                    (identities, source_id, legacy_ids),
                ).fetchall()
            for raw in rows:
                row = dict(raw)
                global_id = str(row.get("global_attention_id") or "").strip()
                if global_id:
                    result[str(uuid.UUID(global_id))] = row
                result[
                    f"legacy:{row.get('source_instance_id')}:{row.get('attention_id')}"
                ] = row
            _emit_progress(
                progress,
                "COMPARE_CLOUD",
                min(offset + len(chunk), total),
                total,
            )
        return result

    def _audit(self, event: str, details: Mapping[str, Any]) -> None:
        if self.audit is not None:
            self.audit(event, details)

    @staticmethod
    def _value(row: Any, key: str, index: int = 0, default: Any = "") -> Any:
        if isinstance(row, Mapping):
            return row.get(key, default)
        try:
            return row[index]
        except (IndexError, KeyError, TypeError):
            return default

    def _active_batch_locked(self, connection: Any, *, exclude_batch_id: str = "") -> str:
        row = connection.execute(
            """SELECT import_batch_id FROM admission_import_batches
                 WHERE status = ANY(%s::TEXT[])
                   AND (%s::TEXT='' OR import_batch_id::TEXT<>%s::TEXT)
                 ORDER BY started_at DESC NULLS LAST,
                          imported_at DESC NULLS LAST,
                          import_batch_id DESC
                 LIMIT 1 FOR UPDATE""",
            (list(ACTIVE_IMPORT_STATUSES), exclude_batch_id, exclude_batch_id),
        ).fetchone()
        return str(self._value(row, "import_batch_id", 0, "") or "")

    def _claim_analysis_batch(
        self,
        *,
        batch_id: str,
        path: Path,
        source_sha256: str,
        source_id: str,
        username: str,
        mode: str,
    ) -> None:
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-import-global",),
            )
            active_batch_id = self._active_batch_locked(con)
            if active_batch_id:
                raise AdmissionImportTaskActiveError(active_batch_id)
            con.execute(
                """INSERT INTO admission_import_batches(
                       import_batch_id,source_filename,source_sha256,
                       legacy_source_instance_id,imported_by,mode,status,totals_json,
                       current_phase,progress_percent,processed_records,total_records,
                       status_message,started_at,progress_updated_at,last_heartbeat_at
                       ) VALUES(%s::UUID,%s,%s,%s,%s,%s,'ANALYZING','{}'::JSONB,
                            'VALIDATE_SOURCE',0,0,0,%s,NOW(),NOW(),NOW())""",
                (
                    batch_id, path.name, source_sha256, source_id, username, mode,
                    IMPORT_PHASE_LABELS["VALIDATE_SOURCE"],
                ),
            )

    @staticmethod
    def _active_baseline_context(connection: Any) -> dict[str, Any]:
        rows = connection.execute(
            """SELECT operational_session_id::TEXT,operational_source_id::TEXT,
                      turn_id,generation,active_username,primary_device_id
                 FROM admission_operational_sessions
                WHERE status='ACTIVE'
                ORDER BY updated_at DESC NULLS LAST
                FOR SHARE"""
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                "SEED requiere exactamente un turno central activo y verificable."
            )
        raw = rows[0]
        if isinstance(raw, Mapping):
            context = dict(raw)
        else:
            keys = (
                "operational_session_id",
                "operational_source_id",
                "turn_id",
                "generation",
                "active_username",
                "primary_device_id",
            )
            context = dict(zip(keys, raw, strict=False))
        _historical_context({}, "CENTRAL_BASELINE", baseline_context=context)
        return context

    def _set_analysis_source(
        self,
        batch_id: str,
        source_sha256: str,
        source_id: str,
        *,
        backup_path: str,
        backup_sha256: str,
        baseline_context: Mapping[str, Any] | None,
    ) -> None:
        with self.connection_factory() as con:
            con.execute(
                """UPDATE admission_import_batches
                      SET source_sha256=%s,legacy_source_instance_id=%s,
                          backup_path=%s,backup_sha256=%s,
                          baseline_context_json=%s::JSONB,
                          last_heartbeat_at=NOW(),progress_updated_at=NOW()
                    WHERE import_batch_id=%s::UUID AND status='ANALYZING'""",
                (
                    source_sha256,
                    source_id,
                    backup_path,
                    backup_sha256,
                    json.dumps(dict(baseline_context or {}), sort_keys=True),
                    batch_id,
                ),
            )

    def update_task_progress(
        self,
        import_batch_id: str,
        *,
        operation: str,
        phase: str,
        processed: int,
        total: int,
    ) -> None:
        """Persist progress from the worker without keeping an import transaction open."""
        if not import_batch_id:
            return
        self.ensure_schema()
        message = IMPORT_PHASE_LABELS.get(phase, str(phase))
        percent = import_progress_percent(operation, phase, processed, total)
        with self.connection_factory() as con:
            con.execute(
                """UPDATE admission_import_batches
                      SET current_phase=%s,progress_percent=GREATEST(progress_percent,%s),
                          processed_records=%s,total_records=%s,status_message=%s,
                          progress_updated_at=NOW(),last_heartbeat_at=NOW()
                    WHERE import_batch_id=%s::UUID
                      AND status=ANY(%s::TEXT[])""",
                (
                    phase, percent, max(0, int(processed)), max(0, int(total)),
                    message, str(import_batch_id), list(ACTIVE_IMPORT_STATUSES),
                ),
            )

    def mark_task_failed(self, import_batch_id: str, error: str) -> None:
        if not import_batch_id:
            return
        self.ensure_schema()
        with self.connection_factory() as con:
            con.execute(
                """UPDATE admission_import_batches
                      SET status='FAILED',current_phase='FAILED',status_message=%s,
                          error_code='WORKER_FAILED',error_message=%s,
                          completed_at=NOW(),progress_updated_at=NOW(),last_heartbeat_at=NOW()
                    WHERE import_batch_id=%s::UUID
                      AND status=ANY(%s::TEXT[])""",
                ("La tarea no se completó.", str(error)[:1000], import_batch_id,
                 list(ACTIVE_IMPORT_STATUSES)),
            )

    def load_task(self, import_batch_id: str) -> dict[str, Any] | None:
        if not import_batch_id:
            return None
        self.ensure_schema()
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT import_batch_id,source_filename,mode,status,totals_json,
                          current_phase,progress_percent,processed_records,total_records,
                          status_message,error_message,started_at,progress_updated_at,
                          last_heartbeat_at,completed_at,source_sha256,legacy_source_instance_id
                     FROM admission_import_batches
                    WHERE import_batch_id=%s::UUID""",
                (str(import_batch_id),),
            ).fetchone()
        if not row:
            return None
        if isinstance(row, Mapping):
            data = dict(row)
        else:
            keys = (
                "import_batch_id", "source_filename", "mode", "status", "totals_json",
                "current_phase", "progress_percent", "processed_records", "total_records",
                "status_message", "error_message", "started_at", "progress_updated_at",
                "last_heartbeat_at", "completed_at", "source_sha256", "legacy_source_instance_id",
            )
            data = dict(zip(keys, row, strict=False))
        totals = data.get("totals_json") or {}
        if isinstance(totals, str):
            totals = json.loads(totals or "{}")
        data["totals"] = dict(totals)
        return data

    def find_active_task(self) -> dict[str, Any] | None:
        self.ensure_schema()
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT import_batch_id FROM admission_import_batches
                     WHERE status=ANY(%s::TEXT[])
                     ORDER BY last_heartbeat_at DESC NULLS LAST,
                              started_at DESC NULLS LAST,
                              imported_at DESC NULLS LAST,
                              import_batch_id DESC LIMIT 1""",
                (list(ACTIVE_IMPORT_STATUSES),),
            ).fetchone()
        return self.load_task(str(self._value(row, "import_batch_id", 0, "") or ""))

    def recover_stale_active_task(self) -> dict[str, Any] | None:
        """Make a crash visible; a stale batch must never block all stations forever."""
        self.ensure_schema()
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-import-global",),
            )
            row = con.execute(
                """SELECT import_batch_id FROM admission_import_batches
                     WHERE status=ANY(%s::TEXT[])
                       AND COALESCE(last_heartbeat_at,started_at,imported_at)
                           < NOW()-(%s * INTERVAL '1 second')
                     ORDER BY imported_at DESC LIMIT 1 FOR UPDATE""",
                (list(ACTIVE_IMPORT_STATUSES), IMPORT_STALE_SECONDS),
            ).fetchone()
            batch_id = str(self._value(row, "import_batch_id", 0, "") or "")
            if not batch_id:
                return None
            con.execute(
                """UPDATE admission_import_batches
                      SET status='FAILED',current_phase='INTERRUPTED',
                          status_message='Importación interrumpida; analice nuevamente la base.',
                          error_code='INTERRUPTED',completed_at=NOW(),
                          progress_updated_at=NOW(),last_heartbeat_at=NOW()
                    WHERE import_batch_id=%s::UUID""",
                (batch_id,),
            )
        return self.load_task(batch_id)

    def analyze(
        self,
        sqlite_path: str | Path,
        *,
        mode: str,
        current_user: Mapping[str, Any] | Any,
        import_batch_id: str | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        self._require_admin(current_user)
        self.ensure_schema()
        normalized_mode = str(mode or "").strip().upper()
        if normalized_mode not in IMPORT_MODES:
            raise ValueError("El modo de importacion debe ser SEED o MERGE.")
        path = Path(sqlite_path).expanduser().resolve()
        if (
            path.suffix.casefold() not in {".db", ".sqlite", ".sqlite3"}
            or not path.is_file()
        ):
            raise ValueError("Seleccione una base SQLite valida.")
        batch_id = str(import_batch_id or uuid.uuid4())
        username = canonical_username(current_user) or canonical_user_id(current_user)
        self._claim_analysis_batch(
            batch_id=batch_id,
            path=path,
            source_sha256="",
            source_id=f"PENDING:{batch_id}",
            username=username,
            mode=normalized_mode,
        )
        IMPORT_LOG.info(
            "%s batch_id=%s source=%s",
            "BASELINE_ANALYZE_START"
            if normalized_mode == "SEED"
            else "MERGE_ANALYZE",
            batch_id,
            path.name,
        )
        try:
            source_sha256, source_id, patient_count, payloads = self._payloads(
                path,
                progress=progress,
            )
            backup_path, backup_sha256 = _verified_sqlite_backup(
                path, source_sha256
            )
            IMPORT_LOG.info(
                "BASELINE_SOURCE_VERIFIED batch_id=%s source_sha256=%s "
                "backup_sha256=%s records=%s patients=%s",
                batch_id,
                source_sha256,
                backup_sha256,
                len(payloads),
                patient_count,
            )
            baseline_context: dict[str, Any] | None = None
            if normalized_mode == "SEED":
                with self.connection_factory() as con:
                    baseline_context = self._active_baseline_context(con)
                for payload in payloads:
                    payload.update(
                        _historical_context(
                            payload,
                            source_id,
                            baseline_context=baseline_context,
                        )
                    )
            self._set_analysis_source(
                batch_id,
                source_sha256,
                source_id,
                backup_path=backup_path,
                backup_sha256=backup_sha256,
                baseline_context=baseline_context,
            )
        except Exception:
            self.mark_task_failed(batch_id, "No fue posible analizar la fuente SQLite.")
            raise
        self.update_task_progress(
            batch_id,
            operation="ANALYZE",
            phase="COMPARE_CLOUD",
            processed=0,
            total=len(payloads),
        )
        cloud_by_id = self._cloud_rows(payloads, progress=progress)
        staged: list[dict[str, Any]] = []
        counts: Counter[str] = Counter()
        for row_number, payload in enumerate(payloads, start=1):
            global_id = str(payload["global_attention_id"])
            cloud = cloud_by_id.get(global_id) or cloud_by_id.get(
                f"legacy:{source_id}:{payload['legacy_attention_id']}"
            )
            if cloud and cloud.get("global_attention_id"):
                global_id = str(uuid.UUID(str(cloud["global_attention_id"])))
                payload["global_attention_id"] = global_id
            classification, local_revision, cloud_revision = _classify_import_row(
                payload,
                cloud,
                self._projection_matches,
                allow_updates=normalized_mode == "MERGE",
            )
            counts[classification] += 1
            IMPORT_LOG.info(
                "BASELINE_RECORD_CLASSIFIED batch_id=%s row=%s "
                "classification=%s",
                batch_id,
                row_number,
                classification,
            )
            staged.append(
                {
                    "row_number": row_number,
                    "global_attention_id": global_id,
                    "legacy_attention_id": int(payload["legacy_attention_id"]),
                    "local_revision": local_revision,
                    "cloud_revision": cloud_revision,
                    "classification": classification,
                    "payload": payload,
                }
            )
            if row_number % READ_CHUNK_SIZE == 0 or row_number == len(payloads):
                _emit_progress(
                    progress, "CLASSIFY_ROWS", row_number, len(payloads)
                )
        totals = {
            "records": len(staged),
            "patients": patient_count,
            "backup_path": backup_path,
            "backup_sha256": backup_sha256,
            "baseline_context": dict(baseline_context or {}),
            **dict(counts),
        }
        with self.connection_factory() as con:
            total_staged = len(staged)
            for offset in range(0, total_staged, STAGING_BATCH_SIZE):
                chunk = staged[offset : offset + STAGING_BATCH_SIZE]
                _insert_staging_batch(
                    con,
                    [
                        (
                            batch_id,
                            item["row_number"],
                            item["global_attention_id"],
                            source_id,
                            item["legacy_attention_id"],
                            item["local_revision"],
                            item["cloud_revision"],
                            item["classification"],
                            json.dumps(
                                item["payload"],
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        )
                        for item in chunk
                    ],
                )
                _emit_progress(
                    progress,
                    "STAGE_ROWS",
                    min(offset + len(chunk), total_staged),
                    total_staged,
                )
            con.execute(
                """UPDATE admission_import_batches
                      SET status='ANALYZED',totals_json=%s::JSONB,
                          current_phase='FINALIZE_ANALYSIS',progress_percent=100,
                          processed_records=%s,total_records=%s,
                          status_message=%s,progress_updated_at=NOW(),
                          last_heartbeat_at=NOW(),completed_at=NOW()
                    WHERE import_batch_id=%s::UUID""",
                (
                    json.dumps(totals, sort_keys=True), total_staged, total_staged,
                    IMPORT_PHASE_LABELS["FINALIZE_ANALYSIS"], batch_id,
                ),
            )
        _emit_progress(progress, "FINALIZE_ANALYSIS", 1, 1)
        self._audit("ADMIN_DATABASE_IMPORT_ANALYZED", {"batch_id": batch_id, **totals})
        return {
            "import_batch_id": batch_id,
            "mode": normalized_mode,
            "source_sha256": source_sha256,
            "source_instance_id": source_id,
            "source_path": str(path),
            "backup_path": backup_path,
            "backup_sha256": backup_sha256,
            "baseline_context": dict(baseline_context or {}),
            **totals,
        }

    def _prepare_apply(
        self,
        con: Any,
        *,
        batch_id: str,
        current_user: Mapping[str, Any] | Any,
        device_id: str,
    ) -> tuple[str, str, str, list[Any]]:
        con.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("admission-import-global",),
        )
        batch = con.execute(
            """SELECT status,mode,source_sha256,legacy_source_instance_id,
                      backup_path,backup_sha256,baseline_context_json
                 FROM admission_import_batches
                WHERE import_batch_id=%s::UUID FOR UPDATE""",
            (batch_id,),
        ).fetchone()
        if not batch or str(self._value(batch, "status", 0)).upper() != "ANALYZED":
            raise ValueError("El lote no esta disponible para aplicar.")
        active_batch_id = self._active_batch_locked(con, exclude_batch_id=batch_id)
        if active_batch_id:
            raise AdmissionImportTaskActiveError(active_batch_id)
        mode = str(self._value(batch, "mode", 1, "")).upper()
        IMPORT_LOG.info(
            "%s batch_id=%s",
            "BASELINE_APPLY_START" if mode == "SEED" else "MERGE_APPLY",
            batch_id,
        )
        source_sha256 = str(self._value(batch, "source_sha256", 2, ""))
        source_id = str(self._value(batch, "legacy_source_instance_id", 3, ""))
        backup_path = str(self._value(batch, "backup_path", 4, ""))
        backup_sha256 = str(self._value(batch, "backup_sha256", 5, ""))
        baseline_value = self._value(batch, "baseline_context_json", 6, {}) or {}
        baseline_context = (
            json.loads(baseline_value)
            if isinstance(baseline_value, str)
            else dict(baseline_value)
        )
        if not backup_path or not backup_sha256:
            raise ValueError(
                "El lote no tiene una copia de seguridad verificada. Analícelo nuevamente."
            )
        backup_file = Path(backup_path)
        if not backup_file.is_file() or _stream_sha256(
            backup_file, phase="VERIFY_BACKUP"
        ) != backup_sha256:
            raise ValueError(
                "La copia de seguridad previa no está disponible o cambió."
            )
        central_seed_id = ""
        if mode == "SEED":
            current_context = self._active_baseline_context(con)
            expected_context = _historical_context(
                {}, source_id, baseline_context=baseline_context
            )
            live_context = _historical_context(
                {}, source_id, baseline_context=current_context
            )
            if expected_context != live_context:
                raise ValueError(
                    "El turno central cambió desde la vista previa; analice nuevamente."
                )
            existing_seed = con.execute(
                """SELECT central_seed_id::TEXT,seed_source_fingerprint,status,
                          legacy_source_instance_id
                     FROM admission_central_seeds
                    WHERE seed_kind='INITIAL_BASELINE' FOR UPDATE"""
            ).fetchone()
            if existing_seed:
                _require_compatible_initial_baseline(
                    existing_fingerprint=str(
                        self._value(existing_seed, "seed_source_fingerprint", 1, "")
                    ),
                    existing_source_id=str(
                        self._value(
                            existing_seed,
                            "legacy_source_instance_id",
                            3,
                            "",
                        )
                    ),
                    source_sha256=source_sha256,
                    source_id=source_id,
                )
            central_seed_id = str(
                self._value(existing_seed, "central_seed_id", 0, "")
                or uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"sigeh-initial-baseline:{source_id}:{source_sha256}",
                )
            )
            con.execute(
                """INSERT INTO admission_central_seeds(
                       central_seed_id,legacy_source_instance_id,
                       seed_source_fingerprint,schema_version,status,
                       origin_device_id,seed_kind,operational_source_id,
                       operational_session_id,turn_id,applied_by_user_id,
                       metadata_json
                   ) VALUES(%s::UUID,%s,%s,1,'RUNNING',%s,'INITIAL_BASELINE',
                            %s::UUID,%s::UUID,%s,%s,%s::JSONB)
                   ON CONFLICT(central_seed_id) DO UPDATE SET
                       status=CASE WHEN admission_central_seeds.status='COMPLETED'
                           THEN 'COMPLETED' ELSE 'RUNNING' END,
                       metadata_json=EXCLUDED.metadata_json""",
                (
                    central_seed_id,
                    source_id,
                    source_sha256,
                    str(device_id),
                    baseline_context["operational_source_id"],
                    baseline_context["operational_session_id"],
                    int(baseline_context["turn_id"]),
                    canonical_user_id(current_user),
                    json.dumps(
                        {
                            "backup_path": backup_path,
                            "backup_sha256": backup_sha256,
                            "baseline_context": baseline_context,
                        },
                        sort_keys=True,
                    ),
                ),
            )
        con.execute(
            """UPDATE admission_import_batches
                  SET status='APPLYING',current_phase='PREPARE_APPLY',
                      progress_percent=0,processed_records=0,total_records=0,
                      status_message=%s,started_at=NOW(),progress_updated_at=NOW(),
                      last_heartbeat_at=NOW(),error_code=NULL,error_message=NULL
                WHERE import_batch_id=%s::UUID""",
            (IMPORT_PHASE_LABELS["PREPARE_APPLY"], batch_id),
        )
        rows = con.execute(
            """SELECT * FROM admission_import_staging
                WHERE import_batch_id=%s::UUID
                  AND classification IN ('INSERT','UPDATE')
                  AND result_code IS NULL
                ORDER BY row_number""",
            (batch_id,),
        ).fetchall()
        return mode, source_id, central_seed_id, list(rows)

    def apply(
        self,
        import_batch_id: str,
        *,
        current_user: Mapping[str, Any] | Any,
        device_id: str,
        sqlite_path: str | Path | None = None,
        expected_source_sha256: str = "",
        progress: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        self._require_admin(current_user)
        batch_id = str(uuid.UUID(str(import_batch_id)))
        IMPORT_LOG.info("IMPORT_APPLY_START batch_id=%s", batch_id)
        if sqlite_path is not None:
            source_path = Path(sqlite_path).expanduser().resolve()
            current_sha256 = _stream_sha256(
                source_path, progress=progress, phase="VERIFY_SOURCE"
            )
            if expected_source_sha256 and current_sha256 != expected_source_sha256:
                raise ValueError(
                    "La base SQLite cambió después del análisis. Analícela nuevamente."
                )
        # This is still before the first import-table query, while preserving
        # the local stale-preview guard: an altered file must not open cloud.
        self.ensure_schema()
        with self.connection_factory() as con:
            mode, source_id, central_seed_id, rows = self._prepare_apply(
                con,
                batch_id=batch_id,
                current_user=current_user,
                device_id=device_id,
            )
        _emit_progress(progress, "PREPARE_APPLY", 1, 1)
        counts: Counter[str] = Counter()
        total_rows = len(rows)
        for offset in range(0, total_rows, APPLY_BATCH_SIZE):
            chunk = rows[offset : offset + APPLY_BATCH_SIZE]
            with self.connection_factory() as con:
                for raw in chunk:
                    item = dict(raw)
                    payload_value = item.get("payload_json") or {}
                    payload = (
                        json.loads(payload_value)
                        if isinstance(payload_value, str)
                        else dict(payload_value)
                    )
                    global_id = str(item["global_attention_id"])
                    con.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (f"admission-import:{global_id}",),
                    )
                    current = con.execute(
                        """SELECT is_deleted,server_revision
                             FROM admission_attention_projection
                            WHERE global_attention_id=%s::UUID FOR UPDATE""",
                        (global_id,),
                    ).fetchone()
                    if current and bool(current[0]):
                        result_code = "SKIP_TOMBSTONED"
                    elif current and int(current[1] or 0) > int(
                        item.get("cloud_revision") or 0
                    ):
                        result_code = "SKIPPED_CLOUD_NEWER"
                    else:
                        context = _historical_context(
                            payload,
                            source_id
                            or str(
                                payload.get("legacy_source_instance_id") or "LEGACY"
                            ),
                        )
                        revision = int(current[1] or 0) if current else 0
                        now = _utc_now()
                        payload.update(context)
                        payload.update(
                            {
                                "reconciliation_status": payload.get(
                                    "reconciliation_status"
                                )
                                or "ADMIN_HISTORICAL_IMPORT",
                                "import_requested_by_user_id": canonical_user_id(
                                    current_user
                                ),
                                "import_requested_from_device_id": str(device_id),
                            }
                        )
                        event = SyncEvent(
                            event_uuid=str(uuid.uuid4()),
                            entity_type="attention",
                            entity_uuid=global_id,
                            operation="RECONCILE",
                            payload=payload,
                            operational_session_id=context["operational_session_id"],
                            generation=context["generation"],
                            device_id=context["origin_device_id"],
                            created_at=now,
                            base_version=revision,
                            operational_source_id=context["operational_source_id"],
                            turn_id=int(payload.get("turn_id") or 0),
                            origin_user_id=str(payload.get("origin_user_id") or ""),
                            origin_username=context["admission_username"],
                            created_at_device=str(
                                payload.get("created_at_device") or now
                            ),
                            created_at_effective_utc=str(
                                payload.get("created_at_effective_utc") or now
                            ),
                            device_local_sequence=int(
                                payload.get("device_local_sequence") or 0
                            ),
                        )
                        con.execute(
                            """INSERT INTO admission_sync_events(
                                   event_uuid,entity_type,entity_uuid,operation,payload_json,
                                   operational_session_id,generation,origin_device_id,
                                   base_version,resulting_version,created_at,received_at,
                                   operational_source_id,turn_id,origin_user_id,origin_username,
                                   created_at_device,created_at_effective_utc,
                                   device_local_sequence,reconciliation_status
                               ) VALUES(%s::UUID,'attention',%s::UUID,'RECONCILE',%s::JSONB,
                                        %s::UUID,%s,%s,%s,%s,%s,NOW(),%s::UUID,%s,%s,%s,
                                        %s,%s,%s,%s)""",
                            (
                                event.event_uuid,
                                global_id,
                                event.payload_json(),
                                event.operational_session_id,
                                event.generation,
                                event.device_id,
                                revision,
                                revision + 1,
                                event.created_at,
                                event.operational_source_id,
                                event.turn_id,
                                event.origin_user_id,
                                event.origin_username,
                                event.created_at_device,
                                event.created_at_effective_utc,
                                event.device_local_sequence,
                                str(payload["reconciliation_status"]),
                            ),
                        )
                        AdmissionCloudRepository._materialize_attention(
                            con,
                            event,
                            revision + 1,
                        )
                        result_code = (
                            "APPLIED_INSERT" if not current else "APPLIED_UPDATE"
                        )
                    con.execute(
                        """UPDATE admission_import_staging
                              SET result_code=%s,applied_at=NOW()
                            WHERE import_batch_id=%s::UUID AND row_number=%s""",
                        (result_code, batch_id, int(item["row_number"])),
                    )
                    counts[result_code] += 1
            _emit_progress(
                progress,
                "APPLY_ATTENTIONS",
                min(offset + len(chunk), total_rows),
                total_rows,
            )

        if sqlite_path is not None:
            patient_result = seed_admission_database_to_cloud(
                sqlite_path,
                self.connection_factory,
                progress=lambda current, total: _emit_progress(
                    progress,
                    "SYNC_PATIENT_DIRECTORY",
                    current,
                    total,
                ),
            )
            counts["PATIENTS_INSERTED"] = int(patient_result.get("inserted") or 0)
            counts["PATIENTS_EXISTING"] = int(
                patient_result.get("already_present") or 0
            )
            counts["PATIENT_CONFLICTS"] = int(patient_result.get("conflicts") or 0)
        with self.connection_factory() as con:
            if central_seed_id:
                con.execute(
                    """UPDATE admission_central_seeds
                          SET status='COMPLETED',imported_records=%s,
                              seed_completed_at=NOW()
                        WHERE central_seed_id=%s::UUID""",
                    (sum(counts.values()), central_seed_id),
                )
            con.execute(
                """UPDATE admission_import_batches
                      SET status='COMPLETED',applied_at=NOW(),totals_json=%s::JSONB,
                          current_phase='FINALIZE_APPLY',progress_percent=100,
                          processed_records=%s,total_records=%s,
                          status_message=%s,completed_at=NOW(),
                          progress_updated_at=NOW(),last_heartbeat_at=NOW()
                    WHERE import_batch_id=%s::UUID""",
                (
                    json.dumps(dict(counts), sort_keys=True), total_rows, total_rows,
                    IMPORT_PHASE_LABELS["FINALIZE_APPLY"], batch_id,
                ),
            )
            con.execute(
                """UPDATE admission_dataset_state
                      SET dataset_epoch=dataset_epoch+1,updated_at=NOW(),
                          last_import_batch_id=%s::UUID WHERE singleton=1""",
                (batch_id,),
            )
        result = {"import_batch_id": batch_id, **dict(counts)}
        _emit_progress(progress, "FINALIZE_APPLY", 1, 1)
        self._audit("ADMIN_DATABASE_IMPORT_APPLIED", result)
        IMPORT_LOG.info(
            "%s batch_id=%s applied_insert=%s applied_update=%s",
            "BASELINE_APPLY_DONE" if mode == "SEED" else "MERGE_APPLY",
            batch_id,
            counts.get("APPLIED_INSERT", 0),
            counts.get("APPLIED_UPDATE", 0),
        )
        return result


__all__ = [
    "ACTIVE_IMPORT_STATUSES",
    "IMPORT_MODES",
    "IMPORT_PHASE_LABELS",
    "IMPORT_STALE_SECONDS",
    "AdmissionDatabaseImporter",
    "AdmissionImportSchemaError",
    "AdmissionImportTaskActiveError",
    "import_progress_percent",
]
