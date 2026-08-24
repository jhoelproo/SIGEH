"""Idempotent V15 patient seed into the central patient directory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from patient_directory import (
    CentralPatientDirectoryRepository,
    normalize_patient_document,
)

SEED_SCHEMA = """
CREATE TABLE IF NOT EXISTS admission_patient_seed_registry(
  central_seed_id UUID PRIMARY KEY,
  source_instance_id TEXT NOT NULL UNIQUE,
  seed_source TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  local_patient_count INTEGER NOT NULL,
  local_attention_count INTEGER NOT NULL,
  inserted_count INTEGER NOT NULL,
  already_present_count INTEGER NOT NULL,
  conflict_count INTEGER NOT NULL,
  seed_completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    }


def _first_row_anchor(
    connection: sqlite3.Connection,
    table: str,
    preferred_columns: tuple[str, ...],
) -> tuple[Any, ...]:
    columns = _table_columns(connection, table)
    selected = [column for column in preferred_columns if column in columns]
    if not selected:
        return ()
    quoted = ",".join(f'"{column}"' for column in selected)
    row = connection.execute(
        f'SELECT {quoted} FROM "{table}" ORDER BY "{selected[0]}" LIMIT 1'
    ).fetchone()
    return tuple(row) if row else ()


def source_identity(connection: sqlite3.Connection) -> str:
    """Return the persistent identity stored by V15 or a growth-stable legacy ID."""
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "app_metadata" in tables:
        columns = _table_columns(connection, "app_metadata")
        key_column = "clave" if "clave" in columns else "key"
        value_column = "valor" if "valor" in columns else "value"
        if key_column in columns and value_column in columns:
            row = connection.execute(
                f'SELECT "{value_column}" FROM app_metadata WHERE "{key_column}"=?',
                ("integration.source_instance_id",),
            ).fetchone()
            if row and str(row[0] or "").strip():
                return str(row[0]).strip()

    schema = [
        (str(row[0]), str(row[1] or ""))
        for row in connection.execute(
            "SELECT name,sql FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    anchors = {
        "patients": _first_row_anchor(
            connection,
            "pacientes",
            ("id", "created_at", "nombre", "cedula", "nss"),
        )
        if "pacientes" in tables
        else (),
        "attentions": _first_row_anchor(
            connection,
            "atenciones",
            ("id", "created_at", "fecha", "hora", "paciente_id"),
        )
        if "atenciones" in tables
        else (),
        "turns": _first_row_anchor(
            connection,
            "turnos",
            ("id", "created_at", "fecha_inicio", "representante"),
        )
        if "turnos" in tables
        else (),
    }
    material = json.dumps(
        {"schema": schema, "anchors": anchors},
        ensure_ascii=False,
        default=str,
        sort_keys=True,
    )
    return "V15-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:28]


_source_identity = source_identity


def _stable_patient_uuid(source_id: str, legacy_id: int) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hospital-admission-patient:{source_id}:{int(legacy_id)}",
        )
    )


def _patient_payload(row: Mapping[str, Any], source_id: str) -> dict[str, Any]:
    legacy_id = int(row["id"])
    cedula = str(row.get("cedula") or "").strip()
    nss = str(row.get("nss") or "").strip()
    return {
        "global_patient_id": str(row.get("global_patient_id") or "").strip()
        or _stable_patient_uuid(source_id, legacy_id),
        "patient_name": str(row.get("nombre") or "SIN NOMBRE").strip() or "SIN NOMBRE",
        "cedula": cedula,
        "cedula_normalized": normalize_patient_document(cedula),
        "nss": nss,
        "nss_normalized": normalize_patient_document(nss),
        "phone": str(row.get("telefono") or "").strip(),
        "address": str(row.get("direccion") or "").strip(),
        "nationality": str(row.get("nacionalidad") or "").strip(),
        "canonical_ars": str(row.get("ars") or "").strip(),
        "legacy_source_instance_id": source_id,
        "legacy_patient_id": legacy_id,
        "server_revision": 1,
        "is_deleted": False,
        "deleted_at": "",
    }


def _event_uuid(global_patient_id: str) -> str:
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hospital-admission-patient-directory:{global_patient_id}:1",
        )
    )


