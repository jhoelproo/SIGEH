"""Reuse a bounded query result while its centrally computed fingerprint matches."""

from copy import deepcopy
from threading import RLock
from typing import Any, Callable

from psycopg2.sql import SQL

QueryResult = tuple[list[Any], dict[str, float]]


class QuerySnapshotCache:
    """Keep one result in RAM; every refresh still checks the authoritative query."""

    def __init__(self):
        self._lock = RLock()
        self._identity: tuple | None = None
        self._fingerprint: str | None = None
        self._rows: list[Any] = []

    def clear(self):
        with self._lock:
            self._identity = None
            self._fingerprint = None
            self._rows = []

    def fetch(
        self, fetch_all: Callable[..., QueryResult], sql: str, params=(), **options
    ) -> QueryResult:
        with self._lock:
            return self._fetch(fetch_all, sql, tuple(params), options)

    def _fetch(self, fetch_all, sql, params, options) -> QueryResult:
        identity = (sql, params)
        probe = (
            SQL(
                "SELECT md5(COALESCE(jsonb_agg(sigeh_queue),'[]'::jsonb)::text) "
                "AS snapshot_fingerprint FROM ({query}) AS sigeh_queue"
            )
            .format(query=SQL(sql))
            .as_string(None)
        )
        headers, timings = fetch_all(probe, params, **options)
        fingerprint = self._read_fingerprint(headers)
        if identity != self._identity or fingerprint != self._fingerprint:
            rows, detail_timings = fetch_all(sql, params, **options)
            timings = {
                key: value + detail_timings[key] for key, value in timings.items()
            }
            self._rows = deepcopy(rows)
            self._identity = identity
            self._fingerprint = fingerprint
        return deepcopy(self._rows), timings

    @staticmethod
    def _read_fingerprint(headers) -> str:
        if len(headers) != 1:
            raise ValueError("La comprobación central no devolvió una huella única.")
        fingerprint = headers[0].get("snapshot_fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("La comprobación central devolvió una huella inválida.")
        return fingerprint
