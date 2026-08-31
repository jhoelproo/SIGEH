"""Collect non-clinical evidence for SIGEH multistation physical gates.

The collector is read-only. It records operational identity and global UUIDs,
never patient names, diagnoses, documents, credentials, or authorization data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

def _mapping(row: sqlite3.Row | None) -> dict[str, Any]:
    return dict(row) if row is not None else {}


def _identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "operational_source_id": str(row.get("operational_source_id") or ""),
        "turn_id": int(row.get("turn_id") or row.get("operational_turn_id") or 0),
        "generation": int(row.get("generation") or 0),
        "operational_revision": int(row.get("operational_revision") or 0),
    }


def _uuid_digest(values: list[str]) -> str:
    canonical = "\n".join(sorted(values)).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def collect_local_snapshot(
    database_path: Path,
    *,
    station: str,
    trace_global_attention_id: str = "",
) -> dict[str, Any]:
    database_path = Path(database_path).resolve()
    if not database_path.is_file():
        raise FileNotFoundError(f"No existe la réplica local: {database_path}")
    with sqlite3.connect(f"file:{database_path.as_posix()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        integrity = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        cache = _mapping(
            connection.execute(
                "SELECT * FROM admission_operational_cache WHERE singleton=1"
            ).fetchone()
        )
        runtime = _mapping(
            connection.execute(
                "SELECT * FROM sync_runtime_context WHERE singleton=1"
            ).fetchone()
        )
        identity = _identity(cache or runtime)
        source_id = identity["operational_source_id"]
        turn_id = identity["turn_id"]
        rows = connection.execute(
            """SELECT global_attention_id
                 FROM atenciones
                WHERE operational_source_id=? AND operational_turn_id=?
                  AND COALESCE(is_deleted,0)=0
                  AND UPPER(TRIM(COALESCE(estado,'ACTIVA')))
                      NOT IN ('ANULADA','ELIMINADA')
                  AND TRIM(COALESCE(global_attention_id,''))<>''
                ORDER BY global_attention_id""",
            (source_id, turn_id),
        ).fetchall()
        global_ids = [str(row[0]) for row in rows]
        pending_count = int(
            connection.execute(
                """SELECT COUNT(*) FROM sync_outbox
                    WHERE operational_source_id=? AND turn_id=?
                      AND sync_status IN ('PENDING','RETRY')""",
                (source_id, turn_id),
            ).fetchone()[0]
        )
        trace: dict[str, Any] = {}
        if trace_global_attention_id:
            local_attention = connection.execute(
                """SELECT global_attention_id,operational_source_id,
                          operational_turn_id,generation,sync_state,
                          origin_device_id,device_local_sequence,is_deleted
                     FROM atenciones WHERE global_attention_id=?""",
                (trace_global_attention_id,),
            ).fetchone()
            outbox = connection.execute(
                """SELECT event_uuid,operation,sync_status,retry_count,
                          operational_source_id,turn_id,generation,device_id
                     FROM sync_outbox
                    WHERE entity_type='attention' AND entity_uuid=?
                    ORDER BY created_at""",
                (trace_global_attention_id,),
            ).fetchall()
            trace = {
                "attention": _mapping(local_attention),
                "outbox": [dict(row) for row in outbox],
            }
    return {
        "station": str(station),
        "database_sha256": hashlib.sha256(database_path.read_bytes()).hexdigest(),
        "sqlite_quick_check": integrity,
        "device_id": str(runtime.get("device_id") or ""),
        "identity": identity,
        "local_count": len(global_ids),
        "pending_count": pending_count,
        "global_attention_ids": global_ids,
        "global_attention_ids_sha256": _uuid_digest(global_ids),
        "trace": trace,
    }


def collect_central_snapshot(
    database_url: str,
    identity: dict[str, Any],
    *,
    trace_global_attention_id: str = "",
) -> dict[str, Any]:
    import psycopg2
    from psycopg2.extras import RealDictCursor

    with psycopg2.connect(database_url, connect_timeout=10) as connection:
        connection.set_session(readonly=True, autocommit=False)
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(
                """SELECT operational_source_id::TEXT,turn_id,generation,
                          operational_revision,status,primary_device_id
                     FROM admission_operational_sessions
                    WHERE status='ACTIVE'
                    ORDER BY updated_at DESC LIMIT 1"""
            )
            active = dict(cursor.fetchone() or {})
            cursor.execute(
                """SELECT global_attention_id::TEXT
                     FROM admission_attention_projection
                    WHERE operational_source_id::TEXT=%s AND turn_id=%s
                      AND COALESCE(is_deleted,FALSE)=FALSE
                      AND UPPER(TRIM(COALESCE(source_status,'ACTIVA')))
                          IN ('ACTIVA','PENDIENTE')
                    ORDER BY global_attention_id::TEXT""",
                (
                    identity["operational_source_id"],
                    identity["turn_id"],
                ),
            )
            global_ids = [str(row["global_attention_id"]) for row in cursor.fetchall()]
            cursor.execute(
                """SELECT COUNT(*) AS active_primary_count
                     FROM admission_operational_devices
                    WHERE station_role='PRIMARY' AND detached_at IS NULL
                      AND invalidated_at IS NULL"""
            )
            primary_count = int(cursor.fetchone()["active_primary_count"])
            trace: dict[str, Any] = {}
            if trace_global_attention_id:
                cursor.execute(
                    """SELECT global_attention_id::TEXT,
                              operational_source_id::TEXT,turn_id,generation,
                              source_status,is_deleted,server_revision
                         FROM admission_attention_projection
                        WHERE global_attention_id::TEXT=%s""",
                    (trace_global_attention_id,),
                )
                projection = dict(cursor.fetchone() or {})
                cursor.execute(
                    """SELECT event_uuid::TEXT,operation,generation,turn_id,
                              operational_source_id::TEXT,device_id,server_revision
                         FROM admission_sync_events
                        WHERE entity_type='attention' AND entity_uuid::TEXT=%s
                        ORDER BY server_revision""",
                    (trace_global_attention_id,),
                )
                trace = {
                    "projection": projection,
                    "events": [dict(row) for row in cursor.fetchall()],
                }
    return {
        "active_operational_state": active,
        "active_primary_count": primary_count,
        "central_count": len(global_ids),
        "global_attention_ids": global_ids,
        "global_attention_ids_sha256": _uuid_digest(global_ids),
        "trace": trace,
    }


def build_evidence(
    database_path: Path,
    *,
    station: str,
    bundle_root: Path,
    trace_global_attention_id: str = "",
) -> dict[str, Any]:
    from database_config import resolve_database_url

    local = collect_local_snapshot(
        database_path,
        station=station,
        trace_global_attention_id=trace_global_attention_id,
    )
    database_url = resolve_database_url(bundle_root)
    if not database_url:
        raise RuntimeError("No se pudo resolver la conexión central protegida.")
    central = collect_central_snapshot(
        database_url,
        local["identity"],
        trace_global_attention_id=trace_global_attention_id,
    )
    return {
        "schema_version": 1,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "local": local,
        "central": central,
        "identity_matches_active": (
            local["identity"]["operational_source_id"]
            == str(central["active_operational_state"].get("operational_source_id") or "")
            and local["identity"]["turn_id"]
            == int(central["active_operational_state"].get("turn_id") or 0)
        ),
        "dataset_matches_central": (
            local["global_attention_ids"] == central["global_attention_ids"]
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-db", required=True, type=Path)
    parser.add_argument("--station", required=True)
    parser.add_argument("--bundle-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--trace-global-attention-id", default="")
    args = parser.parse_args()
    evidence = build_evidence(
        args.local_db,
        station=args.station,
        bundle_root=args.bundle_root,
        trace_global_attention_id=args.trace_global_attention_id.strip(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0 if evidence["identity_matches_active"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
