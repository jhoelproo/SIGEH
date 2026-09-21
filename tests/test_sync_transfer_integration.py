from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_hybrid import AdmissionCloudRepository
from patient_directory import CentralPatientDirectoryRepository
from transfer_budget import TransferMeter, history_page_window


class Connection:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query, params=()):
        self.query, self.params = query, params
        return SimpleNamespace(
            fetchall=lambda: self.rows, fetchone=lambda: self.rows[0]
        )


def test_directory_header_query_has_no_payload_and_is_bounded():
    connection = Connection([{"sequence": 25001, "global_patient_id": "id"}])
    repo = CentralPatientDirectoryRepository(lambda: connection)
    assert repo.event_headers_after(25000, limit=5000)[0]["sequence"] == 25001
    assert connection.params == (25000, 500)
    assert "payload_json" not in connection.query
    repo.event_headers_after(-1, limit=0)
    assert connection.params == (0, 1)


def test_directory_targeted_snapshot_preserves_deleted_and_identity():
    connection = Connection(
        [
            {
                "global_patient_id": "id",
                "is_deleted": True,
                "deleted_at": "2026-09-20",
                "server_revision": 3,
            }
        ]
    )
    repo = CentralPatientDirectoryRepository(lambda: connection)
    assert repo.snapshots_for_ids([]) == []
    assert connection.query == ""
    rows = repo.snapshots_for_ids(["id"])
    assert rows[0]["is_deleted"] is True
    assert rows[0]["deleted_at"] == "2026-09-20"
    assert "SELECT *" not in connection.query
    assert connection.params == (["id"],)


@pytest.mark.parametrize("size,total", [(1, 0), (1, 1), (2, 3), (500, 501)])
def test_turn_projection_pages_do_not_truncate(size, total):
    repo = AdmissionCloudRepository(lambda: None)
    rows = [{"sequence": i} for i in range(total)]
    calls = []

    def page(**kwargs):
        calls.append(kwargs)
        offset = kwargs["offset"]
        return rows[offset : offset + kwargs["limit"]]

    repo.current_turn_attention_events = page
    pages = list(
        repo.current_turn_attention_pages(
            operational_source_id="source", turn_id=77, limit=size
        )
    )
    assert [row for page in pages for row in page] == rows
    assert all(call["turn_id"] == 77 for call in calls)
    assert all(call["operational_source_id"] == "source" for call in calls)


def test_window_heads_survive_event_compaction():
    connection = Connection(
        [
            {
                "minimum_available_sequence": 25000,
                "checkpoint_sequence": 25000,
                "latest_sequence": 25000,
            }
        ]
    )
    for cls in (AdmissionCloudRepository, CentralPatientDirectoryRepository):
        window = cls(lambda: connection).event_window()
        assert window["latest_sequence"] == 25000
        assert "GREATEST(f.checkpoint_sequence" in connection.query


def test_incremental_attention_query_uses_confirmed_sequence():
    connection = Connection()
    repo = AdmissionCloudRepository(lambda: connection)
    assert repo.events_after(25000, limit=5000) == []
    assert connection.params == (25000, 500)
    assert "SELECT *" not in connection.query


@pytest.mark.parametrize(
    "pending,expected", [(False, (50, 250, 0)), (True, (300, 0, 250))]
)
def test_history_merge_window_keeps_pending_rows(pending, expected):
    assert history_page_window(50, 250, pending) == expected


def test_postgres_wrapper_keeps_results_and_measures_failures(tmp_path, monkeypatch):
    import CALCULOS_QT as app
    import transfer_budget

    meter = TransferMeter(tmp_path / "usage.db")
    monkeypatch.setattr(transfer_budget, "get_transfer_meter", lambda: meter)
    raw = Mock(query=b"SELECT value")
    raw.fetchall.return_value = [("result",)]
    wrapper = object.__new__(app.PostgresWrapper)
    wrapper.con = Mock()
    wrapper.con.cursor.return_value = raw
    assert wrapper.execute("SELECT value").fetchall() == [("result",)]
    raw.execute.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        wrapper.execute("SELECT value")
    summary = meter.snapshot()
    assert summary["requests"] == 2
    assert summary["responses"] == 1
    assert summary["rows"] == 1


def test_budget_exceeded_or_unavailable_does_not_stop_database(tmp_path, monkeypatch):
    import CALCULOS_QT as app
    import transfer_budget

    meter = TransferMeter(tmp_path / "usage.db", quota_bytes=1)
    meter.record("other", response_bytes=100)
    monkeypatch.setattr(transfer_budget, "get_transfer_meter", lambda: meter)
    raw = Mock(query=b"SELECT value")
    raw.fetchall.return_value = [("result",)]
    wrapper = object.__new__(app.PostgresWrapper)
    wrapper.con = Mock()
    wrapper.con.cursor.return_value = raw
    assert wrapper.execute("SELECT value").fetchall() == [("result",)]
    meter.path = tmp_path  # filesystem failure in the optional meter
    assert wrapper.execute("SELECT value").fetchall() == [("result",)]
    assert raw.execute.call_count == 2
