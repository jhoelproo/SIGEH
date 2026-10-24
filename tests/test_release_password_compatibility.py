"""Mixed workstations must still authenticate after central account changes."""

import base64
import hashlib
import hmac
from unittest.mock import MagicMock

import pytest

import CALCULOS_QT as app


def old_client_accepts(password, digest, salt):
    actual = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), base64.b64decode(salt), 120_000
    )
    return hmac.compare_digest(base64.b64encode(actual).decode(), digest)


@pytest.mark.parametrize("operation", ["create", "admin_reset", "security_reset"])
def test_central_writes_remain_readable_by_installed_clients(monkeypatch, operation):
    con = MagicMock()
    con.__enter__.return_value = con
    digest, salt = app.hash_secret("answer")
    con.execute.return_value.fetchone.return_value = {
        "security_answer_hash": digest,
        "security_answer_salt": salt,
    }
    monkeypatch.setattr(app, "db_connect", lambda: con)
    password = "new password 001!"
    if operation == "create":
        app.create_user("Test", "test", password, "Question", "Answer")
        values = con.execute.call_args.args[1]
        stored, stored_salt = values[3:5]
        assert old_client_accepts("answer", values[6], values[7])
    elif operation == "admin_reset":
        app.admin_reset_password("test", password)
        stored, stored_salt = con.execute.call_args.args[1][:2]
    else:
        assert app.reset_password_by_security("test", "ANSWER", password)
        stored, stored_salt = con.execute.call_args.args[1][:2]
    assert old_client_accepts(password, stored, stored_salt)
    assert not old_client_accepts("wrong", stored, stored_salt)
    assert password not in stored + stored_salt
