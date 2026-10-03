"""Exercise delta reads against disposable local PostgreSQL, never production."""

import json
from pathlib import Path
from time import perf_counter

import pytest

from admission_turn_row_cache import TurnRowCache
from tests.test_billing_consistency_postgres import Connection, server as server


SQL = """SELECT p.*,p.attention_id AS id FROM admission_attention_projection p
WHERE p.operational_source_id::TEXT=%s AND p.turn_id=%s AND NOT p.is_deleted
ORDER BY p.attention_id"""


@pytest.fixture
def connection(server):
    with Connection(server) as con:
        con.execute("""CREATE TEMP TABLE admission_attention_projection(
            attention_id BIGINT PRIMARY KEY,operational_source_id TEXT,turn_id BIGINT,
            is_deleted BOOLEAN DEFAULT FALSE,latest_payload_json JSONB,
            version INTEGER DEFAULT 1)""")
        con.execute("""INSERT INTO admission_attention_projection
            (attention_id,operational_source_id,turn_id,latest_payload_json)
            SELECT n,'source',1,jsonb_build_object('detail',repeat('x',20000))
            FROM generate_series(1,100) n""")
        yield con


def test_real_sql_changes_payload_without_revision_and_removes_deleted_rows(connection):
    cache = TurnRowCache()
    assert len(cache.load(connection, "source", 1, SQL)) == 100
    connection.execute("""UPDATE admission_attention_projection SET latest_payload_json=
        jsonb_build_object('specialty','PEDIATRIA') WHERE attention_id=1""")
    rows = cache.load(connection, "source", 1, SQL)
    assert rows[0]["latest_payload_json"] == {"specialty": "PEDIATRIA"}
    connection.execute(
        "UPDATE admission_attention_projection SET is_deleted=TRUE WHERE attention_id=1"
    )
    assert len(cache.load(connection, "source", 1, SQL)) == 99
    assert cache.load(connection, "source", 2, SQL) == []


class MeteredConnection:
    def __init__(self, connection):
        self.connection = connection
        self.bytes = 0
        self.rows = 0

    def execute(self, sql, params):
        cursor = self.connection.execute(sql, params)
        meter = self

        class Cursor:
            def fetchall(self):
                rows = cursor.fetchall()
                meter.rows += len(rows)
                meter.bytes += len(json.dumps(rows, default=str).encode())
                return rows

        return Cursor()


def test_comparative_transfer_and_full_dataset_equivalence(connection):
    baseline = MeteredConnection(connection)
    optimized = MeteredConnection(connection)
    expected = baseline.execute(SQL, ("source", 1)).fetchall()
    baseline_start = perf_counter()
    for _ in range(29):
        assert baseline.execute(SQL, ("source", 1)).fetchall() == expected
    baseline_ms = (perf_counter() - baseline_start) * 1000
    cache = TurnRowCache()
    optimized_start = perf_counter()
    for _ in range(30):
        assert cache.load(optimized, "source", 1, SQL) == expected
    optimized_ms = (perf_counter() - optimized_start) * 1000
    reduction = 1 - optimized.bytes / baseline.bytes
    assert reduction > 0.95
    result = {
        "scenario": "100 synthetic rows, 20KB detail, 30 unchanged refreshes",
        "baseline_bytes": baseline.bytes,
        "optimized_bytes": optimized.bytes,
        "baseline_rows": baseline.rows,
        "optimized_rows": optimized.rows,
        "reduction_percent": reduction * 100,
        "baseline_elapsed_ms": baseline_ms,
        "optimized_elapsed_ms": optimized_ms,
        "wire_bytes": False,
    }
    target = Path("output/egress-turn-cache-comparison.json")
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
