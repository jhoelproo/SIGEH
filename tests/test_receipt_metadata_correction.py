"""Shared corrections retain identity, locking order and duplicate guards."""

from unittest.mock import Mock

import pytest

import receipt_patient_correction as correction
from admission_demographics import _snapshot_session
from tests.test_receipt_patient_correction import (
    Connection,
    receipt as receipt,
    attention as attention,
)


@pytest.mark.parametrize("changes", [{}, {"nombre": "OLD"}, {"fecha": "2026-10-05"}])
def test_unchanged_header_does_not_rewrite_dedup(receipt, changes):
    connection = Connection()
    guard = Mock()
    correction.update_header_key(connection, receipt, changes, "admin", guard)
    assert not connection.calls
    guard.assert_not_called()


@pytest.mark.parametrize(
    "changes",
    [
        {"fecha": "2026-10-06"},
        {"nombre": "NEW"},
        {"fecha": "2026-10-06", "nombre": "NEW"},
    ],
)
def test_duplicate_guard_sees_final_header_and_identity(receipt, changes):
    connection = Connection()
    guard = Mock(return_value="KEY")
    correction.update_header_key(connection, receipt, changes, "admin", guard)
    assert guard.call_args.kwargs["fecha"] == changes.get("fecha", receipt["fecha"])
    assert guard.call_args.kwargs["nombre"] == changes.get("nombre", receipt["nombre"])
    assert guard.call_args.kwargs["exclude_id"] == receipt["id"]
    assert connection.calls[0][1] == ("KEY", receipt["id"])


def test_duplicate_date_error_leaves_key_intact(receipt):
    connection = Connection()
    with pytest.raises(ValueError, match="duplicate"):
        correction.update_header_key(
            connection,
            receipt,
            {"fecha": "2026-10-06"},
            "admin",
            Mock(side_effect=ValueError("duplicate")),
        )
    assert not connection.calls


@pytest.mark.parametrize(
    "row",
    [None, {"admission_global_attention_id": None, "admission_atencion_id": None}],
)
def test_unlinked_receipt_does_not_lock_attention(row):
    connection = Connection(row)
    assert correction.lock_metadata_attention(connection, 1) is None
    assert len(connection.calls) == 1


def test_metadata_lock_has_same_order_as_name_correction(receipt, attention):
    connection = Connection(receipt, attention, attention)
    assert correction.lock_metadata_attention(connection, 1) == attention
    assert connection.calls[2][1] == ("admission-sync:attention:ATTENTION",)
    assert connection.calls[3][1] == ("admission-billing:SOURCE:7",)
    assert "FOR UPDATE" in connection.calls[4][0]


def test_unlinked_metadata_correction_does_not_query_admission(receipt):
    connection = Connection()
    receipt.update(admission_global_attention_id=None, admission_atencion_id=None)
    assert (
        correction.correct_linked_metadata(connection, receipt, None, {}, {}, "NOW")
        is None
    )
    assert not connection.calls


def test_invalid_current_link_is_rejected(receipt):
    connection = Connection()
    with pytest.raises(ValueError):
        correction.correct_linked_metadata(connection, receipt, None, {}, {}, "NOW")
    assert not connection.calls


@pytest.mark.parametrize("actor", [{}, {"username": "admin"}])
def test_linked_patch_updates_metadata_preserves_clock_and_records_actor(
    monkeypatch, receipt, attention, actor
):
    attention["server_revision"] = 1
    snapshot = {"snapshot_hash": "HASH", "source_updated_at": "NOW"}
    connection = Connection(attention, snapshot)
    patch = Mock()
    monkeypatch.setattr("admission_demographics._correct_snapshot", patch)
    changes = {
        "fecha": "2026-10-06",
        "numero_autorizacion": "00001234",
        "specialty_snapshot": "PEDIATRÍA",
        "insurance_document_type": "NO. CARNET",
        "insurance_document_number": "000ABC001",
    }
    assert (
        correction.correct_linked_metadata(
            connection, receipt, attention, changes, actor, "NOW"
        )
        == snapshot
    )
    payload = patch.call_args.kwargs["attention_patch"]
    assert payload["service_date"] == "2026-10-06"
    assert payload["detail_sheet"] == payload["specialty"] == "PEDIATRÍA"
    assert payload["insurance_document_number"] == "000ABC001"
    assert payload["receipt_correction_actor"] == actor.get("username", "Sistema")
    assert "created_at_effective_utc" not in payload and "turn_id" not in payload
    assert connection.calls[-1][1] == ("HASH", "NOW", 1)


