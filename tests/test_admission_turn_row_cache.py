from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from admission_turn_row_cache import TurnRowCache


SQL = """SELECT p.* FROM admission_attention_projection p
WHERE p.operational_source_id::TEXT=%s AND p.turn_id=%s
ORDER BY p.attention_id"""


class Connection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.fail_details = False

    def execute(self, sql, params):
        self.calls.append((sql, params))
        if "AS row_fingerprint" in sql:
            rows = [
                {"attention_id": row["attention_id"], "row_fingerprint": repr(row)}
                for row in self.rows
            ]
        else:
            if self.fail_details:
                raise ConnectionError("offline")
            rows = [row for row in self.rows if row["attention_id"] in params[2]]
        return SimpleNamespace(fetchall=lambda: deepcopy(rows))


def test_unchanged_refresh_downloads_headers_only_and_returns_independent_rows():
    con = Connection([{"attention_id": 1, "latest_payload_json": {"name": "A"}}])
    cache = TurnRowCache()
    first = cache.load(con, "source", 1, SQL)
    first[0]["latest_payload_json"]["name"] = "mutated"
    assert cache.load(con, "source", 1, SQL)[0]["latest_payload_json"]["name"] == "A"
    assert len(con.calls) == 3
    assert "AS row_fingerprint" in con.calls[-1][0]


def test_updates_inserts_deletes_and_order_are_reflected():
    con = Connection([{"attention_id": 1, "value": "old"}, {"attention_id": 2}])
    cache = TurnRowCache()
    cache.load(con, "source", 1, SQL)
    con.rows = [{"attention_id": 3}, {"attention_id": 1, "value": "new"}]
    assert cache.load(con, "source", 1, SQL) == con.rows
    assert con.calls[-1][1][2] == [3, 1]
    con.rows = []
    assert cache.load(con, "source", 1, SQL) == []


def test_failed_download_does_not_confirm_fingerprint():
    con = Connection([{"attention_id": 1, "value": "old"}])
    cache = TurnRowCache()
    cache.load(con, "source", 1, SQL)
    con.rows[0]["value"] = "new"
    con.fail_details = True
    with pytest.raises(ConnectionError):
        cache.load(con, "source", 1, SQL)
    con.fail_details = False
    assert cache.load(con, "source", 1, SQL)[0]["value"] == "new"


def test_turn_identity_does_not_reuse_other_turn_rows():
    con = Connection([{"attention_id": 1, "value": "same"}])
    cache = TurnRowCache()
    cache.load(con, "source", 1, SQL)
    cache.load(con, "source", 2, SQL)
    cache.load(con, "other", 2, SQL)
    assert len(con.calls) == 6


def test_missing_detail_is_retried_instead_of_confirmed():
    con = Connection([{"attention_id": 1}])
    original = con.execute

    def missing(sql, params):
        cursor = original(sql, params)
        return (
            cursor
            if "AS row_fingerprint" in sql
            else SimpleNamespace(fetchall=lambda: [])
        )

    con.execute = missing
    cache = TurnRowCache()
    assert cache.load(con, "source", 1, SQL) == []
    con.execute = original
    assert cache.load(con, "source", 1, SQL) == [{"attention_id": 1}]


@pytest.mark.parametrize(
    "sql", ["SELECT 1", "SELECT p.* FROM admission_attention_projection p"]
)
def test_invalid_query_is_rejected_without_network_call(sql):
    con = Connection([])
    with pytest.raises(ValueError):
        TurnRowCache().load(con, "source", 1, sql)
    assert con.calls == []


def test_header_failure_does_not_return_cache_as_current():
    con = Connection([{"attention_id": 1}])
    cache = TurnRowCache()
    cache.load(con, "source", 1, SQL)
    con.execute = lambda *_: (_ for _ in ()).throw(ConnectionError("offline"))
    with pytest.raises(ConnectionError):
        cache.load(con, "source", 1, SQL)


def test_concurrent_refreshes_download_details_only_once():
    con = Connection([{"attention_id": 1}])
    cache = TurnRowCache()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: cache.load(con, "source", 1, SQL), range(20)))
    assert results == [[{"attention_id": 1}]] * 20
    assert sum("AS row_fingerprint" not in sql for sql, _ in con.calls) == 1


def test_identity_is_parameterized_and_invalid_turn_does_not_query():
    con = Connection([])
    source = "source' OR TRUE--"
    cache = TurnRowCache()
    assert cache.load(con, source, 1, SQL) == []
    sql, params = con.calls[0]
    assert source not in sql
    assert params == (source, 1)
    with pytest.raises(ValueError):
        cache.load(con, "source", "invalid", SQL)
    assert len(con.calls) == 1
