"""Saved corrections can refresh a pending editor without accepting external drift."""

import pytest

import billing_admission_edit


@pytest.mark.parametrize("owned", [True, False])
def test_only_saved_matching_owned_identifiers_refresh_editor(owned):
    snapshot = {
        "nss_clean": "OLD",
        "cedula_clean": "OLD-CEDULA",
        "name": "PATIENT",
        "service_date": "2026-10-06",
        "canonical_ars": "RENACER",
    }
    projection = {
        "allow_pending_receipt_correction": owned,
        "nss_snapshot": "000123",
        "cedula_snapshot": "40200000001",
    }
    receipt = {
        "admission_nss_snapshot": "000123",
        "admission_cedula_snapshot": "40200000001",
    }
    result = billing_admission_edit.reconcile_owned_identifiers(
        snapshot, projection, receipt
    )
    assert result["nss_clean"] == ("000123" if owned else "OLD")
    assert result["cedula_clean"] == ("40200000001" if owned else "OLD-CEDULA")
    assert result["name"] == "PATIENT"
    assert result["service_date"] == "2026-10-06"
    assert result["canonical_ars"] == "RENACER"
    assert snapshot["nss_clean"] == "OLD"


@pytest.mark.parametrize(
    "projection,receipt",
    [
        ({"nss_snapshot": "UNSAVED"}, {"admission_nss_snapshot": "000123"}),
        ({"nss_snapshot": "123"}, {"admission_nss_snapshot": "000123"}),
        ({"nss_snapshot": "000123"}, {}),
        ({}, {"admission_nss_snapshot": "000123"}),
    ],
)
def test_external_or_incomplete_identifier_drift_is_not_accepted(projection, receipt):
    snapshot = {"nss_clean": "OLD"}
    result = billing_admission_edit.reconcile_owned_identifiers(
        snapshot, {"allow_pending_receipt_correction": True, **projection}, receipt
    )
    assert result == snapshot


def test_saved_empty_identifier_and_snapshot_without_identifiers():
    projection = {
        "allow_pending_receipt_correction": True,
        "nss_snapshot": None,
        "cedula_snapshot": "  ",
    }
    receipt = {"admission_nss_snapshot": "", "admission_cedula_snapshot": None}
    assert billing_admission_edit.reconcile_owned_identifiers(
        {"nss_clean": "OLD", "cedula_clean": "OLD"}, projection, receipt
    ) == {"nss_clean": "", "cedula_clean": ""}
    assert billing_admission_edit.reconcile_owned_identifiers(
        {"name": "PATIENT"}, projection, receipt
    ) == {"name": "PATIENT"}
