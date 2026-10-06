"""Query snapshots must preserve edits, permissions, order and failure semantics."""

from copy import deepcopy
import hashlib
import json

import pytest

from query_snapshot_cache import QuerySnapshotCache

SQL = "SELECT attention_id,patient_name FROM queue WHERE owner=%s ORDER BY attention_id"


class Reader:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []
        self.fail_probe = False
        self.fail_details = False

    def fetch_all(self, sql, params=(), **options):
        self.calls.append((sql, params, options))
        probe = "AS snapshot_fingerprint" in sql
        if (probe and self.fail_probe) or (not probe and self.fail_details):
            raise RuntimeError("Unavailable")
        result = deepcopy(self.rows)
        if probe:
            fingerprint = hashlib.sha256(
                json.dumps(result, sort_keys=True).encode()
            ).hexdigest()
            result = [{"snapshot_fingerprint": fingerprint}]
        return result, {"connection_ms": 1.0, "query_ms": 2.0, "elapsed_ms": 3.0}

    def details(self):
        return [call for call in self.calls if "AS snapshot_fingerprint" not in call[0]]


def test_unchanged_queue_downloads_details_only_once_and_returns_independent_rows():
    reader = Reader([{"attention_id": 1, "patient_name": "SYNTHETIC"}])
    cache = QuerySnapshotCache()
    rows, _ = cache.fetch(reader.fetch_all, SQL, ("owner",), operation="queue")
    rows[0]["patient_name"] = "LOCAL MUTATION"
    refreshed, _ = cache.fetch(reader.fetch_all, SQL, ("owner",), operation="queue")
    assert refreshed == reader.rows
    assert len(reader.details()) == 1


@pytest.mark.parametrize(
    "changed", [[], [{"attention_id": 2}], [{"attention_id": 1, "nss": "001"}]]
)
def test_changed_queue_and_empty_transitions_download_fresh_details(changed):
    reader = Reader([{"attention_id": 1}])
    cache = QuerySnapshotCache()
    cache.fetch(reader.fetch_all, SQL, ("owner",))
    reader.rows = changed
    assert cache.fetch(reader.fetch_all, SQL, ("owner",))[0] == changed
    assert len(reader.details()) == 2


def test_empty_snapshot_is_cached():
    reader = Reader()
    cache = QuerySnapshotCache()
    for _ in range(3):
        assert cache.fetch(reader.fetch_all, SQL)[0] == []
    assert len(reader.details()) == 1


def test_different_parameters_query_or_explicit_clear_never_reuse_a_snapshot():
    reader = Reader([{"attention_id": 1}])
    cache = QuerySnapshotCache()
    cache.fetch(reader.fetch_all, SQL, ("a",))
    cache.fetch(reader.fetch_all, SQL, ("b",))
    cache.fetch(reader.fetch_all, SQL + " LIMIT 1", ("b",))
    cache.clear()
    cache.fetch(reader.fetch_all, SQL + " LIMIT 1", ("b",))
    assert len(reader.details()) == 4


def test_reordering_invalidates_snapshot():
    reader = Reader([{"attention_id": 1}, {"attention_id": 2}])
    cache = QuerySnapshotCache()
    cache.fetch(reader.fetch_all, SQL)
    reader.rows.reverse()
    assert cache.fetch(reader.fetch_all, SQL)[0] == reader.rows
    assert len(reader.details()) == 2


@pytest.mark.parametrize("stage", ["probe", "details"])
def test_failures_propagate_and_do_not_replace_the_previous_snapshot(stage):
    reader = Reader([{"attention_id": 1}])
    cache = QuerySnapshotCache()
    cache.fetch(reader.fetch_all, SQL)
    reader.rows = [{"attention_id": 2}]
    setattr(reader, "fail_" + stage, True)
    with pytest.raises(RuntimeError, match="Unavailable"):
        cache.fetch(reader.fetch_all, SQL)
    setattr(reader, "fail_" + stage, False)
    assert cache.fetch(reader.fetch_all, SQL)[0] == reader.rows


def test_probe_preserves_bound_parameters_and_query_options():
    reader = Reader()
    cache = QuerySnapshotCache()
    params = ("O'Brien",)
    options = {
        "operation": "queue",
        "sql_stage": "ELIGIBILITY_QUERY",
        "current_user": {"role": "admin"},
    }
    cache.fetch(reader.fetch_all, SQL, params, **options)
    assert "O'Brien" not in reader.calls[0][0]
    assert reader.calls[0][1:] == (params, options)


@pytest.mark.parametrize(
    "headers",
    [
        [],
        [{}, {}],
        [{}],
        [{"snapshot_fingerprint": None}],
        [{"snapshot_fingerprint": ""}],
    ],
)
def test_invalid_header_never_serves_stale_data(headers):
    cache = QuerySnapshotCache()
    with pytest.raises(ValueError):
        cache.fetch(lambda *_args, **_options: (headers, {}), SQL)


def test_timings_include_both_queries_only_when_details_are_downloaded():
    reader = Reader()
    cache = QuerySnapshotCache()
    assert cache.fetch(reader.fetch_all, SQL)[1]["elapsed_ms"] == 6.0
    assert cache.fetch(reader.fetch_all, SQL)[1]["elapsed_ms"] == 3.0
