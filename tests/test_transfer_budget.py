from datetime import date
from unittest.mock import Mock

import pytest

from transfer_budget import AdaptivePoll, TransferMeter, MeasuredCursor, billing_period


@pytest.mark.parametrize(
    "day,expected",
    [(1, "2026-09-01"), (20, "2026-09-20"), (21, "2026-08-21"), (28, "2026-08-28")],
)
def test_billing_period(day, expected):
    assert billing_period(date(2026, 9, 20), day) == expected


@pytest.mark.parametrize("day", [0, 32, -1])
def test_invalid_period_day(day):
    with pytest.raises(ValueError):
        billing_period(date(2026, 9, 20), day)


def test_budget_persists_thresholds_without_patient_data(tmp_path):
    path = tmp_path / "usage.db"
    meter = TransferMeter(path, quota_bytes=1000, stations=2)
    for amount, expected in [(249, 0), (1, 50), (100, 70), (75, 85), (100, 85)]:
        meter.record("attention", response_bytes=amount, today=date(2026, 9, 20))
        assert meter.snapshot(date(2026, 9, 20))["alert_percent"] == expected
    again = TransferMeter(path, quota_bytes=1000, stations=2)
    assert again.snapshot(date(2026, 9, 20))["response_bytes_estimated"] == 525
    assert again.snapshot(date(2026, 10, 1))["response_bytes_estimated"] == 0
    assert again.background_delay(date(2026, 9, 20)) > 30


def test_meter_failure_never_blocks_work(tmp_path):
    meter = TransferMeter(tmp_path / "invalid" / "usage.db")
    meter.path.parent.mkdir()
    meter.path.mkdir()
    meter.record("other", requests=1)
    assert meter.snapshot()["available"] is False


def test_cursor_counts_each_fetched_row_and_no_raw_data(tmp_path):
    meter = TransferMeter(tmp_path / "usage.db")
    raw = Mock()
    raw.fetchone.side_effect = [("PRIVATE_NAME",), None]
    raw.fetchall.return_value = [("private",), ("second",)]
    raw.fetchmany.return_value = [("third",)]
    cursor = MeasuredCursor(raw, meter, "attention")
    assert cursor.fetchone() == ("PRIVATE_NAME",)
    assert cursor.fetchone() is None
    assert len(cursor.fetchall()) == 2
    assert len(cursor.fetchmany(1)) == 1
    snapshot = meter.snapshot()
    assert snapshot["rows"] == 4
    assert snapshot["response_bytes_estimated"] > 0
    assert "PRIVATE_NAME" not in meter.path.read_bytes().decode(errors="ignore")


def test_adaptive_poll_keeps_bounded_latency_and_activity_resets():
    poll = AdaptivePoll(minimum=10, maximum=30)
    assert [poll.completed(changed=False) for _ in range(4)] == [20, 30, 30, 30]
    assert poll.completed(changed=True) == 10


def test_period_handles_year_and_short_months():
    assert billing_period(date(2026, 1, 1), 31) == "2025-12-31"
    assert billing_period(date(2024, 2, 29), 31) == "2024-02-29"


@pytest.mark.parametrize(
    "options", [{"quota_bytes": 0}, {"stations": 0}, {"quota_bytes": 1, "stations": 2}]
)
def test_invalid_budget(options, tmp_path):
    with pytest.raises(ValueError):
        TransferMeter(tmp_path / "usage.db", **options)


@pytest.mark.parametrize(
    "query,expected",
    [
        ("SELECT * FROM admission_sync_events", "attention"),
        ("select * from admission_patient_directory_events", "patients"),
        ("select id from admission_attention_projection", "projection"),
        ("SELECT 1", "other"),
    ],
)
def test_query_classification(query, expected):
    from transfer_budget import query_stream

    assert query_stream(query) == expected


def test_cursor_iteration_and_attributes(tmp_path):
    meter = TransferMeter(tmp_path / "usage.db")
    raw = Mock(rowcount=2)
    raw.fetchone.side_effect = [(b"abc",), (None,), None]
    raw.fetchmany.return_value = []
    cursor = MeasuredCursor(raw, meter, "other")
    assert list(cursor) == [(b"abc",), (None,)]
    assert cursor.rowcount == 2
    assert cursor.fetchmany() == []
    assert meter.snapshot()["rows"] == 2


def test_meter_singleton_valid_and_invalid_config(tmp_path, monkeypatch):
    import transfer_budget as module

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(module, "_METER", None)
    first = module.get_transfer_meter()
    assert module.get_transfer_meter() is first
    config = tmp_path / "SIGEH" / "telemetry" / "budget.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"stations":2}')
    monkeypatch.setattr(module, "_METER", None)
    assert module.get_transfer_meter().allowance == 2_500_000_000
    config.write_text("invalid")
    monkeypatch.setattr(module, "_METER", None)
    assert module.get_transfer_meter().allowance == 5_000_000_000


def test_aggregation_and_export(tmp_path, monkeypatch):
    import json
    import transfer_usage
    from transfer_budget import aggregate_snapshots

    meter = TransferMeter(tmp_path / "usage.db")
    meter.record("other", response_bytes=850)
    snapshot = meter.snapshot()
    old = {**snapshot, "captured_at": "2000", "response_bytes_estimated": 0}
    assert aggregate_snapshots([old, snapshot, old], 1000)["alert_percent"] == 85
    assert aggregate_snapshots([], 1000)["stations"] == 0
    with pytest.raises(ValueError, match="ciclos"):
        aggregate_snapshots(
            [snapshot, {**snapshot, "station_id": "other", "period": "other"}]
        )
    with pytest.raises(ValueError, match="disponible"):
        aggregate_snapshots([{"available": False}])
    monkeypatch.setattr(transfer_usage, "get_transfer_meter", lambda: meter)
    output = tmp_path / "export.json"
    assert transfer_usage.main(["--output", str(output)]) == 0
    combined = tmp_path / "combined.json"
    assert (
        transfer_usage.main(
            ["--output", str(combined), "--combine", str(output), str(output)]
        )
        == 0
    )
    assert json.loads(combined.read_text())["stations"] == 1


def test_poll_rejects_invalid_bounds():
    with pytest.raises(ValueError):
        AdaptivePoll(30, 10)


def test_export_script_entry_point(tmp_path, monkeypatch):
    import runpy
    import sys
    import transfer_budget

    meter = TransferMeter(tmp_path / "usage.db")
    monkeypatch.setattr(transfer_budget, "get_transfer_meter", lambda: meter)
    monkeypatch.setattr(
        sys, "argv", ["transfer_usage.py", "--output", str(tmp_path / "usage.json")]
    )
    with pytest.raises(SystemExit) as result:
        runpy.run_module("transfer_usage", run_name="__main__")
    assert result.value.code == 0
