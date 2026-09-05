"""Real SQL and concurrency tests on a disposable, loopback-only PostgreSQL."""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import socket
import subprocess
import threading

import psycopg2
from psycopg2.extras import RealDictCursor
import pytest

import CALCULOS_QT as app

SOURCE = "11111111-1111-4111-8111-111111111111"
GLOBAL = "22222222-2222-4222-8222-222222222222"
EPOCH = "33333333-3333-4333-8333-333333333333"
USER = {"username": "operator", "role": app.ROLE_ADMIN}

SCHEMA = """
CREATE TABLE sigeh_product_state(singleton INT,production_epoch_id UUID);
CREATE TABLE admission_operational_sessions(
 operational_session_id TEXT DEFAULT 'test', operational_source_id UUID,
 active_user_id TEXT DEFAULT '1',active_username TEXT DEFAULT 'operator',
 active_user_display_name TEXT DEFAULT 'OPERATOR', primary_device_id TEXT DEFAULT 'A',
 turn_id BIGINT,generation INT DEFAULT 1,status TEXT DEFAULT 'ACTIVE',
 updated_at TIMESTAMPTZ DEFAULT NOW(),production_epoch_id UUID);
CREATE TABLE admission_attention_projection(
 source_instance_id TEXT DEFAULT 'ORIGIN',attention_id BIGINT PRIMARY KEY,patient_id BIGINT DEFAULT 1,
 turn_id BIGINT DEFAULT 3949,service_date TEXT DEFAULT '2026-09-04',service_time TEXT DEFAULT '02:10 PM',
 patient_name TEXT DEFAULT 'PACIENTE SINTETICO', coverage_status TEXT DEFAULT 'ASEGURADO_VALIDADO',
 canonical_ars TEXT DEFAULT 'FUTURO',nss_snapshot TEXT DEFAULT '1234567',cedula_snapshot TEXT DEFAULT '00000000001',
 service_type TEXT DEFAULT 'EMERGENCIA',specialty TEXT DEFAULT 'GENERAL',admission_username TEXT DEFAULT 'operator',
 authorization_snapshot TEXT DEFAULT '',source_status TEXT DEFAULT 'ACTIVA',has_detail_sheet BOOLEAN DEFAULT TRUE,
 readiness TEXT DEFAULT 'LISTA',readiness_reasons TEXT DEFAULT '[]',source_updated_at TEXT DEFAULT '',
 snapshot_hash TEXT DEFAULT 'test',contract_version INT DEFAULT 1,synced_at TEXT DEFAULT '',
 global_attention_id UUID UNIQUE,global_patient_id UUID,operational_source_id UUID,version INT DEFAULT 1,
 origin_device_id TEXT DEFAULT 'ORIGIN',device_local_sequence BIGINT DEFAULT 1,operational_session_id TEXT DEFAULT 'test',
 generation INT DEFAULT 1,is_deleted BOOLEAN DEFAULT FALSE,created_at_effective_utc TIMESTAMPTZ DEFAULT NOW());
CREATE TABLE admission_shift_inheritances(source_instance_id TEXT,attention_id BIGINT,turno_origen_id BIGINT,estado TEXT);
CREATE TABLE admission_billing_claims(
 source_instance_id TEXT,attention_id BIGINT,claimed_by TEXT,session_id TEXT,station_id TEXT,
 turno_origen_id BIGINT,turno_procesamiento_id BIGINT,estado_herencia TEXT,
 processed_at TIMESTAMPTZ,receipt_id BIGINT,claimed_at TIMESTAMPTZ,expires_at TIMESTAMPTZ,
 PRIMARY KEY(source_instance_id,attention_id));
CREATE TABLE admission_quick_list_dismissals(source_instance_id TEXT,attention_id BIGINT,is_active BOOLEAN);
CREATE TABLE recibos(id BIGINT,numero INT,admission_global_attention_id UUID,admission_atencion_id BIGINT,
 admission_source_instance_id TEXT,is_deleted INT DEFAULT 0,estado_facturacion TEXT DEFAULT 'PENDIENTE',
 estado_documento TEXT DEFAULT 'PRELIMINAR',created_at TEXT DEFAULT '',
 turno_origen_id BIGINT,turno_procesamiento_id BIGINT,herencia_estado TEXT);
CREATE TABLE ars(id INT,nombre TEXT,billing_enabled BOOLEAN);
"""


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    binary = Path(
        os.environ.get("SIGEH_TEST_PG_BIN", "C:/Program Files/PostgreSQL/17/bin")
    )
    if not (binary / "initdb.exe").exists():
        pytest.skip(
            "Disposable PostgreSQL binaries unavailable; no production fallback"
        )
    root = tmp_path_factory.mktemp("billing-consistency-pg")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    subprocess.run(
        [
            str(binary / "initdb.exe"),
            "-D",
            str(root / "data"),
            "-U",
            "tester",
            "-A",
            "trust",
            "--encoding=UTF8",
            "--no-locale",
        ],
        check=True,
        capture_output=True,
        creationflags=flags,
    )
    control = [str(binary / "pg_ctl.exe"), "-D", str(root / "data")]
    subprocess.run(
        control
        + [
            "-l",
            str(root / "server.log"),
            "-o",
            f"-h 127.0.0.1 -p {port}",
            "-w",
            "start",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        timeout=30,
        creationflags=flags,
    )
    try:
        yield f"host=127.0.0.1 port={port} user=tester dbname=postgres"
    finally:
        subprocess.run(
            control + ["-m", "fast", "-w", "stop"],
            check=True,
            capture_output=True,
            creationflags=flags,
        )


class Connection:
    def __init__(self, dsn):
        self.raw = psycopg2.connect(dsn)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *_args):
        try:
            self.raw.rollback() if exc_type else self.raw.commit()
        finally:
            self.raw.close()

    def execute(self, sql, params=()):
        cursor = self.raw.cursor(cursor_factory=RealDictCursor)
        cursor.execute(sql, params)
        return cursor


