from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import CALCULOS_QT as app


class _ReadOnlyTransactionError(RuntimeError):
    pgcode = "25006"


def test_read_only_error_recognizes_sqlstate_and_wrapped_message():
    assert app.is_database_read_only_error(_ReadOnlyTransactionError("denied"))

    outer = RuntimeError("login failed")
    outer.__cause__ = RuntimeError(
        "cannot execute UPDATE in a read-only transaction"
    )
    assert app.is_database_read_only_error(outer)

    assert not app.is_database_read_only_error(RuntimeError("connection refused"))


def test_online_login_resets_stale_pool_once_and_retries(monkeypatch):
    user = {
        "id": 7,
        "username": "fernando",
        "full_name": "Fernando Guerrero",
        "role": "administrador",
    }
    attempts = []
    resets = []
    cached = []
    cleanups = []

    def authenticate(_username, _password):
        attempts.append("authenticate")
        if len(attempts) == 1:
            raise _ReadOnlyTransactionError("stale pooled connection")
        return user

    monkeypatch.setattr(app, "CENTRAL_OFFLINE_BOOT", False)
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("pc-a", "PC A"))
    monkeypatch.setattr(app, "authenticate_user", authenticate)
    monkeypatch.setattr(app, "reset_database_pool", lambda: resets.append("reset"))
    monkeypatch.setattr(
        app,
        "_clear_sticky_pool_read_only_state",
        lambda: cleanups.append("cleanup") or True,
    )
    monkeypatch.setattr(
        app._OFFLINE_AUTH_CACHE,
        "store_online_auth",
        lambda *args: cached.append(args),
    )

    assert app.authenticate_user_for_login("fernando", "secret") == user
    assert attempts == ["authenticate", "authenticate"]
    assert resets == ["reset"]
    assert cleanups == ["cleanup"]
    assert len(cached) == 1


def test_online_login_does_not_loop_while_server_remains_read_only(monkeypatch):
    attempts = []
    resets = []
    cleanups = []

    def authenticate(_username, _password):
        attempts.append("authenticate")
        raise _ReadOnlyTransactionError("database remains read only")

    monkeypatch.setattr(app, "CENTRAL_OFFLINE_BOOT", False)
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("pc-a", "PC A"))
    monkeypatch.setattr(app, "authenticate_user", authenticate)
    monkeypatch.setattr(app, "reset_database_pool", lambda: resets.append("reset"))
    monkeypatch.setattr(
        app,
        "_clear_sticky_pool_read_only_state",
        lambda: cleanups.append("cleanup") or False,
    )

    with pytest.raises(_ReadOnlyTransactionError):
        app.authenticate_user_for_login("fernando", "secret")

    assert attempts == ["authenticate", "authenticate"]
    assert resets == ["reset"]
    assert cleanups == ["cleanup"]


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _StateConnection:
    def __init__(self, state):
        self.state = state
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        self.queries.append((str(query), params))
        if "pg_is_in_recovery" in str(query):
            return _Result(self.state)
        return _Result()


def test_sticky_pool_state_is_cleared_only_below_safe_capacity(monkeypatch):
    state = {
        "in_recovery": False,
        "read_only": "on",
        "setting_source": "session",
        "database_bytes": int(app.DEFAULT_DATABASE_LIMIT_BYTES * 0.91),
    }
    connection = _StateConnection(state)
    monkeypatch.setattr(app, "db_connect", lambda: connection)

    assert app._clear_sticky_pool_read_only_state() is True
    assert any(
        "SET default_transaction_read_only = off" in query
        for query, _params in connection.queries
    )


@pytest.mark.parametrize(
    "state_update",
    [
        {"in_recovery": True},
        {"read_only": "off"},
        {"setting_source": "default"},
        {"database_bytes": app.DEFAULT_DATABASE_LIMIT_BYTES},
    ],
)
def test_sticky_pool_state_is_not_overridden_when_unsafe(
    monkeypatch, state_update
):
    state = {
        "in_recovery": False,
        "read_only": "on",
        "setting_source": "session",
        "database_bytes": int(app.DEFAULT_DATABASE_LIMIT_BYTES * 0.91),
    }
    state.update(state_update)
    connection = _StateConnection(state)
    monkeypatch.setattr(app, "db_connect", lambda: connection)

    assert app._clear_sticky_pool_read_only_state() is False
    assert not any(
        "SET default_transaction_read_only = off" in query
        for query, _params in connection.queries
    )


def test_sticky_pool_state_handles_empty_or_failed_probe(monkeypatch):
    empty_connection = _StateConnection(None)
    monkeypatch.setattr(app, "db_connect", lambda: empty_connection)
    assert app._clear_sticky_pool_read_only_state() is False

    monkeypatch.setattr(
        app,
        "db_connect",
        lambda: (_ for _ in ()).throw(RuntimeError("probe unavailable")),
    )
    assert app._clear_sticky_pool_read_only_state() is False


def test_pool_refresh_does_not_retry_unrelated_database_error(monkeypatch):
    attempts = []
    monkeypatch.setattr(
        app,
        "reset_database_pool",
        lambda: pytest.fail("pool must not reset for an unrelated error"),
    )

    def fail():
        attempts.append(1)
        raise RuntimeError("connection refused")

    with pytest.raises(RuntimeError, match="connection refused"):
        app._run_with_fresh_pool_after_read_only(fail, stage="LOGIN_TEST")
    assert len(attempts) == 1


def test_login_uses_offline_cache_only_for_offline_or_temporary_failure(
    monkeypatch,
):
    offline_user = {"username": "fernando", "_offline_login": True}
    offline_calls = []
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("pc-a", "PC A"))
    monkeypatch.setattr(
        app._OFFLINE_AUTH_CACHE,
        "authenticate",
        lambda *args: offline_calls.append(args) or offline_user,
    )

    monkeypatch.setattr(app, "CENTRAL_OFFLINE_BOOT", True)
    assert app.authenticate_user_for_login("fernando", "secret") == offline_user

    monkeypatch.setattr(app, "CENTRAL_OFFLINE_BOOT", False)
    monkeypatch.setattr(
        app,
        "_run_with_fresh_pool_after_read_only",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("temporary network failure")
        ),
    )
    monkeypatch.setattr(app, "is_temporary_connection_error", lambda _exc: True)
    assert app.authenticate_user_for_login("fernando", "secret") == offline_user
    assert len(offline_calls) == 2


def test_valid_but_unknown_online_user_does_not_create_offline_cache(monkeypatch):
    monkeypatch.setattr(app, "CENTRAL_OFFLINE_BOOT", False)
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("pc-a", "PC A"))
    monkeypatch.setattr(
        app, "_run_with_fresh_pool_after_read_only", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(
        app._OFFLINE_AUTH_CACHE,
        "store_online_auth",
        lambda *_args: pytest.fail("unknown user must not be cached"),
    )
    assert app.authenticate_user_for_login("missing", "secret") is None


def test_login_worker_reports_central_read_only_without_claiming_bad_password(
    monkeypatch,
):
    failures = []
    monkeypatch.setattr(
        app,
        "authenticate_user_for_login",
        lambda *_args: (_ for _ in ()).throw(
            _ReadOnlyTransactionError("cannot execute UPDATE in a read-only transaction")
        ),
    )

    worker = app.LoginAuthenticationWorker("fernando", "secret")
    worker.failed.connect(failures.append)
    worker.run()

    assert failures == [
        {
            "code": "CENTRAL_DATABASE_READ_ONLY",
            "message": app.CENTRAL_DATABASE_READ_ONLY_MESSAGE,
            "error_type": "_ReadOnlyTransactionError",
        }
    ]
