"""Read-only capacity analysis and guarded maintenance policies.

The analyzer never removes data.  Destructive maintenance helpers are explicit,
transactional and require a caller-provided confirmation after a fresh dry-run.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

MIB = 1024 * 1024
DEFAULT_DATABASE_LIMIT_BYTES = 500 * MIB
CAPACITY_SAMPLE_INTERVAL = timedelta(hours=6)
MINIMUM_TREND_SPAN = timedelta(days=3)
IMPORT_COMPLETED_RETENTION = timedelta(days=7)
IMPORT_FAILED_RETENTION = timedelta(days=30)
EVENT_RETENTION = timedelta(days=7)
ACTIVE_IMPORT_STATUSES = frozenset({"ANALYZING", "APPLYING"})
CAPACITY_LOG = logging.getLogger("database_capacity")

EVENT_STREAMS = {
    "ATTENTION": ("admission_sync_events", "received_at"),
    "PATIENT_DIRECTORY": ("admission_patient_directory_events", "created_at"),
}


CAPACITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS database_capacity_history(
  id BIGSERIAL PRIMARY KEY,
  captured_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  database_size_bytes BIGINT NOT NULL,
  database_limit_bytes BIGINT NOT NULL,
  usage_percent NUMERIC(8,3) NOT NULL,
  largest_relations_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
  dead_rows_jsonb JSONB NOT NULL DEFAULT '[]'::jsonb,
  captured_by TEXT NOT NULL,
  notes TEXT,
  pdf_storage_bytes BIGINT NOT NULL DEFAULT 0,
  sync_events_bytes BIGINT NOT NULL DEFAULT 0,
  import_staging_bytes BIGINT NOT NULL DEFAULT 0,
  patient_events_bytes BIGINT NOT NULL DEFAULT 0
);
ALTER TABLE database_capacity_history
  ADD COLUMN IF NOT EXISTS pdf_storage_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE database_capacity_history
  ADD COLUMN IF NOT EXISTS sync_events_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE database_capacity_history
  ADD COLUMN IF NOT EXISTS import_staging_bytes BIGINT NOT NULL DEFAULT 0;
ALTER TABLE database_capacity_history
  ADD COLUMN IF NOT EXISTS patient_events_bytes BIGINT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_database_capacity_history_captured
  ON database_capacity_history(captured_at DESC);
CREATE TABLE IF NOT EXISTS admission_replication_event_floors(
  stream_name TEXT PRIMARY KEY,
  minimum_available_sequence BIGINT NOT NULL DEFAULT 0,
  checkpoint_sequence BIGINT NOT NULL DEFAULT 0,
  retention_days INTEGER NOT NULL DEFAULT 7,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  CHECK(stream_name IN ('ATTENTION','PATIENT_DIRECTORY')),
  CHECK(minimum_available_sequence >= 0),
  CHECK(checkpoint_sequence >= minimum_available_sequence)
);
INSERT INTO admission_replication_event_floors(
  stream_name,minimum_available_sequence,checkpoint_sequence,retention_days
) VALUES
  ('ATTENTION',0,0,7),
  ('PATIENT_DIRECTORY',0,0,7)
ON CONFLICT(stream_name) DO NOTHING;
"""


def _mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def format_bytes(value: float) -> str:
    amount = max(0.0, float(value or 0))
    if amount >= 1024**3:
        return f"{amount / 1024**3:,.2f} GB"
    if amount >= MIB:
        return f"{amount / MIB:,.2f} MB"
    if amount >= 1024:
        return f"{amount / 1024:,.2f} KB"
    return f"{int(amount):,} bytes"


def capacity_status(usage_percent: float) -> tuple[str, str]:
    usage = float(usage_percent or 0.0)
    if usage >= 95.0:
        return "VERY_CRITICAL", "Muy crítico"
    if usage >= 85.0:
        return "CRITICAL", "Crítico"
    if usage >= 70.0:
        return "ATTENTION", "Atención"
    return "NORMAL", "Normal"