@pytest.fixture
def database(server, monkeypatch):
    # Reset only the disposable server created by this fixture.
    with Connection(server) as con:
        con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        con.execute(SCHEMA)
        con.execute("INSERT INTO sigeh_product_state VALUES(1,%s)", (EPOCH,))
        con.execute(
            "INSERT INTO admission_operational_sessions(operational_source_id,turn_id,production_epoch_id) VALUES(%s,3949,%s)",
            (SOURCE, EPOCH),
        )
        con.execute(
            "INSERT INTO admission_attention_projection(attention_id,global_attention_id,operational_source_id) VALUES(329,%s,%s)",
            (GLOBAL, SOURCE),
        )
        con.execute("INSERT INTO ars VALUES(1,'FUTURO',TRUE)")
    monkeypatch.setattr(app, "db_connect", lambda: Connection(server))
    monkeypatch.setattr(app, "write_runtime_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(app, "write_main_app_log", lambda *_args, **_kwargs: None)
    monkeypatch.setenv("COMPUTERNAME", "TEST-A")
    return lambda: Connection(server)


def candidates(identifier="", **kwargs):
    return app.BillingAdmissionQueryService().get_operational_candidates(
        identifier,
        current_user=USER,
        session_id="A",
        **kwargs,
    )


@pytest.mark.parametrize(
    "search",
    [
        "PACIENTE SINTETICO",
        " paciente   sintético ",
        "1234567",
        "00000000001",
        "329",
        GLOBAL,
    ],
)
def test_current_eligible_search(database, search):
    rows = candidates(search)
    assert len(rows) == 1 and rows[0].global_attention_id == GLOBAL


@pytest.mark.parametrize(
    "change",
    [
        "source_status='ANULADA'",
        "service_type='URGENCIA'",
        "is_deleted=TRUE",
        "readiness='PENDIENTE_CORRECCION'",
        "canonical_ars='SENASA SUBSIDIADO'",
    ],
)
def test_excluded_not_pending_after_repeated_reads(database, change):
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET " + change)
    for _ in range(3):
        assert candidates() == []
        assert (
            app.get_projected_billable_attention(329, "ORIGIN", current_user=USER)
            is None
        )


def test_pending_receipt_is_not_new_candidate(database):
    with database() as con:
        con.execute(
            "INSERT INTO recibos(id,admission_global_attention_id) VALUES(6004,%s)",
            (GLOBAL,),
        )
    assert candidates() == []
    result = app.evaluate_attention_billing_eligibility(
        329, USER, global_attention_id=GLOBAL
    )
    assert not result["eligible"] and result["receipt_id"] == 6004


def test_disabled_ars_excluded_in_selector_and_revalidation(database):
    with database() as con:
        con.execute("UPDATE ars SET billing_enabled=FALSE")
    assert candidates() == []
    assert (
        app.get_projected_billable_attention(329, "ORIGIN", current_user=USER) is None
    )


def claim(session, user=USER):
    return app.claim_projected_billable_attention(
        329,
        "ORIGIN",
        username=user["username"],
        session_id=session,
        current_user=user,
        global_attention_id=GLOBAL,
    )


def test_live_claim_one_winner_even_admin_and_expired_recovery(database):
    assert claim("A") is not None
    other = {"username": "other", "role": app.ROLE_ADMIN}
    assert claim("B", other) is None
    with database() as con:
        con.execute(
            "UPDATE admission_billing_claims SET expires_at=NOW()-INTERVAL '1 second'"
        )
    assert claim("B", other) is not None


def test_cancel_and_same_station_resume(database):
    attention = claim("A")
    attention = claim("A-new")
    assert attention is not None
    assert len(candidates()) == 1
    app.release_admission_billing_claim(attention, session_id="A-new")
    assert claim("B", {"username": "other", "role": app.ROLE_ADMIN}) is not None


def test_concurrent_claim_one_winner(database):
    barrier = threading.Barrier(2)

    def reserve(session):
        barrier.wait(timeout=5)
        return claim(session, {"username": session, "role": app.ROLE_ADMIN})

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["A", "B"]))
    assert sum(item is not None for item in results) == 1
    with database() as con:
        assert (
            con.execute(
                "SELECT COUNT(*) AS n FROM admission_billing_claims"
            ).fetchone()["n"]
            == 1
        )


