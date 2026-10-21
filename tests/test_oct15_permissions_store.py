import json
from unittest.mock import Mock

import pytest
import CALCULOS_QT as app
from user_permissions import (
    overrides_for,
    effective_permission,
    validate_overrides,
    save_overrides,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, {}),
        ("{bad", {}),
        ("[]", {}),
        (["bad"], {}),
        ('{"x":false}', {"x": False}),
        ({"x": True}, {"x": True}),
    ],
)
def test_override_storage_shapes(value, expected):
    assert overrides_for({"permission_overrides": value}) == expected


@pytest.mark.parametrize(
    "user,defaults,expected",
    [
        (None, {"x"}, False),
        ({"is_active": 0}, {"x"}, False),
        ({"permission_overrides": {"x": False}}, {"x"}, False),
        ({"permission_overrides": {"x": True}}, set(), True),
        ({"permission_overrides": {"x": 1}}, set(), False),
        ({}, {"x"}, False),
        ({"role": "aux"}, {"x"}, True),
    ],
)
def test_effective_permissions_fail_closed(user, defaults, expected):
    assert effective_permission(user, "x", defaults) is expected


@pytest.mark.parametrize(
    "value", [None, [], {"unknown": True}, {"x": 1}, {"x": "true"}]
)
def test_reject_invalid_permission_requests(value):
    with pytest.raises(ValueError):
        validate_overrides(value, {"x"})


def transaction(actor=None, target=None):
    connection = Mock()
    actor = actor or {"username": "admin", "role": app.ROLE_ADMIN, "is_active": 1}
    target = target or {
        "username": "operator",
        "role": app.ROLE_AUX,
        "is_active": 1,
        "permission_overrides": {},
    }
    connection.execute.return_value.fetchall.return_value = [actor, target]
    return connection


def write(connection, values, **kw):
    return save_overrides(
        connection,
        "operator",
        values,
        {"username": "admin"},
        kw.get("expected"),
        lambda user: app.user_has_permission(
            user, app.PERMISSION_MANAGE_USER_PERMISSIONS
        ),
        {"x"},
    )


def test_store_explicit_deny_and_audit():
    connection = transaction()
    assert write(connection, {"x": False}, expected={}) == {"x": False}
    calls = connection.execute.call_args_list
    assert json.loads(calls[1].args[1][0]) == {"x": False}
    assert json.loads(calls[2].args[1][1])["previous"] == {}


@pytest.mark.parametrize(
    "actor",
    [
        {"username": "admin", "role": app.ROLE_AUX, "is_active": 1},
        {"username": "admin", "role": app.ROLE_ADMIN, "is_active": 0},
        {
            "username": "admin",
            "role": app.ROLE_ADMIN,
            "permission_overrides": {app.PERMISSION_MANAGE_USER_PERMISSIONS: False},
        },
    ],
)
def test_revalidate_administrator_and_reject_forged_context(actor):
    connection = transaction(actor=actor)
    with pytest.raises(PermissionError):
        write(connection, {"x": True})
    assert connection.execute.call_count == 1


def test_missing_actor_or_target_and_revision_conflict():
    for rows in [
        [],
        [{"username": "admin", "role": app.ROLE_ADMIN}],
        [
            {"username": "admin", "role": app.ROLE_ADMIN},
            {"username": "operator", "permission_overrides": {"x": False}},
        ],
    ]:
        connection = transaction()
        connection.execute.return_value.fetchall.return_value = rows
        with pytest.raises((PermissionError, ValueError)):
            write(connection, {"x": True}, expected={})
        assert connection.execute.call_count == 1


def test_reset_defaults_and_idempotent_noop():
    connection = transaction()
    assert write(connection, {}) == {}
    assert connection.execute.call_count == 1
    connection = transaction(
        target={"username": "operator", "permission_overrides": {"x": True}}
    )
    assert write(connection, {}, expected={"x": True}) == {}


def test_operation_reloads_effective_permissions(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_user",
        lambda name: {
            "username": name,
            "role": app.ROLE_AUX,
            "permission_overrides": {app.PERMISSION_EDIT_PENDING: False},
        },
    )
    with pytest.raises(PermissionError):
        app._require_receipt_permission(
            {"username": "operator", "role": app.ROLE_ADMIN},
            app.PERMISSION_EDIT_PENDING,
            "denied",
        )
    monkeypatch.setattr(app, "get_user", lambda _name: None)
    with pytest.raises(PermissionError):
        app._require_receipt_permission(
            "operator", app.PERMISSION_EDIT_PENDING, "denied"
        )