def test_empty_or_national_patch_only_changes_supplied_fields():
    assert correction.linked_metadata_patch({}) == {}
    assert correction.linked_metadata_patch(
        {"admission_nss_snapshot": "000001", "admission_cedula_snapshot": "00000000001"}
    ) == {"nss": "000001", "cedula": "00000000001"}


def test_legacy_without_central_identity_does_not_block_metadata_lock(receipt):
    connection = Connection(receipt, None)
    assert correction.lock_metadata_attention(connection, 1) is None


def test_ordinary_metadata_edit_skips_unchanged_values(receipt, attention):
    receipt["numero_autorizacion"] = "00001234"
    connection = Connection()
    assert (
        correction.correct_edited_receipt_metadata(
            connection,
            receipt,
            attention,
            receipt["fecha"],
            receipt["numero_autorizacion"],
            {},
            "NOW",
        )
        is None
    )
    assert not connection.calls


def test_ordinary_metadata_edit_preserves_legacy_authorization_workflow(receipt):
    receipt["admission_global_attention_id"] = None
    connection = Connection()
    assert (
        correction.correct_edited_receipt_metadata(
            connection, receipt, None, receipt["fecha"], "00001234", {}, "NOW"
        )
        is None
    )
    assert not connection.calls


def test_ordinary_metadata_edit_only_patches_changed_values(
    monkeypatch, receipt, attention
):
    receipt["numero_autorizacion"] = "00001234"
    patch = Mock(return_value="RESULT")
    monkeypatch.setattr(correction, "correct_linked_metadata", patch)
    assert (
        correction.correct_edited_receipt_metadata(
            None, receipt, attention, "2026-10-06", "00001234", {}, "NOW"
        )
        == "RESULT"
    )
    assert patch.call_args.args[3] == {"fecha": "2026-10-06"}


def test_metadata_without_master_patient_identity_is_still_correctable(
    monkeypatch, receipt, attention
):
    attention.update(global_patient_id=None, server_revision=1)
    snapshot = {"snapshot_hash": "HASH", "source_updated_at": "NOW"}
    connection = Connection(attention, snapshot)
    patch = Mock()
    monkeypatch.setattr("admission_demographics._correct_snapshot", patch)
    assert (
        correction.correct_linked_metadata(
            connection,
            receipt,
            attention,
            {"numero_autorizacion": "00001234"},
            {},
            "NOW",
        )
        == snapshot
    )
    assert patch.call_args.kwargs["attention_patch"]["authorization"] == "00001234"


@pytest.mark.parametrize("value", ["ORIGINAL_SESSION", " ORIGINAL_SESSION "])
def test_existing_origin_session_does_not_query_or_reassign_shift(value):
    connection = Connection()
    assert (
        _snapshot_session(connection, {"operational_session_id": value}, {})
        == "ORIGINAL_SESSION"
    )
    assert not connection.calls


def test_legacy_origin_session_is_resolved_only_within_its_source():
    connection = Connection({"operational_session_id": "MATCHED_SESSION"})
    assert (
        _snapshot_session(connection, {}, {"operational_source_id": "SOURCE"})
        == "MATCHED_SESSION"
    )
    assert connection.calls[0][1] == ("SOURCE",)


def test_missing_origin_session_does_not_invent_identity():
    connection = Connection(None)
    with pytest.raises(ValueError, match="sesión de origen"):
        _snapshot_session(connection, {}, {"operational_source_id": "SOURCE"})
