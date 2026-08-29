"""Identidad de producto y bootstrap operacional único de SIGEH."""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PRODUCT_NAME = "SIGEH"
PRODUCT_ID = "SIGEH"
APP_VERSION = "1.0.7"
GITHUB_OWNER = "jhoelproo"
GITHUB_REPOSITORY = f"{GITHUB_OWNER}/SIGEH"
PRODUCTION_BOOTSTRAP_VERSION = "SIGEH_PRODUCTION_BOOTSTRAP_V1"


@dataclass(frozen=True)
class ProductionBootstrapResult:
    production_epoch_id: str
    bootstrap_version: str
    applied: bool
    closed_sessions: int
    released_devices: int
    closed_intervals: int
    production_initialized_at: str | None


def ensure_sigeh_production_schema(connection: Any) -> None:
    """Converge instalaciones viejas/nuevas sin borrar historia."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS sigeh_product_state(
               singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
               product_id TEXT NOT NULL,
               bootstrap_version TEXT NOT NULL,
               production_epoch_id UUID NOT NULL UNIQUE,
               bootstrap_status TEXT NOT NULL,
               bootstrap_completed_at TIMESTAMPTZ NOT NULL,
               production_initialized_at TIMESTAMPTZ,
               updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
               CHECK(product_id='SIGEH'),
               CHECK(bootstrap_status IN ('COMPLETED','PRODUCTION_ACTIVE'))
           )"""
    )
    connection.execute(
        """ALTER TABLE admission_operational_sessions
             ADD COLUMN IF NOT EXISTS production_epoch_id UUID"""
    )
    connection.execute(
        """ALTER TABLE admission_operational_turn_intervals
             ADD COLUMN IF NOT EXISTS production_epoch_id UUID"""
    )
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_admission_operational_production_active
             ON admission_operational_sessions(production_epoch_id,updated_at DESC)
             WHERE status='ACTIVE'"""
    )
    connection.execute(
        """CREATE OR REPLACE FUNCTION sigeh_bind_active_operational_epoch()
           RETURNS TRIGGER AS $$
           DECLARE current_epoch UUID;
           BEGIN
             IF NEW.status='ACTIVE' AND NEW.production_epoch_id IS NULL THEN
               SELECT production_epoch_id INTO current_epoch
                 FROM sigeh_product_state WHERE singleton=1;
               IF current_epoch IS NULL THEN
                 RAISE EXCEPTION 'SIGEH production bootstrap is not prepared';
               END IF;
               NEW.production_epoch_id := current_epoch;
             END IF;
             IF NEW.status='ACTIVE' AND NEW.production_epoch_id IS NOT NULL THEN
               UPDATE sigeh_product_state
                  SET bootstrap_status='PRODUCTION_ACTIVE',
                      production_initialized_at=COALESCE(
                          production_initialized_at,NOW()
                      ),
                      updated_at=NOW()
                WHERE singleton=1
                  AND production_epoch_id=NEW.production_epoch_id;
             END IF;
             RETURN NEW;
           END;
           $$ LANGUAGE plpgsql"""
    )
    connection.execute(
        "DROP TRIGGER IF EXISTS trg_sigeh_bind_active_epoch "
        "ON admission_operational_sessions"
    )
    connection.execute(
        """CREATE TRIGGER trg_sigeh_bind_active_epoch
           BEFORE INSERT OR UPDATE OF status
           ON admission_operational_sessions
           FOR EACH ROW EXECUTE FUNCTION sigeh_bind_active_operational_epoch()"""
    )


def _row_mapping(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _bootstrap_result(
    row: Any, *, applied: bool, counts=(0, 0, 0)
) -> ProductionBootstrapResult:
    state = _row_mapping(row)
    return ProductionBootstrapResult(
        production_epoch_id=str(state.get("production_epoch_id") or ""),
        bootstrap_version=str(state.get("bootstrap_version") or ""),
        applied=bool(applied),
        closed_sessions=int(counts[0]),
        released_devices=int(counts[1]),
        closed_intervals=int(counts[2]),
        production_initialized_at=(
            str(state.get("production_initialized_at"))
            if state.get("production_initialized_at") is not None
            else None
        ),
    )


def prepare_sigeh_production_bootstrap(
    connection_factory: Callable[[], Any],
) -> ProductionBootstrapResult:
    """Aplica exactamente una vez el corte TEST -> SIGEH.

    La operación conserva sesiones, intervalos y atenciones como historia; solo
    elimina su autoridad operacional activa. El advisory lock hace seguro que
    dos estaciones arranquen simultáneamente.
    """
    with connection_factory() as connection:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (PRODUCTION_BOOTSTRAP_VERSION,),
        )
        ensure_sigeh_production_schema(connection)
        existing = connection.execute(
            "SELECT * FROM sigeh_product_state WHERE singleton=1 FOR UPDATE"
        ).fetchone()
        if existing:
            return _bootstrap_result(existing, applied=False)

        already_production = connection.execute(
            """SELECT COUNT(*) FROM admission_operational_sessions
                 WHERE production_epoch_id IS NOT NULL"""
        ).fetchone()
        if already_production and int(already_production[0] or 0) > 0:
            raise RuntimeError(
                "Se detectó estado productivo sin marcador SIGEH; el reset fue bloqueado."
            )

        active_ids = [
            str(row[0])
            for row in connection.execute(
                """SELECT operational_session_id
                     FROM admission_operational_sessions
                    WHERE status='ACTIVE' FOR UPDATE"""
            ).fetchall()
        ]
        closed_intervals = 0
        released_devices = 0
        if active_ids:
            cursor = connection.execute(
                """UPDATE admission_operational_turn_intervals
                      SET ended_at=COALESCE(ended_at,NOW())
                    WHERE operational_session_id::TEXT=ANY(%s)
                      AND ended_at IS NULL""",
                (active_ids,),
            )
            closed_intervals = max(0, int(cursor.rowcount or 0))
            cursor = connection.execute(
                """UPDATE admission_operational_devices
                      SET detached_at=COALESCE(detached_at,NOW()),
                          invalidated_at=COALESCE(invalidated_at,NOW()),
                          invalidated_reason=COALESCE(
                              NULLIF(invalidated_reason,''),
                              'SIGEH_PRODUCTION_BOOTSTRAP'
                          ),
                          station_role='SECONDARY'
                    WHERE operational_session_id::TEXT=ANY(%s)
                      AND detached_at IS NULL""",
                (active_ids,),
            )
            released_devices = max(0, int(cursor.rowcount or 0))
            connection.execute(
                """UPDATE admission_operational_sessions
                      SET status='CLOSED',
                          primary_device_id='',primary_login_session_id='',
                          primary_last_seen=NOW(),updated_at=NOW(),
                          change_reason='SIGEH_PRODUCTION_BOOTSTRAP',
                          changed_by='SIGEH_BOOTSTRAP'
                    WHERE operational_session_id::TEXT=ANY(%s)
                      AND status='ACTIVE'""",
                (active_ids,),
            )

        epoch_id = str(uuid.uuid4())
        state = connection.execute(
            """INSERT INTO sigeh_product_state(
                   singleton,product_id,bootstrap_version,production_epoch_id,
                   bootstrap_status,bootstrap_completed_at,updated_at
               ) VALUES(1,%s,%s,%s,'COMPLETED',NOW(),NOW())
               RETURNING *""",
            (PRODUCT_ID, PRODUCTION_BOOTSTRAP_VERSION, epoch_id),
        ).fetchone()
        return _bootstrap_result(
            state,
            applied=True,
            counts=(len(active_ids), released_devices, closed_intervals),
        )


def current_production_epoch(connection: Any) -> str:
    ensure_sigeh_production_schema(connection)
    row = connection.execute(
        """SELECT production_epoch_id::TEXT
             FROM sigeh_product_state
            WHERE singleton=1 AND product_id=%s""",
        (PRODUCT_ID,),
    ).fetchone()
    return str(row[0] or "") if row else ""


def mark_production_session_started(
    connection: Any, operational_session_id: str
) -> str:
    """Vincula el primer contenedor operacional real con la época SIGEH."""
    epoch_id = current_production_epoch(connection)
    if not epoch_id:
        raise RuntimeError("SIGEH no tiene una frontera productiva preparada.")
    connection.execute(
        """UPDATE admission_operational_sessions
              SET production_epoch_id=%s
            WHERE operational_session_id=%s
              AND production_epoch_id IS NULL""",
        (epoch_id, str(operational_session_id)),
    )
    connection.execute(
        """UPDATE sigeh_product_state
              SET bootstrap_status='PRODUCTION_ACTIVE',
                  production_initialized_at=COALESCE(production_initialized_at,NOW()),
                  updated_at=NOW()
            WHERE singleton=1"""
    )
    return epoch_id


def reset_local_operational_pointers(
    data_directory: str | Path, epoch_id: str
) -> dict[str, int]:
    """Neutraliza punteros TEST de una réplica sin borrar pacientes/historia."""
    root = Path(data_directory).resolve()
    root.mkdir(parents=True, exist_ok=True)
    counts = _reset_local_database(root / "pacientes.db", epoch_id)

    _write_json(root / "turnos_config.json", {})
    _write_json(root / "representantes.json", [])
    _write_json(
        root / "resumen_turno.json",
        {
            "actualizado": datetime.now(timezone.utc).isoformat(),
            "production_epoch_id": str(epoch_id),
            "total": 0,
            "sin_seguro": 0,
            "general": 0,
            "pediatria": 0,
            "ginecologia": 0,
            "urgencias": 0,
            "consultas": 0,
        },
    )
    return counts


def _reset_local_database(database_path: Path, epoch_id: str) -> dict[str, int]:
    counts = {"closed_turns": 0, "superseded_events": 0}
    if not database_path.is_file():
        return counts
    connection = sqlite3.connect(database_path, timeout=5.0)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        _close_local_turns(connection, tables, counts)
        _supersede_pending_events(connection, tables, counts)
        for table in ("admission_operational_cache", "sync_runtime_context"):
            if table in tables:
                connection.execute(f"DELETE FROM {table}")
        if "app_metadata" in tables:
            connection.execute(
                """INSERT INTO app_metadata(clave,valor) VALUES(?,?)
                   ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor""",
                ("sigeh.production_bootstrap_epoch", str(epoch_id)),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return counts


def _close_local_turns(
    connection: sqlite3.Connection, tables: set[str], counts: dict[str, int]
) -> None:
    if "turnos" not in tables:
        return
    cursor = connection.execute(
        """UPDATE turnos SET estado='CERRADO'
             WHERE UPPER(COALESCE(estado,''))='ABIERTO'"""
    )
    counts["closed_turns"] = max(0, int(cursor.rowcount or 0))


def _supersede_pending_events(
    connection: sqlite3.Connection, tables: set[str], counts: dict[str, int]
) -> None:
    if "sync_outbox" not in tables:
        return
    cursor = connection.execute(
        """UPDATE sync_outbox
              SET sync_status='SUPERSEDED',
                  last_error='SIGEH_PRODUCTION_BOOTSTRAP'
            WHERE sync_status IN ('PENDING','RETRY')"""
    )
    counts["superseded_events"] = max(0, int(cursor.rowcount or 0))


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
