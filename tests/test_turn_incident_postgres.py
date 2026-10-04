"""Real transactions for September 26; the server is disposable and loopback-only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event
from uuid import uuid4

from psycopg2.extras import DictCursor
import pytest

from admission_hybrid import OperationalSessionService
from billing_close_store import SCHEMA, load_close_snapshot
from sigeh_product import (
    ensure_sigeh_production_schema,
    prepare_sigeh_production_bootstrap,
)
from tests import test_billing_consistency_postgres as pg

server = pg.server
SESSION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
ADMIN = {"id": 1, "username": "operator", "role": "administrador"}


class Connection(pg.Connection):
    def execute(self, sql, params=()):
        cursor = self.raw.cursor(cursor_factory=DictCursor)
        cursor.execute(sql, params)
        return cursor


@pytest.fixture
def database(server):
    with Connection(server) as con:
        con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        con.execute(pg.SCHEMA)
        con.execute("""
            ALTER TABLE admission_operational_sessions
                ADD COLUMN primary_login_session_id TEXT DEFAULT 'login',
                ADD COLUMN turn_code TEXT DEFAULT '8AM_8AM',
                ADD COLUMN turn_started_at TIMESTAMPTZ DEFAULT NOW()-INTERVAL '1 day',
                ADD COLUMN turn_ends_at TIMESTAMPTZ DEFAULT NOW(),
                ADD COLUMN primary_last_seen TIMESTAMPTZ DEFAULT NOW(),
                ADD COLUMN operational_revision BIGINT DEFAULT 1,
                ADD COLUMN lease_generation BIGINT DEFAULT 1,
                ADD COLUMN changed_by TEXT, ADD COLUMN change_reason TEXT;
            ALTER TABLE admission_operational_turn_intervals
                ADD COLUMN nominal_ends_at TIMESTAMPTZ,
                ADD COLUMN active_user_id TEXT, ADD COLUMN active_username TEXT,
                ADD PRIMARY KEY(operational_session_id,generation);
            CREATE TABLE admission_operational_devices(
                operational_session_id TEXT,device_id TEXT,login_session_id TEXT,
                station_role TEXT,detached_at TIMESTAMPTZ,last_seen TIMESTAMPTZ,
                invalidated_at TIMESTAMPTZ,invalidated_reason TEXT,
                invalidated_generation INT,new_active_username TEXT,
                PRIMARY KEY(operational_session_id,device_id));
            ALTER TABLE admission_operational_audit ADD COLUMN transition_id UUID,
                ADD COLUMN id BIGSERIAL,ADD COLUMN created_at TIMESTAMPTZ DEFAULT NOW(),
                ADD COLUMN username TEXT,ADD COLUMN generation INT,ADD COLUMN device_id TEXT;
            CREATE UNIQUE INDEX uq_admission_operational_transition
                ON admission_operational_audit(transition_id) WHERE transition_id IS NOT NULL;
            CREATE TABLE admission_sync_events(turn_id BIGINT);
            ALTER TABLE recibos ADD PRIMARY KEY(id);
            ALTER TABLE recibos ADD COLUMN ars TEXT DEFAULT 'FUTURO',
                ADD COLUMN total NUMERIC(14,2) DEFAULT 0,
                ADD COLUMN fecha TEXT DEFAULT '',ADD COLUMN autorizacion_at TEXT DEFAULT '',
                ADD COLUMN numero_autorizacion TEXT DEFAULT '';
        """)
        con.execute(
            """INSERT INTO admission_operational_sessions
            (operational_session_id,operational_source_id,turn_id,production_epoch_id)
            VALUES(%s,%s,111,%s)""",
            (SESSION, pg.SOURCE, pg.EPOCH),
        )
        con.execute("""INSERT INTO admission_operational_turn_intervals
            (operational_session_id,generation,turn_id,started_at,nominal_ends_at,
             active_user_id,active_username,production_epoch_id)
            SELECT operational_session_id,generation,turn_id,turn_started_at,turn_ends_at,
                   active_user_id,active_username,production_epoch_id
            FROM admission_operational_sessions""")
        con.execute(
            "INSERT INTO admission_operational_devices VALUES(%s,'A','login','PRIMARY',NULL,NOW(),NULL,NULL,NULL,NULL)",
            (SESSION,),
        )
        con.execute(SCHEMA)
        con.execute(
            "UPDATE billing_reporting_policy SET enabled_at=NOW()-INTERVAL '2 days'"
        )
    return lambda: Connection(server)


def correction(service, **overrides):
    values = dict(
        actor_user=ADMIN,
        operational_session_id=SESSION,
        primary_device_id="A",
        new_turn_id=None,
        allocate_central_turn_id=True,
        expected_generation=1,
        expected_operational_revision=1,
        expected_previous_turn_id=111,
        new_turn_code="8AM_8PM",
        administrative_override=True,
        transition_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    )
    return service.admin_set_admission_turn(**(values | overrides))


def test_correct_schedule_then_handoff_closes_entire_cohort_once(database):
    service = OperationalSessionService(database)
    with database() as con:
        initial = dict(
            con.execute("SELECT * FROM admission_operational_sessions").fetchone()
        )
        for number in range(1, 112):
            con.execute(
                """INSERT INTO admission_attention_projection
                (attention_id,global_attention_id,operational_source_id,turn_id,created_at_effective_utc)
                VALUES(%s,%s,%s,111,%s)""",
                (number, str(uuid4()), pg.SOURCE, initial["turn_started_at"]),
            )
    result = correction(service)
    assert result.new_turn_id == 111
    assert result.new_generation == 1
    assert (
        datetime.fromisoformat(result.operational_session.turn_started_at)
        == initial["turn_started_at"]
    )
    assert correction(service).idempotent_replay
    with database() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) FROM admission_operational_turn_intervals"
            ).fetchone()[0]
            == 1
        )
        assert (
            con.execute("SELECT COUNT(*) FROM billing_close_snapshots").fetchone()[0]
            == 0
        )
        for number in (112, 113):
            con.execute(
                """INSERT INTO admission_attention_projection
                (attention_id,global_attention_id,operational_source_id,turn_id,created_at_effective_utc)
                VALUES(%s,%s,%s,111,NOW())""",
                (number, str(uuid4()), pg.SOURCE),
            )
        con.execute("""INSERT INTO recibos(id,created_at,admission_global_attention_id,total)
            SELECT attention_id,created_at_effective_utc::TEXT,global_attention_id,1.10
            FROM admission_attention_projection WHERE attention_id<=30""")
    request = dict(
        operational_session_id=SESSION,
        primary_device_id="A",
        new_login_session_id="next-login",
        new_user={"id": 2, "username": "next", "role": "administrador"},
        new_turn_id=None,
        allocate_central_turn_id=True,
        expected_generation=1,
        expected_operational_revision=2,
        expected_previous_turn_id=111,
        expected_current_representative_id="1",
        transition_id=str(uuid4()),
        invalidate_secondaries=False,
    )
    handoff = service.transition_primary_user(**request)
    assert handoff.old_turn_id == 111
    assert service.transition_primary_user(**request).idempotent_replay
    with database() as con:
        snapshot = load_close_snapshot(con, pg.SOURCE, 111)
        assert snapshot["current_admissions"] == 113
        assert snapshot["receipt_count"] == 30
        assert snapshot["pending_current"] == 83
        assert snapshot["amount"] == "33.00"
        assert (
            con.execute("SELECT COUNT(*) FROM billing_close_snapshots").fetchone()[0]
            == 1
        )


def test_bootstrap_on_configured_installation_avoids_busy_operational_tables(database):
    with database() as con:
        con.execute("DROP TABLE sigeh_product_state")
        ensure_sigeh_production_schema(con)
        con.execute(
            """INSERT INTO sigeh_product_state
            VALUES(1,'SIGEH','SIGEH_PRODUCTION_BOOTSTRAP_V1',%s,'PRODUCTION_ACTIVE',NOW(),NOW(),NOW())""",
            (pg.EPOCH,),
        )
    with database() as busy:
        busy.execute("LOCK TABLE admission_operational_sessions IN ROW EXCLUSIVE MODE")
        with database() as reader:
            reader.execute("SET LOCAL lock_timeout='250ms'")
            result = prepare_sigeh_production_bootstrap(lambda: NonClosing(reader))
            assert not result.applied


def test_failed_schedule_correction_rolls_back_revision_and_audit(database):
    from admission_hybrid import AdmissionWriteBlocked

    with database() as con:
        con.execute("DELETE FROM admission_operational_turn_intervals")
    with pytest.raises(AdmissionWriteBlocked, match="intervalo abierto"):
        correction(OperationalSessionService(database))
    with database() as con:
        state = con.execute(
            "SELECT turn_id,turn_code,operational_revision FROM admission_operational_sessions"
        ).fetchone()
        assert tuple(state) == (111, "8AM_8AM", 1)
        assert (
            con.execute("SELECT COUNT(*) FROM admission_operational_audit").fetchone()[
                0
            ]
            == 0
        )


class NonClosing:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, *_):
        return False


def test_heartbeat_and_schedule_correction_can_overlap_without_deadlock(database):
    waiting = Event()

    class Signalled(Connection):
        def execute(self, sql, params=()):
            if "UPDATE admission_operational_" in sql:
                waiting.set()
            return super().execute(sql, params)

    # Hold the session row while heartbeat starts, then touch its device,
    # matching the actual operational command lock order.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with database() as writer:
            writer.execute("SET LOCAL lock_timeout='2s'")
            writer.execute("SELECT * FROM admission_operational_sessions FOR UPDATE")
            heartbeat_service = OperationalSessionService(
                lambda: Signalled(writer.raw.dsn)
            )
            future = pool.submit(
                heartbeat_service.heartbeat,
                operational_session_id=SESSION,
                device_id="A",
            )
            assert waiting.wait(5)
            writer.execute("SELECT * FROM admission_operational_devices FOR UPDATE")
        future.result(timeout=5)
