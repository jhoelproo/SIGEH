"""Corrections retain document validation and tolerate old receipts without a snapshot."""

from unittest.mock import Mock

import pytest

import CALCULOS_QT as app


@pytest.mark.parametrize(
    "attention,authorization,state",
    [
        (None, "1234", app.DOCUMENT_PRELIMINARY),
        (1, "", app.DOCUMENT_PRELIMINARY),
        (1, "1234", app.DOCUMENT_READY),
    ],
)
def test_non_bypass_corrections_preserve_patient_validation(
    attention, authorization, state
):
    assert app._receipt_metadata_policy(
        {"admission_atencion_id": attention}, authorization
    ) == (state, app.AUTH_REVIEW_NOT_APPLICABLE, "")


@pytest.mark.parametrize(
    "row",
    [
        None,
        {"snapshot_jsonb": None},
        {"snapshot_jsonb": {"document": {"logo_path": "known-logo"}}},
    ],
)
def test_snapshot_correction_handles_missing_and_existing_context(monkeypatch, row):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = row
    save = Mock(return_value="snapshot")
    monkeypatch.setattr(app, "save_receipt_document_snapshot", save)
    assert app._snapshot_corrected_receipt(connection, 1, "qa") == "snapshot"
    context = dict((row["snapshot_jsonb"] or {}).get("document") or {}) if row else {}
    save.assert_called_once_with(connection, 1, "qa", document_context=context)