def classify_import_staging_batch(row: Mapping[str, Any], *, now: datetime) -> str:
    status = str(row.get("status") or "").upper()
    completed_at = row.get("completed_at") or row.get("applied_at")
    age = now - completed_at if completed_at else timedelta(0)
    incomplete = int(row.get("incomplete_rows") or 0)
    if status in ACTIVE_IMPORT_STATUSES:
        return "ACTIVE_JOB"
    if status == "ANALYZED":
        return "AWAITING_APPLY"
    if status == "COMPLETED" and incomplete:
        return "INCOMPLETE_RESULTS"
    if status == "COMPLETED" and completed_at and age >= IMPORT_COMPLETED_RETENTION:
        return "SAFE_AFTER_RETENTION"
    if status == "FAILED" and completed_at and age >= IMPORT_FAILED_RETENTION:
        return "SAFE_AFTER_FAILED_RETENTION"
    return "RETENTION_ACTIVE"


@dataclass(frozen=True, slots=True)
class CapacityTrend:
    state: str
    label: str
    monthly_bytes: float | None
    observation_days: float


def calculate_capacity_trend(
    samples: Iterable[Mapping[str, Any]],
    *,
    minimum_span: timedelta = MINIMUM_TREND_SPAN,
) -> CapacityTrend:
    ordered = sorted(
        (
            (
                item.get("captured_at"),
                int(item.get("database_size_bytes") or 0),
            )
            for item in samples
            if item.get("captured_at") is not None
        ),
        key=lambda item: item[0],
    )
    if len(ordered) < 2:
        return CapacityTrend("INSUFFICIENT", "Aún no hay suficientes datos.", None, 0.0)
    first_at, first_bytes = ordered[0]
    last_at, last_bytes = ordered[-1]
    span = last_at - first_at
    observation_days = max(0.0, span.total_seconds() / 86400.0)
    if span < minimum_span:
        return CapacityTrend(
            "INSUFFICIENT",
            "Aún no hay suficientes datos (se requieren varios días).",
            None,
            observation_days,
        )
    monthly_bytes = (last_bytes - first_bytes) * (30.4375 / observation_days)
    if monthly_bytes < -0.25 * MIB:
        label = f"Tendencia decreciente ({format_bytes(abs(monthly_bytes))}/mes)."
        state = "DECREASING"
    elif monthly_bytes > 0.25 * MIB:
        label = f"+{format_bytes(monthly_bytes)}/mes estimados."
        state = "GROWING"
    else:
        label = "Tendencia estable."
        state = "STABLE"
    return CapacityTrend(state, label, monthly_bytes, observation_days)