def _execute_values(connection: Any, query: str, rows: list[tuple[Any, ...]]) -> None:
    if not rows:
        return
    from psycopg2.extras import execute_values

    raw_connection = getattr(connection, "con", connection)
    with raw_connection.cursor() as cursor:
        execute_values(cursor, query, rows, page_size=500)


def _document_index(existing_rows: list[Any]) -> dict[tuple[str, str], set[str]]:
    index: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in existing_rows:
        if bool(row[3]):
            continue
        global_id = str(row[0])
        for kind, value in (("CEDULA", row[1]), ("NSS", row[2])):
            key = str(value or "")
            if key:
                index[(kind, key)].add(global_id)
    return index


def _matching_patient_ids(
    payload: Mapping[str, Any],
    document_index: Mapping[tuple[str, str], set[str]],
) -> set[str]:
    matches: set[str] = set()
    for kind, field in (("CEDULA", "cedula_normalized"), ("NSS", "nss_normalized")):
        value = str(payload.get(field) or "")
        if value:
            matches.update(document_index.get((kind, value), set()))
    return matches


def _prepare_patient_batch(
    local_rows: list[Mapping[str, Any]],
    source_id: str,
    mapped: dict[int, str],
    existing_ids: set[str],
    document_index: dict[tuple[str, str], set[str]],
) -> tuple[list[dict[str, Any]], list[tuple[Any, ...]], int, int, int]:
    payloads: list[dict[str, Any]] = []
    conflict_rows: list[tuple[Any, ...]] = []
    inserted = already = conflicts = 0
    for local_row in local_rows:
        payload = _patient_payload(local_row, source_id)
        legacy_id = int(payload["legacy_patient_id"])
        mapped_id = mapped.get(legacy_id)
        matches = _matching_patient_ids(payload, document_index)
        if mapped_id:
            payload["global_patient_id"] = mapped_id
        elif len(matches) == 1:
            payload["global_patient_id"] = next(iter(matches))
        elif len(matches) > 1:
            conflicts += 1
            conflict_rows.append(
                (
                    source_id,
                    legacy_id,
                    "AMBIGUOUS_DOCUMENT_MATCH",
                    json.dumps(
                        {
                            "cedula": payload["cedula_normalized"],
                            "nss": payload["nss_normalized"],
                        },
                        sort_keys=True,
                    ),
                )
            )
        global_id = str(payload["global_patient_id"])
        mapped[legacy_id] = global_id
        if global_id in existing_ids:
            already += 1
        else:
            inserted += 1
            existing_ids.add(global_id)
            payloads.append(payload)
        for kind, field in (("CEDULA", "cedula_normalized"), ("NSS", "nss_normalized")):
            value = str(payload.get(field) or "")
            if value:
                document_index[(kind, value)].add(global_id)
    return payloads, conflict_rows, inserted, already, conflicts


def _write_patient_batch(
    connection: Any,
    *,
    source_id: str,
    local_rows: list[Mapping[str, Any]],
    mapped: Mapping[int, str],
    payloads: list[Mapping[str, Any]],
    conflict_rows: list[tuple[Any, ...]],
) -> None:
    _execute_values(
        connection,
        "INSERT INTO legacy_entity_uuid_map(entity_type,source_instance_id,legacy_id,global_uuid) "
        "VALUES %s ON CONFLICT(entity_type,source_instance_id,legacy_id) DO NOTHING",
        [
            ("patient", source_id, int(row["id"]), mapped[int(row["id"])])
            for row in local_rows
        ],
    )
    _execute_values(
        connection,
        "INSERT INTO admission_patient_directory("
        "global_patient_id,patient_name,cedula,cedula_normalized,nss,nss_normalized,"
        "phone,address,nationality,canonical_ars,legacy_source_instance_id,"
        "legacy_patient_id,server_revision,is_deleted) VALUES %s "
        "ON CONFLICT(global_patient_id) DO NOTHING",
        [
            (
                item["global_patient_id"],
                item["patient_name"],
                item["cedula"],
                item["cedula_normalized"],
                item["nss"],
                item["nss_normalized"],
                item["phone"],
                item["address"],
                item["nationality"],
                item["canonical_ars"],
                source_id,
                item["legacy_patient_id"],
                1,
                False,
            )
            for item in payloads
        ],
    )
    _execute_values(
        connection,
        "INSERT INTO admission_patient_directory_events("
        "event_uuid,global_patient_id,operation,server_revision,payload_json) "
        "VALUES %s ON CONFLICT(event_uuid) DO NOTHING",
        [
            (
                _event_uuid(str(item["global_patient_id"])),
                item["global_patient_id"],
                "PATIENT_CREATED",
                1,
                json.dumps(item, ensure_ascii=False, sort_keys=True),
            )
            for item in payloads
        ],
    )
    _execute_values(
        connection,
        "INSERT INTO admission_patient_seed_conflicts("
        "source_instance_id,legacy_patient_id,reason_code,details_json) "
        "VALUES %s ON CONFLICT(source_instance_id,legacy_patient_id,reason_code) DO NOTHING",
        conflict_rows,
    )


