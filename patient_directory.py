"""Directorio central de pacientes de Admisión.

La base central conserva la identidad de cada paciente y cada réplica SQLite
mantiene una copia local consultable.  Las consultas remotas son exactas y se
usan solamente para un miss local o el bootstrap incremental.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlite_write_coordinator import (
    connect_local_sqlite,
    get_sqlite_write_coordinator,
    prepare_sqlite_database,
)

PATIENT_DIRECTORY_SCHEMA_VERSION = 1
PATIENT_ENTITY_TYPE = "patient"

POSTGRES_PATIENT_DIRECTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS admission_patient_directory(
  global_patient_id UUID PRIMARY KEY,
  patient_name TEXT NOT NULL,
  cedula TEXT,
  cedula_normalized TEXT,
  nss TEXT,
  nss_normalized TEXT,
  phone TEXT,
  address TEXT,
  nationality TEXT,
  canonical_ars TEXT,
  legacy_source_instance_id TEXT,
  legacy_patient_id BIGINT,
  server_revision INTEGER NOT NULL DEFAULT 1,
  is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS cedula_normalized TEXT;
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS nss_normalized TEXT;
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS server_revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS legacy_source_instance_id TEXT;
ALTER TABLE admission_patient_directory ADD COLUMN IF NOT EXISTS legacy_patient_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_admission_patient_directory_cedula
  ON admission_patient_directory(cedula_normalized) WHERE is_deleted=FALSE;
CREATE INDEX IF NOT EXISTS idx_admission_patient_directory_nss
  ON admission_patient_directory(nss_normalized) WHERE is_deleted=FALSE;
CREATE INDEX IF NOT EXISTS idx_admission_patient_directory_revision
  ON admission_patient_directory(server_revision,global_patient_id);
CREATE INDEX IF NOT EXISTS idx_admission_patient_directory_legacy
  ON admission_patient_directory(legacy_source_instance_id,legacy_patient_id);
CREATE TABLE IF NOT EXISTS legacy_entity_uuid_map(
  entity_type TEXT NOT NULL,
  source_instance_id TEXT NOT NULL,
  legacy_id BIGINT NOT NULL,
  global_uuid UUID NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY(entity_type,source_instance_id,legacy_id),
  UNIQUE(entity_type,global_uuid)
);
ALTER TABLE legacy_entity_uuid_map
  DROP CONSTRAINT IF EXISTS legacy_entity_uuid_map_entity_type_global_uuid_key;
CREATE TABLE IF NOT EXISTS admission_patient_directory_events(
  sequence BIGSERIAL PRIMARY KEY,
  event_uuid UUID NOT NULL UNIQUE,
  global_patient_id UUID NOT NULL REFERENCES admission_patient_directory(global_patient_id),
  operation TEXT NOT NULL,
  server_revision INTEGER NOT NULL,
  payload_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admission_patient_directory_events_cursor
  ON admission_patient_directory_events(sequence);
CREATE INDEX IF NOT EXISTS idx_admission_patient_directory_events_patient
  ON admission_patient_directory_events(global_patient_id,server_revision);
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
) VALUES('PATIENT_DIRECTORY',0,0,7)
ON CONFLICT(stream_name) DO NOTHING;
CREATE TABLE IF NOT EXISTS admission_patient_seed_conflicts(
  id BIGSERIAL PRIMARY KEY,
  source_instance_id TEXT NOT NULL,
  legacy_patient_id BIGINT NOT NULL,
  reason_code TEXT NOT NULL,
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(source_instance_id,legacy_patient_id,reason_code)
);
"""


