from __future__ import annotations

import threading
from types import SimpleNamespace

import CALCULOS_QT as app
from admission_hybrid import DeviceAttachment, StationRole
from admission_v15_adapter import _HybridAdmissionRuntime


class _Rows:
    def __init__(self, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _Connection:
    def __init__(self):
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.queries.append((str(query), params))
        if "SELECT 1" in str(query):
            return _Rows((1,))
        if "to_regclass" in str(query):
            return _Rows(("present",))
        return _Rows()


def test_bootstrap_blocking_calls_run_outside_main_thread(monkeypatch, tmp_path):
    calls = []

    def record(name, value=None):
        calls.append((name, threading.get_ident()))
        return value

    connection = _Connection()
    monkeypatch.setattr(app, "MAIN_APP_LOG_PATH", tmp_path / "main_app.log")
    monkeypatch.setattr(app, "resolve_database_url", lambda: "postgresql://u:p@host/db")
    monkeypatch.setattr(app, "validate_database_url", lambda value: value)
    monkeypatch.setattr(
        app, "probe_database_connection", lambda *_: record("probe")
    )
    monkeypatch.setattr(app, "init_pool", lambda: record("pool"))
    monkeypatch.setattr(app, "db_connect", lambda: connection)
    monkeypatch.setattr(
        app,
        "inspect_database_schema_compatibility",
        lambda _con: (True, []),
    )
    monkeypatch.setattr(
        app,
        "prepare_database_schema",
        lambda: record("schema", "ALREADY_COMPATIBLE"),
    )
    monkeypatch.setattr(app, "list_usernames", lambda: ["admin"])
    monkeypatch.setattr(
        app,
        "get_admission_operational_snapshot",
        lambda: record(
            "snapshot",
            {
                "operational_session_id": "op-1",
                "primary_device_id": "pc1",
                "turn_id": 320,
                "generation": 30,
            },
        ),
    )

    worker = app.ApplicationBootstrapWorker()
    thread = threading.Thread(target=worker.run, name="bootstrap-test")
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert {name for name, _ident in calls} >= {"probe", "pool", "schema", "snapshot"}
    assert all(ident != threading.main_thread().ident for _name, ident in calls)


def test_db_prod_002_log_keeps_stage_exception_and_traceback_without_secret(
    monkeypatch, tmp_path
):
    log_path = tmp_path / "main_app.log"
    monkeypatch.setattr(app, "MAIN_APP_LOG_PATH", log_path)
    try:
        raise RuntimeError(
            "ProgrammingError on postgresql://service:super-secret@db.example/hospital"
        )
    except RuntimeError as exc:
        app.write_main_app_log(
            "DB-PROD-002",
            stage="DB_MIGRATION_CHECK",
            success=False,
            exception=exc,
        )

    content = log_path.read_text(encoding="utf-8")
    assert "DB-PROD-002" in content
    assert "stage=DB_MIGRATION_CHECK" in content
    assert "exception_type=RuntimeError" in content
    assert "Traceback (most recent call last)" in content
    assert "super-secret" not in content
    assert "postgresql://service:***@db.example/hospital" in content


def test_compatible_schema_skips_legacy_db_init(monkeypatch):
    class RawCursor:
        def execute(self, *_args):
            return None

        def close(self):
            return None

    class RawConnection:
        autocommit = False

        def cursor(self):
            return RawCursor()

        def close(self):
            return None

    connection = _Connection()
    monkeypatch.setattr(app, "validate_database_url", lambda _: "postgresql://ok/db")
    monkeypatch.setattr(app.psycopg2, "connect", lambda *_a, **_k: RawConnection())
    monkeypatch.setattr(app, "db_connect", lambda: connection)
    monkeypatch.setattr(
        app,
        "inspect_database_schema_compatibility",
        lambda _con: (True, []),
    )
    migration_calls = []
    monkeypatch.setattr(app, "db_init", lambda: migration_calls.append(True))

    assert app.prepare_database_schema() == "ALREADY_COMPATIBLE"
    assert migration_calls == []


def test_secondary_is_seeded_from_central_before_v15_mirror():
    host = SimpleNamespace(
        logger=None,
        connection_factory=lambda: None,
        user={"id": "user-2", "username": "fernando", "role": "administrador"},
        session_id="login-2",
        device_id="pc2",
        device_name="PC2",
        current_shift={},
        configuration={"central_schema_ready": True},
        bootstrap_operational_snapshot={
            "operational_session_id": "op-1",
            "operational_source_id": "source-1",
            "active_user_id": "user-2",
            "active_username": "fernando",
            "active_user_display_name": "FERNANDO JHOEL GUERRERO",
            "turn_id": 320,
            "generation": 30,
            "primary_device_id": "pc1",
            "status": "ACTIVE",
        },
    )

    runtime = _HybridAdmissionRuntime(host)

    assert runtime.role is StationRole.SECONDARY
    assert runtime.operational_session.turn_id == 320
    assert runtime.operational_session.generation == 30
    assert runtime.offline is False
    assert runtime._pending_mirror_state is not None
    runtime.session_service.ensure_schema = lambda: (_ for _ in ()).throw(
        AssertionError("schema DDL must not run on the 2-second sync tick")
    )
    attached = []
    runtime.session_service.attach_device = lambda **_kwargs: (
        attached.append(True)
        or DeviceAttachment(
            runtime.operational_session, StationRole.SECONDARY, True, "Conectado."
        )
    )
    runtime.session_service.get_central_admission_operational_state = lambda **_kwargs: runtime._operational_state
    runtime._attach_remote_if_needed()
    assert attached == [True]


def test_central_snapshot_is_one_lightweight_query(monkeypatch):
    row = {
        "operational_session_id": "op-1",
        "active_user_id": "user-2",
        "active_username": "fernando",
        "turn_id": 320,
        "generation": 30,
        "primary_device_id": "pc1",
        "status": "ACTIVE",
    }

    class SnapshotConnection(_Connection):
        def execute(self, query, params=None):
            self.queries.append((str(query), params))
            return _Rows(row)

    connection = SnapshotConnection()
    monkeypatch.setattr(app, "db_connect", lambda: connection)

    assert app.get_admission_operational_snapshot()["turn_id"] == 320
    assert len(connection.queries) == 1
    sql = connection.queries[0][0].casefold()
    assert "admission_operational_sessions" in sql
    assert "patient" not in sql
    assert "report" not in sql


def test_login_and_local_v15_prepare_do_not_run_on_gui_thread(monkeypatch, tmp_path):
    calls = []

    def record(name, value=None):
        calls.append((name, threading.get_ident()))
        return value

    class DatabaseManager:
        def __init__(self, **_kwargs):
            record("sqlite_v15")

    monkeypatch.setattr(app, "MAIN_APP_LOG_PATH", tmp_path / "main_app.log")
    monkeypatch.setattr(
        app,
        "authenticate_user_for_login",
        lambda *_: record(
            "authenticate",
            {"id": 2, "username": "fernando", "role": "administrador"},
        ),
    )
    monkeypatch.setattr(app, "make_session_id", lambda: "login-2")
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("pc2", "PC2"))
    monkeypatch.setattr(app, "acquire_local_session_mutex", lambda *_: True)
    monkeypatch.setattr(
        app, "register_active_session", lambda *_a, **_k: {"recovered_same_device": False}
    )
    monkeypatch.setattr(app, "log_action", lambda *_a, **_k: None)
    monkeypatch.setattr(
        app,
        "load_v15_application_module",
        lambda: SimpleNamespace(DatabaseManager=DatabaseManager),
    )
    monkeypatch.setattr(app, "get_universal", lambda category: record(category, {}))
    monkeypatch.setattr(app, "get_user_preferences", lambda *_: record("prefs", {}))
    monkeypatch.setattr(
        app, "list_user_catalog_favorites", lambda *_: record("favorites", set())
    )

    worker = app.LoginAuthenticationWorker("fernando", "secret")
    thread = threading.Thread(target=worker.run, name="login-test")
    thread.start()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert {name for name, _ident in calls} >= {
        "authenticate",
        "sqlite_v15",
        "Medicamentos",
        "Materiales",
        "prefs",
        "favorites",
    }
    assert all(ident != threading.main_thread().ident for _name, ident in calls)
