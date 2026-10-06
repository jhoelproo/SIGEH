"""Validate cloud maintenance exclusively against disposable local PostgreSQL."""

from pathlib import Path

import psycopg2
import pytest

from tests.test_billing_consistency_postgres import Connection, server as server

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/20261006052126_reduce_duplicate_event_indexes.sql"
)
SQL = MIGRATION.read_text(encoding="utf-8")
DUPLICATES = [
    "idx_admission_sync_events_cursor",
    "idx_admission_sync_events_event_uuid",
    "idx_admission_patient_directory_events_cursor",
]


@pytest.fixture
def connection(server):
    with Connection(server) as con:
        con.execute("""CREATE TABLE admission_sync_events(
            sequence BIGSERIAL PRIMARY KEY,event_uuid UUID NOT NULL UNIQUE);
            CREATE INDEX idx_admission_sync_events_cursor ON admission_sync_events(sequence);
            CREATE INDEX idx_admission_sync_events_event_uuid ON admission_sync_events(event_uuid);
            CREATE TABLE admission_patient_directory_events(sequence BIGSERIAL PRIMARY KEY);
            CREATE INDEX idx_admission_patient_directory_events_cursor ON admission_patient_directory_events(sequence);
            CREATE TABLE admission_patient_directory(id INT PRIMARY KEY);
            CREATE TABLE recibos(id INT PRIMARY KEY);
            CREATE TABLE recibo_document_versions(id INT PRIMARY KEY);
            INSERT INTO admission_sync_events(event_uuid)
            VALUES('11111111-1111-4111-8111-111111111111');
            INSERT INTO admission_patient_directory_events DEFAULT VALUES;
            INSERT INTO admission_patient_directory VALUES(1);
            INSERT INTO recibos VALUES(1);
            INSERT INTO recibo_document_versions VALUES(1);""")
        try:
            yield con
        finally:
            con.raw.rollback()


def index_exists(con, name):
    return con.execute(
        "SELECT to_regclass(%s) IS NOT NULL AS exists", (name,)
    ).fetchone()["exists"]


def test_cleanup_preserves_rows_uniqueness_and_is_idempotent(connection):
    before = connection.execute(
        "SELECT count(*) AS count FROM admission_sync_events"
    ).fetchone()
    connection.execute(SQL, None)
    connection.execute(SQL, None)
    assert (
        connection.execute(
            "SELECT count(*) AS count FROM admission_sync_events"
        ).fetchone()
        == before
    )
    for name in DUPLICATES:
        assert not index_exists(connection, name)
    for name in (
        "admission_sync_events_pkey",
        "admission_sync_events_event_uuid_key",
        "admission_patient_directory_events_pkey",
    ):
        assert index_exists(connection, name)
    connection.execute("SAVEPOINT duplicate_insert")
    with pytest.raises(psycopg2.errors.UniqueViolation):
        connection.execute(
            "INSERT INTO admission_sync_events(event_uuid) VALUES('11111111-1111-4111-8111-111111111111')"
        )
    connection.execute("ROLLBACK TO SAVEPOINT duplicate_insert")
    options = connection.execute(
        "SELECT reloptions FROM pg_class WHERE oid='public.recibos'::regclass"
    ).fetchone()["reloptions"]
    assert "autovacuum_vacuum_scale_factor=0.05" in options
    assert "autovacuum_analyze_scale_factor=0.02" in options


@pytest.mark.parametrize(
    "variant", ["different-key", "partial", "constraint", "missing-keeper"]
)
def test_guard_aborts_without_partial_changes_when_index_is_not_equivalent(
    connection, variant
):
    connection.execute("DROP INDEX idx_admission_sync_events_event_uuid")
    if variant == "different-key":
        connection.execute(
            "CREATE INDEX idx_admission_sync_events_event_uuid ON admission_sync_events(sequence)"
        )
    elif variant == "partial":
        connection.execute(
            "CREATE INDEX idx_admission_sync_events_event_uuid ON admission_sync_events(event_uuid) WHERE sequence>0"
        )
    elif variant == "constraint":
        connection.execute(
            "ALTER TABLE admission_sync_events ADD CONSTRAINT idx_admission_sync_events_event_uuid UNIQUE(event_uuid)"
        )
    else:
        connection.execute(
            "ALTER TABLE admission_sync_events DROP CONSTRAINT admission_sync_events_event_uuid_key"
        )
        connection.execute(
            "CREATE INDEX idx_admission_sync_events_event_uuid ON admission_sync_events(event_uuid)"
        )
    connection.execute("SAVEPOINT maintenance")
    with pytest.raises(psycopg2.errors.RaiseException, match="not a redundant"):
        connection.execute(SQL, None)
    connection.execute("ROLLBACK TO SAVEPOINT maintenance")
    for name in DUPLICATES:
        assert index_exists(connection, name)
