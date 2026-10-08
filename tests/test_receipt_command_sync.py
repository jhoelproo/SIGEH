from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from receipt_command_sync import (
    confirm_receipt_request,
    find_confirmed_request,
    lock_receipt_request,
    validate_request,
)

IDENTITY = "11111111-1111-4111-8111-111111111111"
DIGEST = "a" * 64


@pytest.mark.parametrize(
    "identity,digest",
    [("wrong", DIGEST), (IDENTITY, ""), (IDENTITY, "g" * 64), (IDENTITY, "a" * 63)],
)
def test_request_rejects_invalid_identity_or_hash(identity, digest):
    with pytest.raises(ValueError):
        validate_request(identity, digest)


def test_confirmation_is_locked_and_reused_with_bound_parameters():
    row = {"id": 9, "numero": 992000, "username": "demo", "local_request_hash": DIGEST}
    con = Mock()
    con.execute.return_value = SimpleNamespace(fetchone=lambda: row)
    assert lock_receipt_request(con, IDENTITY, DIGEST, "demo") == (9, 992000)
    assert "pg_advisory_xact_lock" in con.execute.call_args_list[0].args[0]
    assert con.execute.call_args_list[0].args[1] == (IDENTITY,)
    assert con.execute.call_args_list[1].args[1] == (IDENTITY,)


def test_absent_request_is_available_for_creation():
    con = Mock()
    con.execute.return_value = SimpleNamespace(fetchone=lambda: None)
    assert find_confirmed_request(con, IDENTITY, DIGEST, "demo") is None


@pytest.mark.parametrize(
    "username,digest,error",
    [("other", DIGEST, PermissionError), ("demo", "b" * 64, ValueError)],
)
def test_existing_request_cannot_be_reassigned(username, digest, error):
    con = Mock()
    con.execute.return_value = SimpleNamespace(
        fetchone=lambda: {
            "id": 9,
            "numero": 992000,
            "username": "demo",
            "local_request_hash": DIGEST,
        }
    )
    with pytest.raises(error):
        find_confirmed_request(con, IDENTITY, digest, username)


def test_confirmation_updates_request_in_current_transaction():
    con = Mock()
    confirm_receipt_request(con, IDENTITY, DIGEST, 9)
    assert con.execute.call_args.args[1] == (str(uuid.UUID(IDENTITY)), DIGEST, 9)
    con.commit.assert_not_called()
