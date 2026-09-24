"""Closure capture against a disposable PostgreSQL, never production."""

import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg2
import pytest

from billing_close_store import SCHEMA, load_close_snapshot
from tests import test_billing_consistency_postgres as pg

server = pg.server


@pytest.fixture
def close_database(server):
    with pg.Connection(server) as con:
        con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        con.execute(pg.SCHEMA)
        con.execute("""
            ALTER TABLE admission_operational_audit ADD COLUMN transition_id UUID UNIQUE;
            INSERT INTO admission_operational_sessions(operational_source_id)
            VALUES('11111111-1111-4111-8111-111111111111');
            ALTER TABLE recibos ADD COLUMN ars TEXT DEFAULT 'FUTURO',
                ADD COLUMN total NUMERIC(14,2) DEFAULT 0,
                ADD COLUMN fecha TEXT DEFAULT '',
                ADD COLUMN autorizacion_at TEXT DEFAULT '',
                ADD COLUMN numero_autorizacion TEXT DEFAULT '';
            INSERT INTO admission_operational_turn_intervals VALUES
                ('test',1,10,'2026-09-23T08:00:00-04','2026-09-24T08:00:00-04',NULL),
                ('test',2,11,'2026-09-24T08:00:00-04','2026-09-25T08:00:00-04',NULL);
        """)
        con.execute(SCHEMA)
        con.execute(
            "UPDATE billing_reporting_policy SET enabled_at='2026-09-23T00:00:00-04'"
        )
    return lambda: pg.Connection(server)


def commit_handoff(con, transition):
    details = {
        "status": "COMMITTED",
        "request": {
            "operational_source_id": pg.SOURCE,
            "transition_type": "PRIMARY_USER_HANDOFF",
        },
        "result": {"old_turn_id": 11, "new_turn_id": 12},
    }
    con.execute(
        """INSERT INTO admission_operational_audit
        (operational_session_id,event_type,details_json,transition_id)
        VALUES('test','TURN_HANDOFF_TRANSITION',%s::jsonb,%s)
        ON CONFLICT(transition_id) DO UPDATE SET details_json=EXCLUDED.details_json""",
        (json.dumps(details), transition),
    )


def test_capture_canonical_receipts_once_and_preserve_snapshot(close_database):
    transition = str(uuid4())
    with close_database() as con:
        con.execute(
            """INSERT INTO admission_attention_projection
            (attention_id,global_attention_id,operational_source_id,turn_id,created_at_effective_utc)
            VALUES(1,%s,%s,10,'2026-09-23T09:00:00-04')""",
            (pg.GLOBAL, pg.SOURCE),
        )
        con.execute(
            """INSERT INTO recibos(id,created_at,admission_global_attention_id)
            VALUES(1,'2026-09-24T10:00:00-04',%s)""",
            (pg.GLOBAL,),
        )
        con.execute("""INSERT INTO recibos(id,created_at,numero_autorizacion)
            SELECT n,'2026-09-24T11:00:00-04','1234' FROM generate_series(2,50) n""")
        commit_handoff(con, transition)
        commit_handoff(con, transition)
        snapshot = load_close_snapshot(con, pg.SOURCE, 11)
        assert snapshot["receipt_count"] == 50
        assert snapshot["linked_billed"] == 1
        assert snapshot["historical_billed"] == 49
        assert snapshot["authorized"] == 49
        assert snapshot["pending_previous"] == 0
        assert (
            con.execute("SELECT COUNT(*) AS n FROM billing_close_snapshots").fetchone()[
                "n"
            ]
            == 1
        )
    with close_database() as con:
        con.execute("UPDATE recibos SET is_deleted=1")
        assert load_close_snapshot(con, pg.SOURCE, 11) == snapshot
    with pytest.raises(psycopg2.Error, match="BILLING_CLOSE_SNAPSHOT_IMMUTABLE"):
        with close_database() as con:
            con.execute("DELETE FROM billing_close_snapshots")