def seed_admission_database_to_cloud(
    sqlite_path: str | Path,
    connection_factory: Callable[[], Any],
    *,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Import structured patient rows in bounded, idempotent batches."""
    source = Path(sqlite_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    repository = CentralPatientDirectoryRepository(connection_factory)
    repository.ensure_schema()
    with connection_factory() as cloud:
        cloud.execute(SEED_SCHEMA)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as local:
        local.row_factory = sqlite3.Row
        source_id = source_identity(local)
        local_patients = [
            dict(row) for row in local.execute("SELECT * FROM pacientes ORDER BY id")
        ]
        local_attention_count = int(
            local.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0]
        )
    seed_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hospital-admission-central-patient-seed:{source_id}:v1",
        )
    )
    with connection_factory() as cloud:
        existing_rows = cloud.execute(
            "SELECT global_patient_id::TEXT,cedula_normalized,nss_normalized,is_deleted "
            "FROM admission_patient_directory"
        ).fetchall()
        mapping_rows = cloud.execute(
            "SELECT legacy_id,global_uuid::TEXT FROM legacy_entity_uuid_map "
            "WHERE entity_type='patient' AND source_instance_id=%s",
            (source_id,),
        ).fetchall()
    mapped = {int(row[0]): str(row[1]) for row in mapping_rows}
    existing_ids = {str(row[0]) for row in existing_rows}
    document_index = _document_index(existing_rows)
    inserted = already = conflicts = 0
    processed = 0
    batch_size = 500
    for offset in range(0, len(local_patients), batch_size):
        local_batch = local_patients[offset : offset + batch_size]
        payloads, conflict_rows, new_count, existing_count, conflict_count = (
            _prepare_patient_batch(
                local_batch,
                source_id,
                mapped,
                existing_ids,
                document_index,
            )
        )
        inserted += new_count
        already += existing_count
        conflicts += conflict_count
        with connection_factory() as cloud:
            _write_patient_batch(
                cloud,
                source_id=source_id,
                local_rows=local_batch,
                mapped=mapped,
                payloads=payloads,
                conflict_rows=conflict_rows,
            )
        processed += len(local_batch)
        if progress:
            progress(processed, len(local_patients))
    with connection_factory() as cloud:
        cloud.execute(
            "INSERT INTO admission_patient_seed_registry("
            "central_seed_id,source_instance_id,seed_source,schema_version,local_patient_count,"
            "local_attention_count,inserted_count,already_present_count,conflict_count) "
            "VALUES(%s::UUID,%s,%s,1,%s,%s,%s,%s,%s) "
            "ON CONFLICT(source_instance_id) DO UPDATE SET "
            "seed_source=EXCLUDED.seed_source,"
            "local_patient_count=EXCLUDED.local_patient_count,"
            "local_attention_count=EXCLUDED.local_attention_count,"
            "inserted_count=admission_patient_seed_registry.inserted_count+EXCLUDED.inserted_count,"
            "already_present_count=EXCLUDED.already_present_count,"
            "conflict_count=EXCLUDED.conflict_count,seed_completed_at=NOW()",
            (
                seed_id,
                source_id,
                str(source),
                len(local_patients),
                local_attention_count,
                inserted,
                already,
                conflicts,
            ),
        )
    return {
        "central_seed_id": seed_id,
        "source_instance_id": source_id,
        "source_path": str(source),
        "local_patients": len(local_patients),
        "local_attentions": local_attention_count,
        "inserted": inserted,
        "already_present": already,
        "conflicts": conflicts,
        "already_completed": inserted == 0,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }


__all__ = ["seed_admission_database_to_cloud", "source_identity"]
