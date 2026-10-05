from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from receipt_list_consistency import (
    effective_list_entry,
    lock_receipt,
    validate_authorization,
    normalize_list_metadata,
)


@pytest.mark.parametrize(
    "value",
    ["", None, "123", "1", "0", "-1234", "1234.5", "ABC1234", "１２３４", "1" * 41],
)
def test_authorization_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        validate_authorization(value)


@pytest.mark.parametrize("value", ["0000", " 001234 ", "1" * 40])
def test_authorization_preserves_leading_zeroes_and_boundaries(value):
    assert validate_authorization(value) == value.strip()


@pytest.mark.parametrize(
    "kind,expected", [(" NSS ", "NSS"), ("cedula", "CÉDULA"), ("CÉDULA", "CÉDULA")]
)
def test_list_document_limits_default_specialty_and_normalization(kind, expected):
    assert normalize_list_metadata(kind, "0" * 24, "0" * 40, "") == (
        expected,
        "0" * 24,
        "0" * 40,
        "EMERGENCIOLOGÍA",
    )


def test_missing_specialty_stays_none_when_no_correction_exists():
    result = effective_list_entry(
        entry(receipt_specialty=None, specialty_snapshot=None)
    )
    assert result["specialty_snapshot"] is None


def entry(**changes):
    values = dict(
        batch_status="PENDIENTE",
        recibo_id=1,
        authorization_snapshot="old",
        receipt_authorization="new",
        receipt_authorization_at="2026-10-05 10:00:00",
        receipt_edited_at="2026-10-05 10:00:00",
        last_edited_at="2026-10-05 09:00:00",
        receipt_nss="00123",
        nss_snapshot="older",
        receipt_cedula="00011",
        cedula_snapshot="older",
        receipt_specialty="GENERAL",
        specialty_snapshot="old",
        document_type_snapshot="NSS",
    )
    return {**values, **changes}


def test_reconcile_legacy_copies_uses_latest_timestamp_without_mutating_input():
    raw = entry()
    result = effective_list_entry(raw)
    assert result["authorization_snapshot"] == "new"
    assert result["document_number_snapshot"] == "00123"
    assert raw["authorization_snapshot"] == "old"
    assert (
        effective_list_entry(entry(last_edited_at="2026-10-05 11:00:00"))[
            "authorization_snapshot"
        ]
        == "old"
    )


@pytest.mark.parametrize("status", ["ENVIADO", "CERRADO", "CANCELADO", None])
def test_frozen_or_unknown_status_does_not_change_snapshot(status):
    raw = entry(batch_status=status)
    assert effective_list_entry(raw) == raw


def test_attention_without_receipt_is_not_matched_by_name():
    raw = entry(recibo_id=None)
    assert effective_list_entry(raw) == raw


def test_cedula_selection_and_empty_current_values_preserve_legacy_data():
    result = effective_list_entry(entry(document_type_snapshot="CÉDULA"))
    assert result["document_number_snapshot"] == "00011"
    result = effective_list_entry(
        entry(
            receipt_authorization="",
            receipt_authorization_at=None,
            last_edited_at=None,
            receipt_edited_at=None,
            receipt_nss="",
            nss_snapshot="00123",
        )
    )
    assert result["authorization_snapshot"] == "old"
    assert result["document_number_snapshot"] == "00123"


def test_timestamp_timezone_and_explicit_empty_correction_are_respected():
    result = effective_list_entry(
        entry(receipt_authorization_at="2026-10-05T15:00:00+00:00")
    )
    assert result["authorization_snapshot"] == "new"
    result = effective_list_entry(
        entry(receipt_authorization="", last_edited_at="invalid")
    )
    assert result["authorization_snapshot"] == ""


def test_missing_receipt_is_rejected_before_mutation():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = None
    with pytest.raises(ValueError, match="no existe"):
        lock_receipt(connection, 42)
    assert connection.execute.call_count == 1


@pytest.mark.parametrize(
    "values,message",
    [
        ({"document_type": "PASAPORTE"}, "Selecciona"),
        ({"document_number": ""}, "Escribe el NSS"),
        ({"document_number": "x" * 25}, "24 caracteres"),
        ({"authorization": ""}, "Escribe el número"),
        ({"authorization": "x" * 41}, "40 caracteres"),
    ],
)
def test_invalid_list_corrections_do_not_connect(monkeypatch, values, message):
    connect = Mock()
    monkeypatch.setattr(app, "db_connect", connect)
    arguments = dict(
        document_type="NSS",
        document_number="1234",
        authorization="1234",
        specialty="",
        user={"role": app.ROLE_ADMIN},
    )
    with pytest.raises(ValueError, match=message):
        app.update_monthly_batch_receipt_export_data(1, 2, **{**arguments, **values})
    connect.assert_not_called()


def test_list_and_quick_permissions_are_checked_before_connection(monkeypatch):
    connect = Mock()
    monkeypatch.setattr(app, "db_connect", connect)
    with pytest.raises(PermissionError):
        app.update_monthly_batch_receipt_export_data(
            1,
            2,
            document_type="NSS",
            document_number="1234",
            authorization="1234",
            specialty="",
            user={},
        )
    with pytest.raises(PermissionError):
        app.update_receipt_authorization(1, "1234", {}, expected_authorization="")
    connect.assert_not_called()