class DatabaseCapacityAnalyzer:
    """Collects PostgreSQL capacity facts without mixing SQL into the GUI."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        database_limit_bytes: int = DEFAULT_DATABASE_LIMIT_BYTES,
        archive_root: str | Path | None = None,
    ):
        self.connection_factory = connection_factory
        self.database_limit_bytes = max(1, int(database_limit_bytes))
        self.archive_root = Path(archive_root).resolve() if archive_root else None

    def ensure_schema(self) -> bool:
        """Best-effort schema upgrade; capacity reads remain usable on a replica."""
        try:
            with self.connection_factory() as con:
                for statement in (
                    part.strip() for part in CAPACITY_SCHEMA.split(";") if part.strip()
                ):
                    con.execute(statement)
        except Exception as exc:  # noqa: BLE001 - driver errors vary by deployment
            CAPACITY_LOG.warning(
                "DATABASE_CAPACITY_SCHEMA_UNAVAILABLE error=%s", type(exc).__name__
            )
            return False
        return True

    @staticmethod
    def _rows(con: Any, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [_mapping(row) for row in con.execute(sql, params).fetchall()]

    @staticmethod
    def _one(con: Any, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        return _mapping(con.execute(sql, params).fetchone())

    @staticmethod
    def _relation_exists(con: Any, relation: str) -> bool:
        row = con.execute("SELECT to_regclass(%s) IS NOT NULL", (relation,)).fetchone()
        return bool(row and row[0])

    @staticmethod
    def _relation_size(con: Any, relation: str) -> int:
        row = con.execute(
            "SELECT COALESCE(pg_total_relation_size(to_regclass(%s)),0)",
            (relation,),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _capture_relations(
        self, con: Any
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        tables = self._rows(
            con,
            """SELECT c.relname AS name,
                      pg_total_relation_size(c.oid) AS total_bytes,
                      pg_relation_size(c.oid) AS heap_bytes,
                      pg_indexes_size(c.oid) AS index_bytes,
                      COALESCE(s.n_live_tup,0) AS live_rows,
                      COALESCE(s.n_dead_tup,0) AS dead_rows,
                      s.last_autovacuum,s.last_vacuum
                 FROM pg_class c
                 JOIN pg_namespace n ON n.oid=c.relnamespace
                 LEFT JOIN pg_stat_user_tables s ON s.relid=c.oid
                WHERE n.nspname='public' AND c.relkind IN ('r','m')
                ORDER BY pg_total_relation_size(c.oid) DESC LIMIT 10""",
        )
        indexes = self._rows(
            con,
            """SELECT s.indexrelname AS name,s.relname AS table_name,
                      pg_relation_size(s.indexrelid) AS total_bytes,
                      COALESCE(s.idx_scan,0) AS scans,
                      COALESCE(s.idx_tup_read,0) AS tuples_read,
                      COALESCE(s.idx_tup_fetch,0) AS tuples_fetched,
                      pg_get_indexdef(s.indexrelid) AS definition
                 FROM pg_stat_user_indexes s
                ORDER BY pg_relation_size(s.indexrelid) DESC LIMIT 10""",
        )
        return tables, indexes

    def analyze_import_staging_cleanup(
        self, con: Any | None = None, *, now: datetime | None = None
    ) -> dict[str, Any]:
        if con is None:
            with self.connection_factory() as managed:
                return self.analyze_import_staging_cleanup(managed, now=now)
        if not self._relation_exists(con, "public.admission_import_staging"):
            return {"relation_bytes": 0, "safe_bytes": 0, "safe_rows": 0, "batches": []}
        current = now or datetime.now(timezone.utc)
        rows = self._rows(
            con,
            """SELECT b.import_batch_id::TEXT AS import_batch_id,b.status,b.mode,
                      b.imported_at,b.applied_at,b.completed_at,b.last_heartbeat_at,
                      b.processed_records,b.total_records,
                      COUNT(s.*) AS staging_rows,
                      COUNT(s.*) FILTER(
                        WHERE s.result_code IS NULL OR BTRIM(s.result_code)=''
                      ) AS incomplete_rows,
                      COALESCE(SUM(pg_column_size(s.*)),0) AS logical_bytes
                 FROM admission_import_batches b
                 LEFT JOIN admission_import_staging s
                   ON s.import_batch_id=b.import_batch_id
                GROUP BY b.import_batch_id,b.status,b.mode,b.imported_at,
                         b.applied_at,b.completed_at,b.last_heartbeat_at,
                         b.processed_records,b.total_records
                ORDER BY b.imported_at DESC""",
        )
        safe_bytes = 0
        safe_rows = 0
        for row in rows:
            reason = classify_import_staging_batch(row, now=current)
            row["cleanup_state"] = reason
            row["safe_to_purge"] = reason.startswith("SAFE_")
            if row["safe_to_purge"]:
                safe_bytes += int(row.get("logical_bytes") or 0)
                safe_rows += int(row.get("staging_rows") or 0)
        return {
            "relation_bytes": self._relation_size(
                con, "public.admission_import_staging"
            ),
            "safe_bytes": safe_bytes,
            "safe_rows": safe_rows,
            "batches": rows,
        }

    def purge_safe_import_staging(self, *, confirmed: bool = False) -> dict[str, int]:
        if not confirmed:
            raise PermissionError(
                "La purga segura requiere confirmación administrativa."
            )
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext('admission-import-staging-cleanup'))"
            )
            plan = self.analyze_import_staging_cleanup(con)
            batch_ids = [
                row["import_batch_id"]
                for row in plan["batches"]
                if row.get("safe_to_purge")
            ]
            deleted = 0
            for batch_id in batch_ids:
                row = con.execute(
                    """DELETE FROM admission_import_staging s
                         USING admission_import_batches b
                         WHERE s.import_batch_id=b.import_batch_id
                           AND b.import_batch_id=%s::UUID
                           AND b.status='COMPLETED'
                           AND COALESCE(b.completed_at,b.applied_at)
                               < NOW()-%s::INTERVAL
                           AND NOT EXISTS(
                             SELECT 1 FROM admission_import_staging pending
                              WHERE pending.import_batch_id=b.import_batch_id
                                AND (pending.result_code IS NULL
                                     OR BTRIM(pending.result_code)='')
                           )
                         RETURNING s.row_number""",
                    (batch_id, f"{IMPORT_COMPLETED_RETENTION.days} days"),
                ).fetchall()
                deleted += len(row)
        return {"batches": len(batch_ids), "rows": deleted}

    def analyze_pdf_storage(self, con: Any | None = None) -> dict[str, Any]:
        if con is None:
            with self.connection_factory() as managed:
                return self.analyze_pdf_storage(managed)
        if not self._relation_exists(con, "public.pdf_storage"):
            return {"relation_bytes": 0, "logical_bytes": 0, "rows": 0}
        totals = self._one(
            con,
            """SELECT COUNT(*) AS rows,
                      COALESCE(SUM(OCTET_LENGTH(file_data)),0) AS logical_bytes,
                      COUNT(*) FILTER(WHERE document_type='UNKNOWN') AS unknown_rows
                 FROM pdf_storage""",
        )
        classification = self._one(
            con,
            """SELECT
                 COUNT(*) FILTER(WHERE r.id IS NOT NULL) AS receipt_rows,
                 COUNT(*) FILTER(WHERE r.id IS NULL AND p.filename LIKE 'recibo_%%')
                   AS orphan_receipt_rows,
                 COALESCE(SUM(OCTET_LENGTH(p.file_data))
                   FILTER(WHERE r.id IS NOT NULL),0) AS receipt_bytes
               FROM pdf_storage p
               LEFT JOIN recibos r ON r.pdf_filename=p.filename""",
        )
        digest_available = bool(
            self._one(
                con,
                "SELECT to_regprocedure('digest(bytea,text)') IS NOT NULL AS available",
            ).get("available")
        )
        verified = {}
        if digest_available:
            verified = self._one(
                con,
                """SELECT COUNT(*) AS rows,
                          COALESCE(SUM(OCTET_LENGTH(p.file_data)),0) AS logical_bytes
                     FROM pdf_storage p
                     JOIN document_external_files e
                       ON e.filename=p.filename
                      AND e.status='AVAILABLE' AND e.verified_at IS NOT NULL
                      AND e.size_bytes=OCTET_LENGTH(p.file_data)
                      AND e.sha256=ENCODE(DIGEST(p.file_data,'sha256'),'hex')""",
            )
        return {
            **totals,
            **classification,
            "verified_external_rows": int(verified.get("rows") or 0),
            "verified_external_bytes": int(verified.get("logical_bytes") or 0),
            "migratable_after_verification_bytes": int(
                classification.get("receipt_bytes") or 0
            ),
            "relation_bytes": self._relation_size(con, "public.pdf_storage"),
        }

    def verify_external_pdf(self, filename: str) -> dict[str, Any]:
        """Verifies one exact external copy; UNKNOWN/unlinked rows stay protected."""
        if self.archive_root is None:
            return {"safe": False, "reason": "ARCHIVE_ROOT_NOT_CONFIGURED"}
        with self.connection_factory() as con:
            row = self._one(
                con,
                """SELECT p.filename,p.file_data,p.document_type,p.owner_receipt_id,
                          e.source_table,e.source_key,e.sha256,e.size_bytes,
                          e.archive_relative_path,e.status,e.verified_at
                     FROM pdf_storage p
                     LEFT JOIN document_external_files e
                       ON e.filename=p.filename
                      AND e.status='AVAILABLE' AND e.verified_at IS NOT NULL
                    WHERE p.filename=%s LIMIT 1""",
                (str(filename),),
            )
        if not row:
            return {"safe": False, "reason": "NOT_FOUND"}
        if not row.get("source_table") or not row.get("source_key"):
            return {"safe": False, "reason": "UNCLASSIFIED_OR_UNLINKED"}
        root = self.archive_root
        candidate = (root / str(row.get("archive_relative_path") or "")).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return {"safe": False, "reason": "ARCHIVE_PATH_ESCAPE"}
        if not candidate.is_file():
            return {"safe": False, "reason": "EXTERNAL_FILE_MISSING"}
        payload = bytes(row.get("file_data") or b"")
        digest = hashlib.sha256(payload).hexdigest()
        external_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        safe = bool(
            payload
            and candidate.stat().st_size
            == len(payload)
            == int(row.get("size_bytes") or 0)
            and digest == external_digest == str(row.get("sha256") or "")
        )
        return {
            "safe": safe,
            "reason": "VERIFIED" if safe else "HASH_OR_SIZE_MISMATCH",
            "size_bytes": len(payload),
            "sha256": digest if safe else "",
        }

    def analyze_event_retention(
        self,
        con: Any,
        *,
        stream_name: str,
        table: str,
        timestamp_column: str,
        projection_ready: bool,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current = now or datetime.now(timezone.utc)
        cutoff = current - EVENT_RETENTION
        candidate = self._one(
            con,
            f"""SELECT COALESCE(MAX(sequence),0) AS candidate_floor,
                       COUNT(*) AS candidate_rows,
                       COALESCE(SUM(pg_column_size(e.*)),0) AS logical_bytes
                  FROM {table} e WHERE {timestamp_column}<%s""",
            (cutoff,),
        )
        window = self._one(
            con,
            f"SELECT COALESCE(MIN(sequence),0) AS min_sequence,"
            f"COALESCE(MAX(sequence),0) AS max_sequence,COUNT(*) AS rows FROM {table}",
        )
        floor_row = {}
        if self._relation_exists(con, "public.admission_replication_event_floors"):
            floor_row = self._one(
                con,
                """SELECT minimum_available_sequence,checkpoint_sequence
                     FROM admission_replication_event_floors WHERE stream_name=%s""",
                (stream_name,),
            )
        return {
            **window,
            **candidate,
            "current_floor": int(floor_row.get("minimum_available_sequence") or 0),
            "checkpoint_sequence": int(floor_row.get("checkpoint_sequence") or 0),
            "projection_ready": bool(projection_ready),
            "retention_safe": bool(
                projection_ready and int(candidate.get("candidate_floor") or 0) > 0
            ),
            "retention_days": EVENT_RETENTION.days,
        }

    def prune_safe_events(
        self, stream_name: str, *, confirmed: bool = False
    ) -> dict[str, int]:
        """Prunes one verified stream behind an atomic projection checkpoint."""
        normalized = str(stream_name or "").upper()
        if normalized not in EVENT_STREAMS:
            raise ValueError("Flujo de eventos no reconocido.")
        if not confirmed:
            raise PermissionError("La retención requiere confirmación administrativa.")
        if not self.ensure_schema():
            raise RuntimeError(
                "No fue posible preparar el checkpoint de retención de eventos."
            )
        table, timestamp_column = EVENT_STREAMS[normalized]
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"database-event-retention:{normalized}",),
            )
            con.execute(f"LOCK TABLE {table} IN SHARE ROW EXCLUSIVE MODE")
            projection_ready = (
                self._attention_projection_ready(con)
                if normalized == "ATTENTION"
                else self._patient_projection_ready(con)
            )
            plan = self.analyze_event_retention(
                con,
                stream_name=normalized,
                table=table,
                timestamp_column=timestamp_column,
                projection_ready=projection_ready,
            )
            if not plan["retention_safe"]:
                raise RuntimeError(
                    "La proyección no permite una retención segura en este momento."
                )
            candidate_floor = int(plan["candidate_floor"] or 0)
            checkpoint = int(plan["max_sequence"] or 0)
            if candidate_floor <= 0 or checkpoint < candidate_floor:
                return {"rows": 0, "floor": 0, "checkpoint": checkpoint}
            con.execute(
                """UPDATE admission_replication_event_floors
                      SET minimum_available_sequence=%s,
                          checkpoint_sequence=%s,
                          updated_at=NOW(),
                          details_json=%s::jsonb
                    WHERE stream_name=%s""",
                (
                    candidate_floor,
                    checkpoint,
                    json.dumps(
                        {
                            "policy": "PROJECTION_CHECKPOINT",
                            "retention_days": EVENT_RETENTION.days,
                        }
                    ),
                    normalized,
                ),
            )
            deleted = con.execute(
                f"""DELETE FROM {table}
                      WHERE sequence<=%s
                        AND {timestamp_column}<NOW()-%s::INTERVAL
                      RETURNING sequence""",
                (candidate_floor, f"{EVENT_RETENTION.days} days"),
            ).fetchall()
        CAPACITY_LOG.info(
            "%s_EVENT_RETENTION_RUN rows=%s floor=%s checkpoint=%s",
            normalized,
            len(deleted),
            candidate_floor,
            checkpoint,
        )
        return {
            "rows": len(deleted),
            "floor": candidate_floor,
            "checkpoint": checkpoint,
        }

    def _attention_projection_ready(self, con: Any) -> bool:
        columns = self._one(
            con,
            """SELECT COUNT(*) AS value FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='admission_attention_projection'
                  AND column_name='latest_payload_json'""",
        )
        if not int(columns.get("value") or 0):
            return False
        missing = self._one(
            con,
            """SELECT COUNT(*) AS value
                 FROM admission_attention_projection p
                 LEFT JOIN LATERAL (
                   SELECT e.payload_json
                     FROM admission_sync_events e
                    WHERE e.entity_type='attention'
                      AND e.entity_uuid=p.global_attention_id
                    ORDER BY e.sequence DESC LIMIT 1
                 ) latest ON TRUE
                WHERE p.global_attention_id IS NULL
                   OR p.latest_payload_json IS NULL
                   OR p.latest_payload_json='{}'::jsonb
                   OR (latest.payload_json IS NOT NULL
                       AND p.latest_payload_json IS DISTINCT FROM latest.payload_json)""",
        )
        return int(missing.get("value") or 0) == 0

    def _patient_projection_ready(self, con: Any) -> bool:
        row = self._one(
            con,
            """SELECT
                 (SELECT COUNT(*) FROM admission_patient_directory) AS projection_rows,
                 (SELECT COUNT(DISTINCT global_patient_id)
                    FROM admission_patient_directory_events) AS event_entities,
                 (SELECT COUNT(*) FROM admission_patient_directory_events e
                   LEFT JOIN admission_patient_directory p
                     ON p.global_patient_id=e.global_patient_id
                  WHERE p.global_patient_id IS NULL) AS missing_entities""",
        )
        return bool(
            int(row.get("missing_entities") or 0) == 0
            and int(row.get("projection_rows") or 0)
            >= int(row.get("event_entities") or 0)
        )

    def _persist_sample(self, snapshot: Mapping[str, Any], actor: str) -> bool:
        with self.connection_factory() as con:
            recent = con.execute(
                """SELECT captured_at FROM database_capacity_history
                    ORDER BY captured_at DESC LIMIT 1"""
            ).fetchone()
            last = recent[0] if recent else None
            if last and datetime.now(timezone.utc) - last < CAPACITY_SAMPLE_INTERVAL:
                return False
            con.execute(
                """INSERT INTO database_capacity_history(
                     database_size_bytes,database_limit_bytes,usage_percent,
                     largest_relations_jsonb,dead_rows_jsonb,captured_by,notes,
                     pdf_storage_bytes,sync_events_bytes,import_staging_bytes,
                     patient_events_bytes
                   ) VALUES(%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s,%s)""",
                (
                    int(snapshot["database_size_bytes"]),
                    int(snapshot["database_limit_bytes"]),
                    float(snapshot["usage_percent"]),
                    json.dumps(snapshot["top_tables"], default=str),
                    json.dumps(snapshot["top_tables"], default=str),
                    str(actor or "SYSTEM"),
                    "CAPACITY_PANEL_SAMPLE",
                    int(snapshot["component_bytes"].get("pdf_storage", 0)),
                    int(snapshot["component_bytes"].get("admission_sync_events", 0)),
                    int(snapshot["component_bytes"].get("admission_import_staging", 0)),
                    int(
                        snapshot["component_bytes"].get(
                            "admission_patient_directory_events", 0
                        )
                    ),
                ),
            )
        return True

    def analyze(
        self, *, actor: str = "SYSTEM", persist_sample: bool = True
    ) -> dict[str, Any]:
        schema_writable = self.ensure_schema()
        with self.connection_factory() as con:
            database_size = int(
                con.execute("SELECT pg_database_size(current_database())").fetchone()[0]
            )
            top_tables, top_indexes = self._capture_relations(con)
            component_bytes = {
                name: self._relation_size(con, f"public.{name}")
                for name in (
                    "pdf_storage",
                    "admission_sync_events",
                    "admission_import_staging",
                    "admission_patient_directory_events",
                )
            }
            staging = self.analyze_import_staging_cleanup(con)
            pdf = self.analyze_pdf_storage(con)
            sync = self.analyze_event_retention(
                con,
                stream_name="ATTENTION",
                table="admission_sync_events",
                timestamp_column="received_at",
                projection_ready=self._attention_projection_ready(con),
            )
            patient = self.analyze_event_retention(
                con,
                stream_name="PATIENT_DIRECTORY",
                table="admission_patient_directory_events",
                timestamp_column="created_at",
                projection_ready=self._patient_projection_ready(con),
            )
        usage = database_size * 100.0 / self.database_limit_bytes
        snapshot = {
            "captured_at": datetime.now(timezone.utc),
            "database_size_bytes": database_size,
            "database_limit_bytes": self.database_limit_bytes,
            "usage_percent": usage,
            "free_bytes": max(0, self.database_limit_bytes - database_size),
            "status": capacity_status(usage),
            "top_tables": top_tables,
            "top_indexes": top_indexes,
            "component_bytes": component_bytes,
            "staging": staging,
            "pdf": pdf,
            "sync_events": sync,
            "patient_events": patient,
            "capacity_schema_writable": schema_writable,
        }
        if persist_sample and schema_writable:
            snapshot["sample_saved"] = self._persist_sample(snapshot, actor)
        elif persist_sample:
            snapshot["sample_saved"] = False
        with self.connection_factory() as con:
            history = self._rows(
                con,
                """SELECT captured_at,database_size_bytes
                     FROM database_capacity_history
                    WHERE captured_at>=NOW()-INTERVAL '31 days'
                    ORDER BY captured_at""",
            )
        snapshot["trend"] = calculate_capacity_trend(history)
        snapshot["safe_recoverable_bytes"] = int(staging["safe_bytes"]) + int(
            pdf["verified_external_bytes"]
        )
        snapshot["after_pdf_bytes"] = int(pdf["migratable_after_verification_bytes"])
        snapshot["after_checkpoint_bytes"] = int(sync["logical_bytes"]) + int(
            patient["logical_bytes"]
        )
        return snapshot


__all__ = [
    "ACTIVE_IMPORT_STATUSES",
    "CAPACITY_SCHEMA",
    "CapacityTrend",
    "DatabaseCapacityAnalyzer",
    "calculate_capacity_trend",
    "capacity_status",
    "classify_import_staging_batch",
    "format_bytes",
]
