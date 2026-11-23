from concurrent.futures import ThreadPoolExecutor
from datetime import date
import sqlite3
from unittest.mock import Mock

import pytest

from transfer_budget import AdaptivePoll, TransferMeter, MeasuredCursor, billing_period


def test_batch_flushes_at_size_and_snapshot_without_losing_cycles(tmp_path):
    meter = TransferMeter(tmp_path / "usage.db", batch_size=3)
    previous, current = date(2026, 9, 30), date(2026, 10, 1)
    meter.record("attention", requests=1, today=previous)
    meter.record("patients", response_bytes=20, today=current)
    assert not meter.path.exists()
    meter.record("patients", response_bytes=30, today=current)

    observer = TransferMeter(meter.path)
    assert observer.snapshot(previous)["requests"] == 1
    assert observer.snapshot(current)["response_bytes_estimated"] == 50
    meter.record("unknown", response_bytes=7, today=current)
    assert meter.snapshot(current)["streams"]["other"]["response_bytes_estimated"] == 7
    assert observer.snapshot(current)["response_bytes_estimated"] == 57


def test_small_batch_flushes_when_the_next_record_reaches_the_interval(tmp_path):
    now = [0.0]
    meter = TransferMeter(tmp_path / "usage.db", batch_size=20, clock=lambda: now[0])
    meter.record("other")
    now[0] = 29.99
    meter.record("other", requests=1)
    now[0] = 59.98
    meter.record("other", requests=1)
    assert not meter.path.exists()
    now[0] = 60
    meter.record("other", requests=1)
    assert TransferMeter(meter.path).snapshot()["requests"] == 3


def test_failed_flush_rolls_back_and_retries_all_counts(tmp_path, monkeypatch):
    now = [0.0]
    meter = TransferMeter(tmp_path / "usage.db", batch_size=2, clock=lambda: now[0])
    original = meter._record_alert
    fail = Mock(side_effect=sqlite3.OperationalError("synthetic disk I/O error"))
    monkeypatch.setattr(meter, "_record_alert", fail)
    meter.record("other", responses=1)
    meter.record("other", responses=2)
    assert TransferMeter(meter.path).snapshot()["responses"] == 0
    meter.record("other", responses=4)
    assert meter.snapshot()["available"] is False
    assert fail.call_count == 1

    monkeypatch.setattr(meter, "_record_alert", original)
    now[0] = 60
    meter.record("other", responses=8)
    assert meter.flush() is True
    assert meter.flush() is True
    assert TransferMeter(meter.path).snapshot()["responses"] == 15


def test_snapshot_retries_a_read_failure_only_after_backoff(tmp_path, monkeypatch):
    now = [0.0]
    meter = TransferMeter(tmp_path / "usage.db", clock=lambda: now[0])
    original = meter._connect
    failure = Mock(side_effect=OSError("synthetic read failure"))
    monkeypatch.setattr(meter, "_connect", failure)
    assert meter.snapshot()["available"] is False
    now[0] = 59.99
    assert meter.snapshot()["available"] is False
    failure.assert_called_once()
    monkeypatch.setattr(meter, "_connect", original)
    now[0] = 60
    assert meter.snapshot()["available"] is True


def test_concurrent_records_keep_exact_totals(tmp_path):
    meter = TransferMeter(tmp_path / "usage.db", batch_size=8)
    with ThreadPoolExecutor(max_workers=4) as workers:
        list(workers.map(lambda _: meter.record("projection", rows=2), range(103)))
    assert meter.snapshot()["rows"] == 206
    assert TransferMeter(meter.path).snapshot()["rows"] == 206


def test_production_meter_buffers_and_registers_normal_exit_flush(
    tmp_path, monkeypatch
):
    import transfer_budget as module

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(module, "_METER", None)
    register = Mock()
    monkeypatch.setattr("atexit.register", register)
    meter = module.get_transfer_meter()
    meter.record("other", requests=1)
    assert not meter.path.exists()
    register.assert_called_once_with(meter.flush)
    register.call_args.args[0]()
    assert TransferMeter(meter.path).snapshot()["requests"] == 1


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
    assert first.allowance == 5_000_000_000 // 3
    assert module.get_transfer_meter() is first
    config = tmp_path / "SIGEH" / "telemetry" / "budget.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"stations":2}')
    monkeypatch.setattr(module, "_METER", None)
    assert module.get_transfer_meter().allowance == 2_500_000_000
    config.write_text("invalid")
    monkeypatch.setattr(module, "_METER", None)
    assert module.get_transfer_meter().allowance == 5_000_000_000 // 3


def test_partial_budget_config_preserves_three_station_default(tmp_path, monkeypatch):
    import transfer_budget as module

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(module, "_METER", None)
    config = tmp_path / "SIGEH" / "telemetry" / "budget.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"start_day":2}')
    meter = module.get_transfer_meter()
    assert meter.start_day == 2
    assert meter.allowance == 5_000_000_000 // 3


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