def test_capture_rolls_back_with_handoff(close_database):
    with pytest.raises(RuntimeError, match="simulated"):
        with close_database() as con:
            commit_handoff(con, str(uuid4()))
            raise RuntimeError("simulated transaction failure")
    with close_database() as con:
        assert load_close_snapshot(con, pg.SOURCE, 11) is None
        assert (
            con.execute(
                "SELECT COUNT(*) AS n FROM admission_operational_audit"
            ).fetchone()["n"]
            == 0
        )


def test_pending_excludes_open_origin_and_prebaseline(close_database):
    with close_database() as con:
        con.execute("""INSERT INTO admission_operational_turn_intervals VALUES
            ('test',3,12,'2026-09-24T09:00:00-04',NULL,NULL)""")
        for identifier, turn, created in (
            (1, 10, "2026-09-23T09:00:00-04"),
            (2, 12, "2026-09-24T10:00:00-04"),
            (3, 10, "2026-09-22T09:00:00-04"),
        ):
            con.execute(
                """INSERT INTO admission_attention_projection
                (attention_id,global_attention_id,operational_source_id,turn_id,created_at_effective_utc)
                VALUES(%s,%s,%s,%s,%s)""",
                (identifier, str(uuid4()), pg.SOURCE, turn, created),
            )
        commit_handoff(con, str(uuid4()))
        snapshot = load_close_snapshot(con, pg.SOURCE, 11)
        assert snapshot["pending_previous"] == 1
        assert snapshot["pending_historical"] == 0
        assert snapshot["total_pending"] == 1


def test_missing_interval_fails_instead_of_zero_report(close_database):
    with close_database() as con:
        con.execute("DELETE FROM admission_operational_turn_intervals")
    with pytest.raises(psycopg2.Error, match="BILLING_CLOSE_INTERVAL_MISSING"):
        with close_database() as con:
            commit_handoff(con, str(uuid4()))


def test_ten_concurrent_retries_keep_one_snapshot(close_database):
    transition = str(uuid4())

    def retry(_index):
        with close_database() as con:
            commit_handoff(con, transition)

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(retry, range(10)))
    with close_database() as con:
        assert (
            con.execute("SELECT COUNT(*) AS n FROM billing_close_snapshots").fetchone()[
                "n"
            ]
            == 1
        )
        assert (
            con.execute(
                "SELECT COUNT(*) AS n FROM admission_operational_audit"
            ).fetchone()["n"]
            == 1
        )


def test_another_transition_cannot_replace_same_turn(close_database):
    with close_database() as con:
        commit_handoff(con, str(uuid4()))
    with pytest.raises(psycopg2.Error, match="BILLING_CLOSE_TRANSITION_COLLISION"):
        with close_database() as con:
            commit_handoff(con, str(uuid4()))


def test_reinstall_does_not_reset_policy_or_capture(close_database):
    with close_database() as con:
        commit_handoff(con, str(uuid4()))
        before = con.execute("SELECT * FROM billing_reporting_policy").fetchone()
        snapshot = load_close_snapshot(con, pg.SOURCE, 11)
        con.execute(SCHEMA)
        assert (
            con.execute("SELECT * FROM billing_reporting_policy").fetchone() == before
        )
        assert load_close_snapshot(con, pg.SOURCE, 11) == snapshot


def test_missing_new_capture_is_error_but_legacy_source_is_allowed(close_database):
    with close_database() as con:
        assert load_close_snapshot(con, "old-device", 10) is None
        con.execute(
            "INSERT INTO admission_operational_sessions(operational_source_id) VALUES(%s)",
            (pg.SOURCE,),
        )
        with pytest.raises(RuntimeError, match="captura central"):
            load_close_snapshot(con, pg.SOURCE, 11, closed_at="2099-01-01T00:00:00-04")
        assert (
            load_close_snapshot(con, pg.SOURCE, 11, closed_at="2000-01-01T00:00:00-04")
            is None
        )
