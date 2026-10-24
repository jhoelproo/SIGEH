"""Versioned password storage accepts old hashes without storing plaintext."""

import base64
import hashlib
import sqlite3
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import CALCULOS_QT as app
from offline_auth import OfflineAuthCache
import password_secrets as storage


def legacy(password):
    salt = b"synthetic-salt16"
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return base64.b64encode(digest).decode(), base64.b64encode(salt).decode()


def test_new_secrets_use_versioned_salted_hashes():
    first, salt = app.hash_secret("Prueba segura 001!")
    second, other_salt = app.hash_secret("Prueba segura 001!")
    assert first.startswith("pbkdf2_sha256$600000$")
    assert first != second and salt != other_salt
    assert app.verify_secret("Prueba segura 001!", first, salt)
    assert not app.verify_secret("incorrecta", first, salt)
    assert "Prueba segura 001!" not in first + salt


def test_legacy_users_remain_valid_without_reset():
    digest, salt = legacy("Contraseña antigua ñ")
    assert app.verify_secret("Contraseña antigua ñ", digest, salt)
    assert not app.verify_secret("incorrecta", digest, salt)


@pytest.mark.parametrize(
    "digest,salt",
    [
        (None, None),
        ("", ""),
        ("é", "!"),
        ("wrong", "AA=="),
        ("pbkdf2_sha256$0$abc", "AA=="),
        ("pbkdf2_sha256$999999999$abc", "AA=="),
        ("pbkdf2_sha256$x$abc", "AA=="),
        ("other$600000$abc", "AA=="),
        ("pbkdf2_sha256$600000$abc$extra", "AA=="),
    ],
)
def test_invalid_hashes_fail_closed(digest, salt):
    assert app.verify_secret("test", digest, salt) is False


@pytest.mark.parametrize("password", ["", "a", "ñ😀\x00", "a" * 4096])
def test_password_boundaries_and_unicode(password):
    digest, salt = app.hash_secret(password)
    assert app.verify_secret(password, digest, salt)
    assert not app.verify_secret(password + "x", digest, salt)


@pytest.mark.parametrize(
    "active,password,expected", [(1, "old", True), (1, "bad", False), (0, "old", False)]
)
def test_login_preserves_identity_permissions_and_never_returns_hashes(
    monkeypatch, active, password, expected
):
    digest, salt = legacy("old")
    row = {
        "id": 7,
        "username": "test",
        "role": app.ROLE_ADMIN,
        "is_active": active,
        "password_hash": digest,
        "password_salt": salt,
        "security_answer_hash": digest,
        "security_answer_salt": salt,
        "permission_overrides": {"x": False},
    }
    con = MagicMock()
    con.__enter__.return_value = con
    con.execute.return_value.fetchone.return_value = row
    monkeypatch.setattr(app, "db_connect", lambda: con)
    user = app.authenticate_user("test", password)
    if not expected:
        assert user is None
        assert con.execute.call_count == 1
        return
    assert user["id"] == 7 and user["permission_overrides"] == {"x": False}
    assert "password_hash" not in user and "security_answer_hash" not in user
    assert con.execute.call_count == 2
    assert "last_login" in con.execute.call_args.args[0]
    assert all(
        "password_hash=" not in call.args[0] for call in con.execute.call_args_list
    )


def test_credential_recheck_never_exposes_verifiers_or_changes_last_login(monkeypatch):
    digest, salt = legacy("old")
    con = MagicMock()
    con.__enter__.return_value = con
    con.execute.return_value.fetchone.return_value = {
        "id": 7,
        "username": "test",
        "is_active": 1,
        "password_hash": digest,
        "password_salt": salt,
    }
    monkeypatch.setattr(app, "db_connect", lambda: con)
    user = app.verify_user_credentials("test", "old")
    assert user["id"] == 7 and "password_hash" not in user
    assert con.execute.call_count == 1


def test_offline_new_hashes_are_stronger_and_persist_across_reopen(tmp_path):
    path = tmp_path / "cache.sqlite"
    cache = OfflineAuthCache(path)
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    user = {"id": "7", "username": "test", "role": "admin"}
    cache.store_online_auth(user, "No plaintext 001!", "station", now=now)
    with sqlite3.connect(path) as con:
        assert (
            con.execute(
                "SELECT verifier_iterations FROM offline_auth_cache"
            ).fetchone()[0]
            == 600_000
        )
    assert (
        OfflineAuthCache(path).authenticate(
            "test", "No plaintext 001!", "station", now=now
        )["id"]
        == "7"
    )
    assert not cache.contains_plaintext_password("No plaintext 001!")
    assert cache.authenticate("test", "No plaintext 001!", "other", now=now) is None


@pytest.mark.parametrize("iterations", [120_000, 240_000, 600_000])
def test_offline_legacy_hashes_are_upgraded_without_changing_expiry(
    tmp_path, iterations
):
    path = tmp_path / "cache.sqlite"
    cache = OfflineAuthCache(path)
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    cache.store_online_auth(
        {"id": 7, "username": "test", "role": "admin"}, "old", "station", now=now
    )
    salt = b"0123456789abcdef"
    digest = hashlib.pbkdf2_hmac("sha256", b"old", salt, iterations)
    with sqlite3.connect(path) as con:
        con.execute(
            "UPDATE offline_auth_cache SET verifier_iterations=?,password_salt=?,password_verifier=?",
            (iterations, salt, digest),
        )
        expires = con.execute(
            "SELECT offline_valid_until FROM offline_auth_cache"
        ).fetchone()[0]
    assert cache.authenticate("test", "old", "station", now=now)
    with sqlite3.connect(path) as con:
        current = con.execute(
            "SELECT verifier_iterations,offline_valid_until FROM offline_auth_cache"
        ).fetchone()
    assert current == (600_000, expires)


@pytest.mark.parametrize("length", [0, 15, 17])
def test_invalid_salt_lengths_are_rejected(length):
    digest, _salt = app.hash_secret("test")
    assert not app.verify_secret("test", digest, base64.b64encode(b"s" * length))


@pytest.mark.parametrize("length", [0, 31, 33])
def test_invalid_digest_lengths_are_rejected(length):
    digest = base64.b64encode(b"x" * length).decode()
    assert not app.verify_secret("test", digest, base64.b64encode(b"s" * 16))


@pytest.mark.parametrize(
    "iterations", [119_999, 120_000, 599_999, 600_000, 2_000_000, 2_000_001]
)
def test_cost_boundaries_and_rehash_policy(iterations):
    digest = base64.b64encode(b"x" * 32).decode()
    hashed = f"pbkdf2_sha256${iterations}${digest}"
    if 120_000 <= iterations <= 2_000_000:
        assert storage.needs_rehash(hashed) == (iterations < 600_000)
    else:
        with pytest.raises(ValueError):
            storage.needs_rehash(hashed)


@pytest.mark.parametrize(
    "salt,digest,cost",
    [
        (b"s" * 16, b"d" * 32, "invalid"),
        (b"s" * 16, b"d" * 32, None),
        (b"s" * 16, b"d" * 32, 119_999),
        (b"s" * 16, b"d" * 32, 2_000_001),
        (None, b"d" * 32, 600_000),
        (b"s", b"d" * 32, 600_000),
        (b"s" * 16, b"d", 600_000),
    ],
)
def test_offline_corrupt_verifiers_fail_closed(salt, digest, cost):
    assert not storage.offline_verifier_matches("test", salt, digest, cost)
