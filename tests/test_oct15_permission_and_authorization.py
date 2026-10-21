from unittest.mock import Mock

import pytest
import CALCULOS_QT as app
from receipt_list_consistency import normalize_list_metadata


@pytest.mark.parametrize(
    "role,override,expected",
    [
        (app.ROLE_AUX, True, True),
        (app.ROLE_ADMIN, False, False),
        (app.ROLE_ADMIN, None, True),
        (app.ROLE_AUX, None, False),
    ],
)
def test_individual_override_precedes_role(role, override, expected):
    permission = app.PERMISSION_MANAGE_BILLING_LISTS
    exceptions = {} if override is None else {permission: override}
    assert (
        app.user_has_permission(
            {"role": role, "permission_overrides": exceptions}, permission
        )
        is expected
    )


@pytest.mark.parametrize("value", ["12AB34", "12-34", "１２３４", "123 456", "123!"])
def test_monthly_authorization_rejects_non_digits(value):
    with pytest.raises(ValueError, match="[Dd]ígitos|numéric"):
        normalize_list_metadata("NSS", "001234567", value, "GENERAL")


def test_authorization_keeps_leading_zeros():
    assert (
        normalize_list_metadata("NSS", "001234567", "000123", "GENERAL")[2] == "000123"
    )


def test_denied_edit_is_enforced_before_receipt_persistence(monkeypatch):
    monkeypatch.setattr(
        app,
        "get_user",
        lambda _name: {
            "username": "operator",
            "role": app.ROLE_ADMIN,
            "permission_overrides": {app.PERMISSION_EDIT_PENDING: False},
        },
    )
    connect = Mock(side_effect=AssertionError("persistence must not start"))
    monkeypatch.setattr(app, "db_connect", connect)
    with pytest.raises(PermissionError):
        app.save_receipt_with_items(
            1,
            1,
            "SYNTHETIC",
            "2026-10-15",
            "",
            "FUTURO",
            0,
            0,
            "",
            "operator",
            False,
            "",
            [],
            authorization_number="001234",
        )
    connect.assert_not_called()


@pytest.mark.parametrize("value", ["ABC123", "12-34", "１２３４"])
def test_receipt_rejects_invalid_authorization_before_database(monkeypatch, value):
    monkeypatch.setattr(
        app, "get_user", lambda _name: {"username": "operator", "role": app.ROLE_ADMIN}
    )
    connect = Mock(side_effect=AssertionError("must not connect"))
    monkeypatch.setattr(app, "db_connect", connect)
    with pytest.raises(ValueError, match="[Dd]ígitos|numéric"):
        app.save_receipt_with_items(
            None,
            1,
            "SYNTHETIC",
            "2026-10-15",
            "",
            "FUTURO",
            0,
            0,
            "",
            "operator",
            False,
            "",
            [],
            authorization_number=value,
            verification_bypass={"reason": "SYNTHETIC TEST"},
        )
    connect.assert_not_called()
