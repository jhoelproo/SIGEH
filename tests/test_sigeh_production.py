from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager

import pytest

from sigeh_product import (
    PRODUCTION_BOOTSTRAP_VERSION,
    mark_production_session_started,
    prepare_sigeh_production_bootstrap,
    reset_local_operational_pointers,
)


class Cursor:
    def __init__(self, rows=(), rowcount=0):
        self._rows = list(rows)
        self.rowcount = rowcount

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class ProductConnection:
    def __init__(self, *, existing_state=None, production_count=0):
        self.state = existing_state
        self.production_count = production_count
        self.active_ids = ["TEST-SESSION"]
        self.executed = []

    def execute(self, query, params=None):
        normalized = " ".join(query.split())
        self.executed.append((normalized, params))
        if normalized.startswith("SELECT * FROM sigeh_product_state"):
            return Cursor([self.state] if self.state else [])
        if "SELECT COUNT(*) FROM admission_operational_sessions" in normalized:
            return Cursor([(self.production_count,)])
        if normalized.startswith("SELECT operational_session_id"):
            return Cursor([(value,) for value in self.active_ids])
        if normalized.startswith("UPDATE admission_operational_turn_intervals"):
            return Cursor(rowcount=len(self.active_ids))
        if normalized.startswith("UPDATE admission_operational_devices"):
            return Cursor(rowcount=len(self.active_ids))
        if normalized.startswith("UPDATE admission_operational_sessions"):
            return Cursor(rowcount=1)
        if normalized.startswith("INSERT INTO sigeh_product_state"):
            self.state = {
                "production_epoch_id": params[2],
                "bootstrap_version": params[1],
                "production_initialized_at": None,
            }
            return Cursor([self.state], rowcount=1)
        if normalized.startswith("SELECT production_epoch_id::TEXT"):
            return Cursor([(self.state["production_epoch_id"],)] if self.state else [])
        return Cursor()


@contextmanager
def factory(connection):
    yield connection


def test_bootstrap_closes_authority_once_and_is_idempotent():
    connection = ProductConnection()
    first = prepare_sigeh_production_bootstrap(lambda: factory(connection))
    assert first.applied is True
    assert first.closed_sessions == 1
    assert first.released_devices == 1
    assert first.closed_intervals == 1
    assert first.bootstrap_version == PRODUCTION_BOOTSTRAP_VERSION

    second = prepare_sigeh_production_bootstrap(lambda: factory(connection))
    assert second.applied is False
    assert second.production_epoch_id == first.production_epoch_id


def test_bootstrap_refuses_unmarked_production_state():
    connection = ProductConnection(production_count=1)
    with pytest.raises(RuntimeError, match="estado productivo sin marcador"):
        prepare_sigeh_production_bootstrap(lambda: factory(connection))


def test_bootstrap_without_test_sessions_still_creates_epoch():
    connection = ProductConnection()
    connection.active_ids = []
    result = prepare_sigeh_production_bootstrap(lambda: factory(connection))
    assert result.applied is True
    assert result.closed_sessions == 0
    assert result.released_devices == 0
    assert result.closed_intervals == 0


def test_mark_production_session_uses_current_epoch():
    connection = ProductConnection(
        existing_state={
            "production_epoch_id": "EPOCH-1",
            "bootstrap_version": PRODUCTION_BOOTSTRAP_VERSION,
            "production_initialized_at": None,
        }
    )
    assert mark_production_session_started(connection, "REAL-SESSION") == "EPOCH-1"
    assert any(
        query.startswith("UPDATE admission_operational_sessions")
        and params == ("EPOCH-1", "REAL-SESSION")
        for query, params in connection.executed
    )


def test_mark_production_session_requires_prepared_epoch():
    with pytest.raises(RuntimeError, match="frontera productiva"):
        mark_production_session_started(ProductConnection(), "SESSION")


def _create_replica(path):
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE app_metadata(clave TEXT PRIMARY KEY,valor TEXT NOT NULL);
        CREATE TABLE admission_operational_cache(singleton INTEGER PRIMARY KEY);
        INSERT INTO admission_operational_cache VALUES(1);
        CREATE TABLE sync_runtime_context(singleton INTEGER PRIMARY KEY);
        INSERT INTO sync_runtime_context VALUES(1);
        CREATE TABLE turnos(id INTEGER PRIMARY KEY,estado TEXT);
        INSERT INTO turnos VALUES(7,'ABIERTO');
        CREATE TABLE atenciones(id INTEGER PRIMARY KEY,nombre TEXT);
        INSERT INTO atenciones VALUES(1,'HISTORIA CONSERVADA');
        CREATE TABLE sync_outbox(
          event_uuid TEXT PRIMARY KEY,sync_status TEXT,last_error TEXT
        );
        INSERT INTO sync_outbox VALUES('EVENT-TEST','PENDING',NULL);
        """
    )
    con.commit()
    con.close()


def test_local_reset_clears_only_operational_pointers(tmp_path):
    database = tmp_path / "pacientes.db"
    _create_replica(database)

    counts = reset_local_operational_pointers(tmp_path, "EPOCH-REAL")
    assert counts == {"closed_turns": 1, "superseded_events": 1}
    con = sqlite3.connect(database)
    assert con.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1
    assert con.execute("SELECT estado FROM turnos").fetchone()[0] == "CERRADO"
    assert (
        con.execute("SELECT COUNT(*) FROM admission_operational_cache").fetchone()[0]
        == 0
    )
    assert con.execute("SELECT COUNT(*) FROM sync_runtime_context").fetchone()[0] == 0
    assert (
        con.execute("SELECT sync_status FROM sync_outbox").fetchone()[0] == "SUPERSEDED"
    )
    assert (
        con.execute(
            "SELECT valor FROM app_metadata WHERE clave='sigeh.production_bootstrap_epoch'"
        ).fetchone()[0]
        == "EPOCH-REAL"
    )
    con.close()
    assert json.loads((tmp_path / "turnos_config.json").read_text("utf-8")) == {}
    assert json.loads((tmp_path / "representantes.json").read_text("utf-8")) == []
    assert (
        json.loads((tmp_path / "resumen_turno.json").read_text("utf-8"))["total"] == 0
    )

    second = reset_local_operational_pointers(tmp_path, "EPOCH-REAL")
    assert second == {"closed_turns": 0, "superseded_events": 0}


def test_local_reset_without_existing_database_still_resets_json(tmp_path):
    assert reset_local_operational_pointers(tmp_path, "EPOCH-EMPTY") == {
        "closed_turns": 0,
        "superseded_events": 0,
    }
    assert (
        json.loads((tmp_path / "resumen_turno.json").read_text("utf-8"))[
            "production_epoch_id"
        ]
        == "EPOCH-EMPTY"
    )


def test_local_reset_handles_minimal_replica_schema(tmp_path):
    database = tmp_path / "pacientes.db"
    con = sqlite3.connect(database)
    con.execute("CREATE TABLE only_history(id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO only_history VALUES(1)")
    con.commit()
    con.close()
    reset_local_operational_pointers(tmp_path, "EPOCH-MIN")
    con = sqlite3.connect(database)
    assert con.execute("SELECT COUNT(*) FROM only_history").fetchone()[0] == 1
    con.close()
