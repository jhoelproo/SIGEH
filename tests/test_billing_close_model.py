import pytest

from billing_close_model import build_close_snapshot


def snapshot(admissions=(), receipts=()):
    return build_close_snapshot(
        source="source",
        turn=3,
        previous_turn=2,
        started_at="2026-09-25T08:00:00-04:00",
        closed_at="2026-09-26T08:00:00-04:00",
        admissions=admissions,
        receipts=receipts,
    )


def admission(identity, turn=2, created="2026-09-24T10:00:00-04:00", **extra):
    return {
        "id": str(identity),
        "turn_id": turn,
        "created_at": created,
        "ars": "ARS",
        **extra,
    }


def receipt(identity, attention=None, authorization="", **extra):
    return {
        "id": identity,
        "attention_id": attention,
        "authorization": authorization,
        "created_at": "2026-09-25T12:00:00-04:00",
        "ars": "ARS",
        **extra,
    }


def test_prebaseline_backlog_is_excluded_without_mutation():
    rows = [admission(i, created="2026-09-22T10:00:00-04:00") for i in range(500)]
    assert snapshot(rows)["total_pending"] == 0
    assert len(rows) == 500


def test_other_stations_reduce_pending_and_keep_old_cohorts_separate():
    rows = [admission(i) for i in range(8)] + [admission("older", turn=1)]
    assert snapshot(rows)["pending_previous"] == 8
    result = snapshot(rows, [receipt(i, str(i)) for i in range(5)])
    assert result["pending_previous"] == 3
    assert result["pending_historical"] == 1
    assert result["total_pending"] == 4
    assert result["linked_billed"] == 5
    assert result["previous_received"] == 8
    assert result["previous_resolved"] == 5
    assert result["historical_received"] == 1
    assert result["historical_resolved"] == 0


def test_historical_authorizations_are_a_subset_and_do_not_resolve_admissions():
    result = snapshot(
        [admission("pending")],
        [
            receipt(1, authorization="1234"),
            receipt(2, authorization="5678"),
            receipt(3, authorization="9999"),
            receipt(4),
        ],
    )
    assert result["historical_billed"] == result["receipt_count"] == 4
    assert result["historical_authorized"] == 3
    assert result["pending_previous"] == 1


def test_claim_is_pending_but_persisted_preliminary_receipt_resolves_it():
    rows = [admission("one", turn=3, claimed=True)]
    assert snapshot(rows)["pending_current"] == 1
    result = snapshot(rows, [receipt(1, "one")])
    assert result["pending_current"] == 0
    assert result["linked_current"] == result["linked_billed"] == 1
    assert result["authorized"] == 0


def test_fifty_canonical_receipts_are_not_zero_and_versions_not_duplicated():
    rows = [receipt(i) for i in range(50)]
    result = snapshot(receipts=rows + [rows[0]])
    assert result["receipt_count"] == 50
    assert result["by_ars"][0]["worked"] == 50


def test_excluded_and_deleted_do_not_count_as_billed_or_pending():
    result = snapshot(
        [admission("one", excluded=True)],
        [
            receipt(1, "one", attention_excluded=True),
            receipt(2, deleted=True),
            receipt(3, invalid=True),
        ],
    )
    assert result["receipt_count"] == result["total_pending"] == 0


def test_report_adapter_keeps_receipts_and_authorizations_distinct():
    from billing_close_report import report_data

    canonical = snapshot(
        [admission("one")], [receipt(1, "one"), receipt(2, authorization="1234")]
    )
    report = report_data(canonical, {"turn_id": 3})
    assert report["invoiced"] == 2
    assert report["authorized_applicable"] == 1
    assert report["inherited_received"] == report["inherited_authorized"] == 1
    assert report["pending_next"] == 0
    assert report["authorization_rate"] == 50
    assert report["details"] == []


def test_receipt_before_turn_reduces_backlog_but_is_not_worked_this_turn():
    result = snapshot(
        [admission("one")], [receipt(1, "one", created_at="2026-09-24T12:00:00-04:00")]
    )
    assert result["receipt_count"] == result["total_pending"] == 0


def test_invalid_timestamp_and_conflicting_effective_versions_are_errors():
    with pytest.raises(ValueError):
        snapshot(receipts=[receipt(1, created_at="invalid")])
    with pytest.raises(ValueError, match="versiones"):
        snapshot(receipts=[receipt(1), receipt(1, authorization="1234")])


def test_future_admission_is_not_part_of_closed_turn():
    result = snapshot(
        [admission("future", turn=3, created="2026-09-26T08:00:00-04:00")]
    )
    assert result["current_admissions"] == result["total_pending"] == 0


def test_known_excluded_attention_cannot_be_counted_by_missing_receipt_flag():
    result = snapshot([admission("one", excluded=True)], [receipt(1, "one")])
    assert result["receipt_count"] == 0


def test_amounts_use_exact_currency_and_only_worked_receipts():
    result = snapshot(receipts=[receipt(1, total="6.00"), receipt(2, total="5.99")])
    assert result["amount"] == "11.99"


def test_independent_database_count_must_match():
    with pytest.raises(ValueError, match="cantidad central"):
        build_close_snapshot(
            source="source",
            turn=3,
            previous_turn=2,
            started_at="2026-09-25T08:00:00-04:00",
            closed_at="2026-09-26T08:00:00-04:00",
            admissions=[],
            receipts=[receipt(1)],
            canonical_receipt_count=50,
        )


def test_close_before_start_is_invalid():
    with pytest.raises(ValueError, match="preceder"):
        build_close_snapshot(
            source="source",
            turn=3,
            previous_turn=2,
            started_at="2026-09-26T08:00:00-04:00",
            closed_at="2026-09-25T08:00:00-04:00",
            admissions=[],
            receipts=[],
        )


def test_snapshot_log_contains_counts_without_patient_data(caplog):
    from billing_close_report import log_snapshot

    data = snapshot([admission("private-id", name="PRIVATE NAME")], [receipt(1)])
    with caplog.at_level("INFO", logger="hospital.billing.close"):
        log_snapshot(data, "2026-09-26T08:00:00-04:00")
    assert "CLOSE_REPORT_SNAPSHOT" in caplog.text
    assert '"receipt_count": 1' in caplog.text
    assert "PRIVATE NAME" not in caplog.text
    assert "private-id" not in caplog.text