def normalize_patient_document(value: Any) -> str:
    """Normaliza cédula/NSS sin imponer un formato visual específico."""
    return re.sub(r"\D", "", str(value or ""))


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid_or_empty(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        return ""


def _mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


@dataclass(frozen=True, slots=True)
class PatientSeedResult:
    source_path: str
    source_instance_id: str
    local_patients: int
    local_attentions: int
    inserted: int
    updated: int
    already_present: int
    conflicts: int

    def as_mapping(self) -> dict[str, Any]:
        return {
            "source_path": self.source_path,
            "source_instance_id": self.source_instance_id,
            "local_patients": self.local_patients,
            "local_attentions": self.local_attentions,
            "cloud_inserted": self.inserted,
            "cloud_updated": self.updated,
            "cloud_already_present": self.already_present,
            "conflicts": self.conflicts,
        }


class CentralPatientDirectoryRepository:
    """Repositorio PostgreSQL del directorio canónico de pacientes."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self.connection_factory = connection_factory

    def ensure_schema(self) -> None:
        with self.connection_factory() as con:
            execute_script = getattr(con, "executescript", None)
            if callable(execute_script):
                execute_script(POSTGRES_PATIENT_DIRECTORY_SCHEMA)
            else:
                for statement in (
                    part.strip()
                    for part in POSTGRES_PATIENT_DIRECTORY_SCHEMA.split(";")
                    if part.strip()
                ):
                    con.execute(statement)

    @staticmethod
    def _payload(row: Mapping[str, Any]) -> dict[str, Any]:
        data = dict(row)
        return {
            "global_patient_id": str(data.get("global_patient_id") or ""),
            "nombre": str(data.get("patient_name") or data.get("nombre") or ""),
            "cedula": str(data.get("cedula") or ""),
            "cedula_normalized": str(data.get("cedula_normalized") or ""),
            "nss": str(data.get("nss") or ""),
            "nss_normalized": str(data.get("nss_normalized") or ""),
            "telefono": str(data.get("phone") or data.get("telefono") or ""),
            "direccion": str(data.get("address") or data.get("direccion") or ""),
            "nacionalidad": str(
                data.get("nationality") or data.get("nacionalidad") or ""
            ),
            "ars": str(data.get("canonical_ars") or data.get("ars") or ""),
            "legacy_source_instance_id": str(
                data.get("legacy_source_instance_id") or ""
            ),
            "legacy_patient_id": data.get("legacy_patient_id"),
            "server_revision": int(data.get("server_revision") or 1),
            "is_deleted": bool(data.get("is_deleted")),
            "deleted_at": str(data.get("deleted_at") or ""),
        }

    @staticmethod
    def _event_uuid(patient_id: str, revision: int) -> str:
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hospital-admission-patient-directory:{patient_id}:{revision}",
            )
        )

    @staticmethod
    def _insert_event(con: Any, row: Mapping[str, Any], operation: str) -> None:
        payload = CentralPatientDirectoryRepository._payload(row)
        con.execute(
            """INSERT INTO admission_patient_directory_events(
                   event_uuid,global_patient_id,operation,server_revision,payload_json
               ) VALUES(%s::UUID,%s::UUID,%s,%s,%s::jsonb)
               ON CONFLICT(event_uuid) DO NOTHING""",
            (
                CentralPatientDirectoryRepository._event_uuid(
                    payload["global_patient_id"], payload["server_revision"]
                ),
                payload["global_patient_id"],
                operation,
                payload["server_revision"],
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
            ),
        )

    def _find_by_document(
        self, con: Any, *, cedula: str = "", nss: str = ""
    ) -> list[dict[str, Any]]:
        normalized_cedula = normalize_patient_document(cedula)
        normalized_nss = normalize_patient_document(nss)
        if not normalized_cedula and not normalized_nss:
            return []
        columns = (
            "global_patient_id::TEXT,patient_name,cedula,cedula_normalized,"
            "nss,nss_normalized,phone,address,nationality,canonical_ars,"
            "legacy_source_instance_id,legacy_patient_id,server_revision,"
            "is_deleted,deleted_at,updated_at"
        )
        if normalized_cedula:
            rows = con.execute(
                f"""SELECT {columns} FROM admission_patient_directory
                     WHERE is_deleted=FALSE AND cedula_normalized=%s
                     ORDER BY updated_at DESC,global_patient_id LIMIT 2""",
                (normalized_cedula,),
            ).fetchall()
        else:
            rows = con.execute(
                f"""SELECT {columns} FROM admission_patient_directory
                     WHERE is_deleted=FALSE AND nss_normalized=%s
                     ORDER BY updated_at DESC,global_patient_id LIMIT 2""",
                (normalized_nss,),
            ).fetchall()
        return [_mapping(row) for row in rows]

    def find_by_cedula(
        self, cedula: str, *, timeout_ms: int | None = None
    ) -> dict[str, Any] | None:
        normalized = normalize_patient_document(cedula)
        if not normalized:
            return None
        with self.connection_factory() as con:
            if timeout_ms is not None:
                con.execute(
                    "SELECT set_config('statement_timeout',%s,TRUE)",
                    (f"{max(100, int(timeout_ms))}ms",),
                )
            rows = self._find_by_document(con, cedula=normalized)
        return self._payload(rows[0]) if len(rows) == 1 else None

    def find_by_nss(
        self, nss: str, *, timeout_ms: int | None = None
    ) -> dict[str, Any] | None:
        normalized = normalize_patient_document(nss)
        if not normalized:
            return None
        with self.connection_factory() as con:
            if timeout_ms is not None:
                con.execute(
                    "SELECT set_config('statement_timeout',%s,TRUE)",
                    (f"{max(100, int(timeout_ms))}ms",),
                )
            rows = self._find_by_document(con, nss=normalized)
        return self._payload(rows[0]) if len(rows) == 1 else None

    def upsert_patient(
        self,
        patient: Mapping[str, Any],
        *,
        source_instance_id: str,
        legacy_patient_id: int,
    ) -> str:
        """Aplica un registro legacy sin sobrescribir una revisión central más nueva."""
        source = str(source_instance_id or "").strip()
        legacy_id = int(legacy_patient_id)
        if not source or legacy_id <= 0:
            raise ValueError("El seed requiere identidad legacy de paciente.")
        data = dict(patient or {})
        local_uuid = _uuid_or_empty(data.get("global_patient_id"))
        stable_uuid = local_uuid or str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hospital-admission-patient:{source}:{legacy_id}",
            )
        )
        local_revision = max(1, int(data.get("version") or 1))
        name = str(data.get("nombre") or "SIN NOMBRE").strip() or "SIN NOMBRE"
        cedula = str(data.get("cedula") or "").strip()
        nss = str(data.get("nss") or "").strip()
        payload = {
            "global_patient_id": stable_uuid,
            "patient_name": name,
            "cedula": cedula,
            "cedula_normalized": normalize_patient_document(cedula),
            "nss": nss,
            "nss_normalized": normalize_patient_document(nss),
            "phone": str(data.get("telefono") or "").strip(),
            "address": str(data.get("direccion") or "").strip(),
            "nationality": str(data.get("nacionalidad") or "").strip(),
            "canonical_ars": str(data.get("ars") or "").strip(),
            "legacy_source_instance_id": source,
            "legacy_patient_id": legacy_id,
            "server_revision": local_revision,
            "is_deleted": bool(data.get("is_deleted")),
        }
        with self.connection_factory() as con:
            mapping = con.execute(
                """SELECT global_uuid FROM legacy_entity_uuid_map
                     WHERE entity_type=%s AND source_instance_id=%s AND legacy_id=%s""",
                (PATIENT_ENTITY_TYPE, source, legacy_id),
            ).fetchone()
            if mapping:
                payload["global_patient_id"] = str(mapping[0])
            else:
                central_matches = self._find_by_document(
                    con, cedula=payload["cedula"], nss=payload["nss"]
                )
                if len(central_matches) > 1:
                    con.execute(
                        """INSERT INTO admission_patient_seed_conflicts(
                               source_instance_id,legacy_patient_id,reason_code,details_json
                           ) VALUES(%s,%s,'AMBIGUOUS_DOCUMENT_MATCH',%s::jsonb)
                           ON CONFLICT(source_instance_id,legacy_patient_id,reason_code)
                           DO NOTHING""",
                        (
                            source,
                            legacy_id,
                            json.dumps(
                                {
                                    "cedula": payload["cedula_normalized"],
                                    "nss": payload["nss_normalized"],
                                }
                            ),
                        ),
                    )
                    return "conflict"
                if len(central_matches) == 1:
                    payload["global_patient_id"] = str(
                        central_matches[0]["global_patient_id"]
                    )
                con.execute(
                    """INSERT INTO legacy_entity_uuid_map(
                           entity_type,source_instance_id,legacy_id,global_uuid
                       ) VALUES(%s,%s,%s,%s::UUID)
                       ON CONFLICT(entity_type,source_instance_id,legacy_id) DO NOTHING""",
                    (
                        PATIENT_ENTITY_TYPE,
                        source,
                        legacy_id,
                        payload["global_patient_id"],
                    ),
                )
            existing = con.execute(
                """SELECT * FROM admission_patient_directory
                     WHERE global_patient_id=%s::UUID FOR UPDATE""",
                (payload["global_patient_id"],),
            ).fetchone()
            if existing is not None:
                current = _mapping(existing)
                current_revision = int(current.get("server_revision") or 1)
                if bool(current.get("is_deleted")):
                    return "already_present"
                if current_revision > local_revision:
                    return "already_present"
                if current_revision == local_revision and self._payload(
                    current
                ) == self._payload(payload):
                    return "already_present"
                next_revision = current_revision + 1
                payload["server_revision"] = next_revision
                row = con.execute(
                    """UPDATE admission_patient_directory SET
                           patient_name=%s,cedula=%s,cedula_normalized=%s,nss=%s,
                           nss_normalized=%s,phone=%s,address=%s,nationality=%s,
                           canonical_ars=%s,legacy_source_instance_id=%s,
                           legacy_patient_id=%s,server_revision=%s,is_deleted=%s,
                           deleted_at=NULL,updated_at=NOW()
                       WHERE global_patient_id=%s::UUID RETURNING *""",
                    (
                        payload["patient_name"],
                        payload["cedula"],
                        payload["cedula_normalized"],
                        payload["nss"],
                        payload["nss_normalized"],
                        payload["phone"],
                        payload["address"],
                        payload["nationality"],
                        payload["canonical_ars"],
                        source,
                        legacy_id,
                        next_revision,
                        payload["is_deleted"],
                        payload["global_patient_id"],
                    ),
                ).fetchone()
                self._insert_event(con, _mapping(row), "PATIENT_UPDATED")
                return "updated"
            row = con.execute(
                """INSERT INTO admission_patient_directory(
                       global_patient_id,patient_name,cedula,cedula_normalized,nss,nss_normalized,
                       phone,address,nationality,canonical_ars,legacy_source_instance_id,
                       legacy_patient_id,server_revision,is_deleted
                   ) VALUES(%s::UUID,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING *""",
                (
                    payload["global_patient_id"],
                    payload["patient_name"],
                    payload["cedula"],
                    payload["cedula_normalized"],
                    payload["nss"],
                    payload["nss_normalized"],
                    payload["phone"],
                    payload["address"],
                    payload["nationality"],
                    payload["canonical_ars"],
                    source,
                    legacy_id,
                    local_revision,
                    payload["is_deleted"],
                ),
            ).fetchone()
            self._insert_event(con, _mapping(row), "PATIENT_CREATED")
            return "inserted"

    def events_after(self, sequence: int, *, limit: int = 500) -> list[dict[str, Any]]:
        with self.connection_factory() as con:
            rows = con.execute(
                """SELECT sequence,event_uuid::TEXT,global_patient_id::TEXT,operation,
                          server_revision,payload_json,created_at
                     FROM admission_patient_directory_events
                    WHERE sequence>%s ORDER BY sequence LIMIT %s""",
                (max(0, int(sequence)), max(1, min(int(limit), 500))),
            ).fetchall()
        return [_mapping(row) for row in rows]

    def event_window(self) -> dict[str, int]:
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT f.minimum_available_sequence,f.checkpoint_sequence,
                          COALESCE((SELECT MAX(e.sequence)
                                      FROM admission_patient_directory_events e),0)
                              AS latest_sequence
                     FROM admission_replication_event_floors f
                    WHERE f.stream_name='PATIENT_DIRECTORY'
                    """
            ).fetchone()
        data = _mapping(row)
        return {
            "minimum_available_sequence": int(
                data.get("minimum_available_sequence") or 0
            ),
            "checkpoint_sequence": int(data.get("checkpoint_sequence") or 0),
            "latest_sequence": int(data.get("latest_sequence") or 0),
        }

    def snapshot_page(
        self, *, after_global_patient_id: str = "", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Pages the complete current directory, including tombstones."""
        with self.connection_factory() as con:
            rows = con.execute(
                """SELECT * FROM admission_patient_directory
                    WHERE global_patient_id::TEXT>%s
                    ORDER BY global_patient_id::TEXT LIMIT %s""",
                (
                    str(after_global_patient_id or ""),
                    max(1, min(int(limit), 500)),
                ),
            ).fetchall()
        return [self._payload(_mapping(row)) for row in rows]

    def directory_count(self) -> int:
        with self.connection_factory() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM admission_patient_directory"
            ).fetchone()
        return int(row[0] or 0) if row else 0


def upsert_patient_from_attention_connection(
    con: Any,
    *,
    global_patient_id: str,
    source_instance_id: str,
    legacy_patient_id: int | None,
    patient_name: str,
    cedula: str,
    nss: str,
    phone: str,
    address: str,
    nationality: str,
    ars: str,
    server_revision: int,
) -> None:
    """Mantiene el directorio al materializar un evento de atención cloud."""
    patient_uuid = _uuid_or_empty(global_patient_id)
    if not patient_uuid:
        return
    revision = max(1, int(server_revision or 1))
    source = str(source_instance_id or "LEGACY").strip() or "LEGACY"
    legacy_id = int(legacy_patient_id or 0) or None
    existing = con.execute(
        "SELECT * FROM admission_patient_directory WHERE global_patient_id=%s::UUID FOR UPDATE",
        (patient_uuid,),
    ).fetchone()
    if existing and int(_mapping(existing).get("server_revision") or 0) > revision:
        return
    fields = {
        "global_patient_id": patient_uuid,
        "patient_name": str(patient_name or "SIN NOMBRE").strip() or "SIN NOMBRE",
        "cedula": str(cedula or "").strip(),
        "cedula_normalized": normalize_patient_document(cedula),
        "nss": str(nss or "").strip(),
        "nss_normalized": normalize_patient_document(nss),
        "phone": str(phone or "").strip(),
        "address": str(address or "").strip(),
        "nationality": str(nationality or "").strip(),
        "canonical_ars": str(ars or "").strip(),
        "legacy_source_instance_id": source,
        "legacy_patient_id": legacy_id,
        "server_revision": revision,
        "is_deleted": False,
    }
    if existing:
        row = con.execute(
            """UPDATE admission_patient_directory SET
                   patient_name=%s,cedula=%s,cedula_normalized=%s,nss=%s,nss_normalized=%s,
                   phone=%s,address=%s,nationality=%s,canonical_ars=%s,
                   legacy_source_instance_id=COALESCE(%s,legacy_source_instance_id),
                   legacy_patient_id=COALESCE(%s,legacy_patient_id),
                   server_revision=GREATEST(server_revision,%s),updated_at=NOW()
               WHERE global_patient_id=%s::UUID RETURNING *""",
            (
                fields["patient_name"],
                fields["cedula"],
                fields["cedula_normalized"],
                fields["nss"],
                fields["nss_normalized"],
                fields["phone"],
                fields["address"],
                fields["nationality"],
                fields["canonical_ars"],
                legacy_id and source,
                legacy_id,
                revision,
                patient_uuid,
            ),
        ).fetchone()
        operation = "PATIENT_UPDATED"
    else:
        row = con.execute(
            """INSERT INTO admission_patient_directory(
                   global_patient_id,patient_name,cedula,cedula_normalized,nss,nss_normalized,
                   phone,address,nationality,canonical_ars,legacy_source_instance_id,
                   legacy_patient_id,server_revision,is_deleted
               ) VALUES(%s::UUID,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)
               RETURNING *""",
            (
                patient_uuid,
                fields["patient_name"],
                fields["cedula"],
                fields["cedula_normalized"],
                fields["nss"],
                fields["nss_normalized"],
                fields["phone"],
                fields["address"],
                fields["nationality"],
                fields["canonical_ars"],
                source,
                legacy_id,
                revision,
            ),
        ).fetchone()
        operation = "PATIENT_CREATED"
    if legacy_id is not None:
        con.execute(
            """INSERT INTO legacy_entity_uuid_map(
                   entity_type,source_instance_id,legacy_id,global_uuid
               ) VALUES(%s,%s,%s,%s::UUID)
               ON CONFLICT(entity_type,source_instance_id,legacy_id) DO NOTHING""",
            (PATIENT_ENTITY_TYPE, source, legacy_id, patient_uuid),
        )
    CentralPatientDirectoryRepository._insert_event(con, _mapping(row), operation)


class LocalPatientDirectory:
    """Replica SQLite del directorio central, sin usar IDs locales como identidad."""

    def __init__(self, database: str | Path):
        self.database = str(database)
        self._initialized = False
        self._initialize_lock = threading.Lock()

    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}

    def _is_prepared_by_admission_bootstrap(self) -> bool:
        """Avoid schema DDL in the patient lookup/save hot path when V15 prepared it."""
        try:
            with closing(
                sqlite3.connect(f"file:{Path(self.database).resolve()}?mode=ro", uri=True)
            ) as con:
                row = con.execute(
                    "SELECT valor FROM app_metadata "
                    "WHERE clave='admission.patient_directory_schema_version'"
                ).fetchone()
        except sqlite3.Error:
            return False
        return bool(row and str(row[0]) == str(PATIENT_DIRECTORY_SCHEMA_VERSION))

    def initialize(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            if self._is_prepared_by_admission_bootstrap():
                self._initialized = True
                return
            prepare_sqlite_database(self.database)
            coordinator = get_sqlite_write_coordinator(self.database)
            with (
                coordinator.write("patient-directory-schema"),
                connect_local_sqlite(
                    self.database, operation="patient-directory-schema"
                ) as con,
            ):
                columns = self._columns(con, "pacientes")
                for name, definition in (
                    ("global_patient_id", "TEXT"),
                    ("server_revision", "INTEGER NOT NULL DEFAULT 0"),
                    ("sync_state", "TEXT NOT NULL DEFAULT 'SYNCED'"),
                    ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
                ):
                    if name not in columns:
                        con.execute(f"ALTER TABLE pacientes ADD COLUMN {name} {definition}")
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pacientes_global_patient_id "
                    "ON pacientes(global_patient_id)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pacientes_cedula_clean "
                    "ON pacientes(cedula_clean)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pacientes_nss_clean "
                    "ON pacientes(nss_clean)"
                )
                con.execute(
                    """CREATE TABLE IF NOT EXISTS patient_directory_state(
                           state_key TEXT PRIMARY KEY,
                           state_value TEXT NOT NULL,
                           updated_at TEXT NOT NULL
                       )"""
                )
            self._initialized = True

    def find_local(self, *, cedula: str = "", nss: str = "") -> dict[str, Any] | None:
        self.initialize()
        cedula_key = normalize_patient_document(cedula)
        nss_key = normalize_patient_document(nss)
        if not cedula_key and not nss_key:
            return None
        with closing(
            sqlite3.connect(f"file:{Path(self.database).resolve()}?mode=ro", uri=True)
        ) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                """SELECT p.* FROM pacientes p
                     WHERE COALESCE(p.is_deleted,0)=0
                       AND ((?<>'' AND p.cedula_clean=?) OR (?<>'' AND p.nss_clean=?))
                     ORDER BY COALESCE(p.updated_at,p.created_at) DESC,p.id DESC LIMIT 2""",
                (cedula_key, cedula_key, nss_key, nss_key),
            ).fetchall()
        return dict(rows[0]) if len(rows) == 1 else None

    @staticmethod
    def _hydrate_on_connection(
        con: sqlite3.Connection, patient: Mapping[str, Any]
    ) -> dict[str, Any]:
        data = dict(patient or {})
        global_id = _uuid_or_empty(data.get("global_patient_id"))
        if not global_id:
            raise ValueError("El paciente remoto no posee global_patient_id.")
        cedula = str(data.get("cedula") or "")
        nss = str(data.get("nss") or "")
        revision = max(1, int(data.get("server_revision") or 1))
        existing = con.execute(
            """SELECT * FROM pacientes WHERE REPLACE(LOWER(global_patient_id),'-','')=
                       REPLACE(LOWER(?),'-','') LIMIT 1""",
            (global_id,),
        ).fetchone()
        if existing and int(existing["server_revision"] or 0) > revision:
            return dict(existing)
        fields = {
            "nombre": str(
                data.get("nombre") or data.get("patient_name") or "SIN NOMBRE"
            ),
            "cedula": cedula,
            "cedula_clean": normalize_patient_document(cedula),
            "nss": nss,
            "nss_clean": normalize_patient_document(nss),
            "telefono": str(data.get("telefono") or data.get("phone") or ""),
            "direccion": str(data.get("direccion") or data.get("address") or ""),
            "nacionalidad": str(
                data.get("nacionalidad") or data.get("nationality") or ""
            ),
            "ars": str(data.get("ars") or data.get("canonical_ars") or ""),
            "global_patient_id": global_id,
            "server_revision": revision,
            "sync_state": "SYNCED",
            "is_deleted": int(bool(data.get("is_deleted"))),
            "updated_at": _timestamp(),
        }
        if existing:
            con.execute(
                "UPDATE pacientes SET "
                + ",".join(f"{key}=?" for key in fields)
                + " WHERE id=?",
                tuple(fields.values()) + (int(existing["id"]),),
            )
            patient_id = int(existing["id"])
        else:
            con.execute(
                "INSERT INTO pacientes("
                + ",".join(fields)
                + ") VALUES("
                + ",".join("?" for _ in fields)
                + ")",
                tuple(fields.values()),
            )
            patient_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        for kind, value in (
            ("CEDULA", fields["cedula_clean"]),
            ("NSS", fields["nss_clean"]),
        ):
            if value:
                con.execute(
                    """INSERT OR IGNORE INTO paciente_identificadores(
                           paciente_id,tipo,valor_normalizado,activo,conflicto
                       ) VALUES(?,?,?,1,0)""",
                    (patient_id, kind, value),
                )
        row = con.execute(
            "SELECT * FROM pacientes WHERE id=?", (patient_id,)
        ).fetchone()
        return dict(row) if row else {}

    def hydrate_many(
        self,
        patients: list[Mapping[str, Any]],
        *,
        final_sequence: int | None = None,
    ) -> int:
        """Aplica un batch cloud y su cursor en una sola transacción SQLite corta."""
        if not patients and final_sequence is None:
            return 0
        self.initialize()
        with connect_local_sqlite(
            self.database, operation="patient-directory-hydrate-batch"
        ) as con:
            con.row_factory = sqlite3.Row
            for patient in patients:
                self._hydrate_on_connection(con, patient)
            if final_sequence is not None:
                current = con.execute(
                    """SELECT state_value FROM patient_directory_state
                        WHERE state_key='cloud_cursor'"""
                ).fetchone()
                current_sequence = int(current[0] or 0) if current else 0
                con.execute(
                    """INSERT INTO patient_directory_state(state_key,state_value,updated_at)
                       VALUES('cloud_cursor',?,?)
                       ON CONFLICT(state_key) DO UPDATE SET
                         state_value=excluded.state_value,updated_at=excluded.updated_at""",
                    (str(max(current_sequence, int(final_sequence))), _timestamp()),
                )
        return len(patients)

    def hydrate(self, patient: Mapping[str, Any]) -> dict[str, Any]:
        self.hydrate_many([patient])
        global_id = _uuid_or_empty(dict(patient or {}).get("global_patient_id"))
        with closing(
            sqlite3.connect(f"file:{Path(self.database).resolve()}?mode=ro", uri=True)
        ) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM pacientes WHERE LOWER(global_patient_id)=LOWER(?) LIMIT 1",
                (global_id,),
            ).fetchone()
        return dict(row) if row else {}

    def patient_cursor(self) -> int:
        self.initialize()
        with closing(
            sqlite3.connect(f"file:{Path(self.database).resolve()}?mode=ro", uri=True)
        ) as con:
            row = con.execute(
                "SELECT state_value FROM patient_directory_state WHERE state_key='cloud_cursor'"
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def set_patient_cursor(self, sequence: int) -> None:
        self.initialize()
        with connect_local_sqlite(
            self.database, operation="patient-directory-cursor"
        ) as con:
            current = con.execute(
                """SELECT state_value FROM patient_directory_state
                    WHERE state_key='cloud_cursor'"""
            ).fetchone()
            current_sequence = int(current[0] or 0) if current else 0
            con.execute(
                """INSERT INTO patient_directory_state(state_key,state_value,updated_at)
                   VALUES('cloud_cursor',?,?)
                   ON CONFLICT(state_key) DO UPDATE SET
                     state_value=excluded.state_value,updated_at=excluded.updated_at""",
                (str(max(current_sequence, int(sequence))), _timestamp()),
            )


class PatientDirectoryService:
    """Busca localmente y hace read-through central sólo ante una ausencia."""

    def __init__(
        self,
        database: str | Path,
        connection_factory: Callable[[], Any],
        *,
        is_online: Callable[[], bool] | None = None,
    ):
        self.local = LocalPatientDirectory(database)
        self.central = CentralPatientDirectoryRepository(connection_factory)
        self.is_online = is_online or (lambda: True)

    def find_by_cedula(self, cedula: str) -> dict[str, Any] | None:
        local = self.local.find_local(cedula=cedula)
        if local is not None or not self.is_online():
            return local
        central = self.central.find_by_cedula(cedula)
        return self.local.hydrate(central) if central else None

    def find_by_nss(self, nss: str) -> dict[str, Any] | None:
        local = self.local.find_local(nss=nss)
        if local is not None or not self.is_online():
            return local
        central = self.central.find_by_nss(nss)
        return self.local.hydrate(central) if central else None

    def verify_with_cloud(
        self, *, cedula: str = "", nss: str = "", timeout_ms: int = 1500
    ) -> dict[str, Any] | None:
        """Consulta central exacta y actualiza la réplica; nunca es el fast path UI."""
        if not self.is_online():
            return None
        central = (
            self.central.find_by_cedula(cedula, timeout_ms=timeout_ms)
            if cedula
            else self.central.find_by_nss(nss, timeout_ms=timeout_ms)
        )
        if not central:
            return None
        self.local.hydrate(central)
        return dict(central)

    def search_patient(self, identifier: str) -> dict[str, Any] | None:
        normalized = normalize_patient_document(identifier)
        if not normalized:
            return None
        return self.find_by_cedula(normalized) or self.find_by_nss(normalized)

    def pull_incremental(self, *, limit: int = 500) -> int:
        cursor = self.local.patient_cursor()
        window_loader = getattr(self.central, "event_window", None)
        window: dict[str, Any] = {}
        if callable(window_loader):
            window = dict(window_loader() or {})
            floor = int(window.get("minimum_available_sequence") or 0)
            if cursor < floor:
                self.bootstrap_from_projection(batch_size=limit, window=window)
                cursor = self.local.patient_cursor()
            if int(window.get("latest_sequence") or 0) <= cursor:
                return 0
        events = self.central.events_after(cursor, limit=limit)
        if not events:
            return 0
        payloads = []
        for event in events:
            payload = event.get("payload_json") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            payloads.append(dict(payload))
        final_sequence = max(int(event.get("sequence") or cursor) for event in events)
        return self.local.hydrate_many(payloads, final_sequence=final_sequence)

    def bootstrap_from_projection(
        self,
        *,
        batch_size: int = 500,
        window: Mapping[str, Any] | None = None,
    ) -> int:
        """Rebuilds a new/stale directory replica at an atomic event checkpoint."""
        page_loader = getattr(self.central, "snapshot_page", None)
        window_loader = getattr(self.central, "event_window", None)
        if not callable(page_loader) or not callable(window_loader):
            raise RuntimeError(  # noqa: TRY004 - service capability is unavailable
                "El snapshot central de pacientes no está disponible."
            )
        event_window = dict(window or window_loader() or {})
        checkpoint = int(
            event_window.get("checkpoint_sequence")
            or event_window.get("latest_sequence")
            or 0
        )
        safe_batch_size = max(1, min(int(batch_size), 500))
        after_id = ""
        total = 0
        while True:
            rows = list(
                page_loader(
                    after_global_patient_id=after_id,
                    limit=safe_batch_size,
                )
                or []
            )
            if not rows:
                break
            total += self.local.hydrate_many(rows)
            after_id = max(str(row.get("global_patient_id") or "") for row in rows)
            if not after_id:
                raise RuntimeError(
                    "El snapshot central contiene un paciente sin identidad."
                )
            if len(rows) < safe_batch_size:
                break
        self.local.set_patient_cursor(checkpoint)
        return total

    def bootstrap_full_directory(self, *, batch_size: int = 500) -> int:
        total = 0
        while True:
            pulled = self.pull_incremental(limit=batch_size)
            total += pulled
            if pulled < max(1, min(int(batch_size), 500)):
                return total

    def seed_admission_database_to_cloud(self) -> PatientSeedResult:
        """Seed estructurado, idempotente y procesado en batches acotados."""
        from patient_seed_tool import seed_admission_database_to_cloud

        result = seed_admission_database_to_cloud(
            self.local.database,
            self.central.connection_factory,
        )
        return PatientSeedResult(
            source_path=str(result["source_path"]),
            source_instance_id=str(result["source_instance_id"]),
            local_patients=int(result["local_patients"]),
            local_attentions=int(result["local_attentions"]),
            inserted=int(result["inserted"]),
            updated=0,
            already_present=int(result["already_present"]),
            conflicts=int(result["conflicts"]),
        )


__all__ = [
    "PATIENT_DIRECTORY_SCHEMA_VERSION",
    "POSTGRES_PATIENT_DIRECTORY_SCHEMA",
    "CentralPatientDirectoryRepository",
    "LocalPatientDirectory",
    "PatientDirectoryService",
    "PatientSeedResult",
    "normalize_patient_document",
    "upsert_patient_from_attention_connection",
]