def test_previous_epoch_never_wins_current_turn(database):
    with database() as con:
        con.execute(
            "INSERT INTO admission_operational_sessions(operational_source_id,turn_id,production_epoch_id,updated_at) VALUES(%s,999,%s,NOW()+INTERVAL '1 day')",
            (SOURCE, GLOBAL),
        )
    assert app.get_central_operational_context()["turn_id"] == 3949
    assert len(candidates()) == 1
    history = app.BillingAdmissionQueryService().load_admission_history_batch(
        current_user=USER,
        turn_filter="ACTUAL",
    )
    assert len(history["rows"]) == 1
    assert str(history["rows"][0]["global_attention_id"]) == GLOBAL
    assert app.get_projected_billable_attention(329, "ORIGIN", current_user=USER)
    assert app.evaluate_attention_billing_eligibility(
        329, USER, global_attention_id=GLOBAL
    )["eligible"]
    assert claim("A")


def test_explicit_inheritance_only(database, monkeypatch):
    monkeypatch.setattr(
        app, "_upsert_admission_inheritance", lambda *_args, **_kwargs: None
    )
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET turn_id=3948")
    assert candidates(turn_filter="TODOS") == []
    with database() as con:
        con.execute(
            "INSERT INTO admission_shift_inheritances VALUES('ORIGIN',329,3948,'PENDIENTE')"
        )
    assert len(candidates(turn_filter="HEREDADO")) == 1
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET service_type='URGENCIA'")
    assert candidates(turn_filter="HEREDADO") == []


def test_late_release_cannot_expire_reacquired_claim(database):
    old = claim("A")
    new = claim("A")
    app.release_admission_billing_claim(old, session_id="A")
    with database() as con:
        row = con.execute(
            "SELECT expires_at>NOW() AS active,claimed_at FROM admission_billing_claims"
        ).fetchone()
    assert row["active"] and str(row["claimed_at"]) == new.billing_claim_acquired_at


def test_save_can_resume_expired_own_claim_but_not_other_claim(database):
    attention = claim("A")
    with database() as con:
        con.execute(
            "UPDATE admission_billing_claims SET expires_at=NOW()-INTERVAL '1 second'"
        )
        result = app._lock_and_validate_admission_processing(
            con, attention, session_id="A"
        )
        assert result["turno_origen_id"] == 3949
    with database() as con:
        con.execute("UPDATE admission_billing_claims SET session_id='OTHER'")
    with pytest.raises(app.AdmissionAttentionUnavailableError), database() as con:
        app._lock_and_validate_admission_processing(con, attention, session_id="A")


