"""Real authentication persistence on a disposable local database."""

import pytest
from psycopg2.pool import ThreadedConnectionPool

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_oct23_password_security import legacy


@pytest.fixture
def users(server, monkeypatch):
    pool = ThreadedConnectionPool(1, 4, server)
    monkeypatch.setattr(app, "db_pool", pool)
    digest, salt = legacy("old")
    with app.db_connect() as con:
        con.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        con.execute("""CREATE TABLE users(id BIGINT PRIMARY KEY,username TEXT,
            full_name TEXT,role TEXT,is_active INT,password_hash TEXT,
            password_salt TEXT,security_answer_hash TEXT,security_answer_salt TEXT,
            last_login TEXT)""")
        con.execute(
            """INSERT INTO users VALUES(7,'test','Original user','admin',1,
            %s,%s,%s,%s,NULL)""",
            (digest, salt, digest, salt),
        )
    try:
        yield
    finally:
        pool.closeall()


def snapshot():
    with app.db_connect() as con:
        return dict(con.execute("SELECT * FROM users WHERE id=7").fetchone())


def test_legacy_login_preserves_hash_and_another_connection_sees_login(users):
    original = snapshot()
    assert app.authenticate_user("test", "bad") is None
    assert snapshot() == original
    account = app.authenticate_user("test", "old")
    assert account["id"] == 7 and "password_hash" not in account
    upgraded = snapshot()
    assert upgraded["password_hash"] == original["password_hash"]
    assert upgraded["last_login"]
    assert app.authenticate_user("test", "old")["id"] == 7
    assert snapshot()["password_hash"] == upgraded["password_hash"]
    for field in ("id", "username", "full_name", "role", "security_answer_hash"):
        assert snapshot()[field] == original[field]
    with app.db_connect() as con:
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_credential_recheck_and_transaction_rollback(users):
    original = snapshot()
    assert app.verify_user_credentials("test", "old")["id"] == 7
    assert snapshot() == original
    with pytest.raises(RuntimeError, match="rollback"):
        with app.db_connect() as con:
            digest, salt = app.hash_secret("temporary", compatible=True)
            con.execute(
                "UPDATE users SET password_hash=%s,password_salt=%s WHERE id=7",
                (digest, salt),
            )
            raise RuntimeError("rollback")
    assert snapshot() == original


def test_password_reset_is_not_overwritten_by_later_login(users):
    digest, salt = app.hash_secret("new-password", compatible=True)
    with app.db_connect() as con:
        con.execute(
            "UPDATE users SET password_hash=%s,password_salt=%s WHERE id=7",
            (digest, salt),
        )
    assert app.authenticate_user("test", "old") is None
    assert snapshot()["password_hash"] == digest
    assert app.authenticate_user("test", "old") is None
    assert app.authenticate_user("test", "new-password")["id"] == 7
