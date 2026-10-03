"""Refresh a central turn using fingerprints before downloading changed rows."""

from copy import deepcopy
from threading import RLock


class TurnRowCache:
    """Keep one turn in memory; PostgreSQL remains authoritative on every read."""

    def __init__(self):
        self._lock = RLock()
        self._identity = None
        self._rows = {}
        self._fingerprints = {}

    def load(self, connection, source_id, turn_id, sql):
        with self._lock:
            return self._load(connection, source_id, turn_id, sql)

    def _load(self, connection, source_id, turn_id, sql):
        identity = (str(source_id), int(turn_id))
        header_sql, detail_sql = self._queries(sql)
        headers = [
            dict(row) for row in connection.execute(header_sql, identity).fetchall()
        ]
        fingerprints = {
            int(row["attention_id"]): str(row["row_fingerprint"]) for row in headers
        }
        previous = self._fingerprints if self._identity == identity else {}
        changed = [
            key for key, value in fingerprints.items() if previous.get(key) != value
        ]
        rows = dict(self._rows) if self._identity == identity else {}
        self._download_changes(connection, identity, detail_sql, changed, rows)
        self._confirm(identity, fingerprints, rows)
        return deepcopy(list(self._rows.values()))

    @staticmethod
    def _download_changes(connection, identity, detail_sql, changed, rows):
        if changed:
            downloaded = connection.execute(detail_sql, (*identity, changed)).fetchall()
            for key in changed:
                rows.pop(key, None)
            rows.update({int(row["attention_id"]): dict(row) for row in downloaded})

    def _confirm(self, identity, fingerprints, rows):
        self._identity = identity
        self._rows = {key: rows[key] for key in fingerprints if key in rows}
        self._fingerprints = {key: fingerprints[key] for key in self._rows}

    @staticmethod
    def _queries(sql):
        select, separator, clauses = sql.partition(
            "FROM admission_attention_projection p"
        )
        if not separator or "ORDER BY" not in clauses:
            raise ValueError(
                "La consulta del turno requiere proyección y orden explícitos."
            )
        header_sql = (
            "SELECT p.attention_id,md5(to_jsonb(p)::TEXT) AS row_fingerprint "
            + separator
            + clauses
        )
        where, order, ordering = clauses.partition("ORDER BY")
        detail_sql = (
            select
            + separator
            + where
            + " AND p.attention_id=ANY(%s) "
            + order
            + ordering
        )
        return header_sql, detail_sql