@pytest.mark.parametrize(
    "change",
    ["service_type='URGENCIA'", "is_deleted=TRUE", "canonical_ars='UNIVERSAL'"],
)
def test_state_changed_after_selection_prevents_receipt_and_releases_owned_claim(
    database, change
):
    attention = claim("A")
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET " + change)
    with pytest.raises(app.AdmissionAttentionUnavailableError), database() as con:
        app._lock_and_validate_admission_processing(con, attention, session_id="A")
    app.release_admission_billing_claim(attention, session_id="A")
    assert candidates() == []
    with database() as con:
        assert (
            con.execute(
                "SELECT expires_at>NOW() AS active FROM admission_billing_claims"
            ).fetchone()["active"]
            is False
        )
        assert con.execute("SELECT COUNT(*) AS n FROM recibos").fetchone()["n"] == 0


def test_missing_source_never_means_same_source(database):
    with database() as con:
        con.execute(
            "UPDATE admission_attention_projection SET operational_source_id=NULL"
        )
    result = app.evaluate_attention_billing_eligibility(
        329, USER, global_attention_id=GLOBAL
    )
    assert not result["eligible"]
    assert candidates() == []


def test_central_error_propagates_not_empty_or_claimed(database, monkeypatch):
    def unavailable():
        raise psycopg2.OperationalError("controlled unavailable")

    monkeypatch.setattr(app, "db_connect", unavailable)
    with pytest.raises(psycopg2.OperationalError):
        candidates()
    with pytest.raises(psycopg2.OperationalError):
        claim("A")
    with pytest.raises(psycopg2.OperationalError):
        app.evaluate_attention_billing_eligibility(
            329, USER, global_attention_id=GLOBAL
        )


def test_annulled_without_tombstone_remains_historical_not_pending(database):
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET source_status='ANULADA'")
    history = app.list_admission_history(current_user=USER, turn_filter="ACTUAL")
    assert len(history["rows"]) == 1
    assert history["rows"][0]["source_status"] == "ANULADA"
    assert candidates() == []
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET is_deleted=TRUE")
    assert app.list_admission_history(current_user=USER)["rows"] == []


def test_tombstone_and_missing_central_identity_are_distinct(database):
    with database() as con:
        con.execute("UPDATE admission_attention_projection SET is_deleted=TRUE")
    result = app.evaluate_attention_billing_eligibility(
        329, USER, global_attention_id=GLOBAL
    )
    assert result["reason_code"] == "TOMBSTONED" and not result["eligible"]
    with database() as con:
        con.execute("UPDATE admission_operational_sessions SET status='CLOSED'")
    with pytest.raises(app.AdmissionBridgeError):
        app.evaluate_attention_billing_eligibility(
            329, USER, global_attention_id=GLOBAL
        )


def test_excluded_search_logs_exact_reason_not_patient_data(database, monkeypatch):
    logs = []
    monkeypatch.setattr(app, "write_runtime_log", logs.append)
    with database() as con:
        con.execute(
            "INSERT INTO recibos(id,admission_global_attention_id) VALUES(6004,%s)",
            (GLOBAL,),
        )
    assert candidates("PACIENTE SINTETICO") == []
    assert any("reason=RECEIPT_PENDING" in entry for entry in logs)
    assert all("PACIENTE" not in entry and "1234567" not in entry for entry in logs)


def test_fresh_bootstrap_installs_bridge_dependencies_and_is_repeatable(
    server, tmp_path, monkeypatch
):
    from psycopg2.pool import ThreadedConnectionPool

    # Only the loopback server created by this module is ever reset.
    with Connection(server) as con:
        con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    pool = ThreadedConnectionPool(1, 2, server)
    monkeypatch.setattr(app, "db_pool", pool)
    monkeypatch.setattr(app, "REPORTS_DIR", str(tmp_path / "reports"))
    monkeypatch.setattr(app, "PDFS_DIR", str(tmp_path / "pdfs"))
    try:
        app.db_init()
        with app.db_connect() as con:
            con.execute("INSERT INTO ars(nombre) VALUES(%s)", ("BOOTSTRAP_PRESERVED",))
        app.db_init()
        with app.db_connect() as con:
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM ars WHERE nombre=%s", ("BOOTSTRAP_PRESERVED",)
                ).fetchone()[0]
                == 1
            )
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM admission_attention_projection WHERE is_deleted=FALSE"
                ).fetchone()[0]
                == 0
            )
            assert (
                con.execute(
                    "SELECT COUNT(*) FROM pg_indexes WHERE indexname=%s",
                    ("idx_admission_projection_billing_scope",),
                ).fetchone()[0]
                == 1
            )
    finally:
        pool.closeall()
