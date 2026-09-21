"""Local payload estimates, without SQL text, credentials or clinical data.

These counters measure fetched application payload, not billed network traffic.
Raw driver calls, TLS/protocol overhead and other cloud clients are excluded.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import uuid
from calendar import monthrange
from contextlib import closing
from datetime import date, datetime, timezone
from pathlib import Path

LOG = logging.getLogger("hospital.transfer")
DEFAULT_QUOTA_BYTES = 5_000_000_000
STREAMS = ("attention", "patients", "projection", "other")
_METER = None
_METER_LOCK = threading.Lock()


def get_transfer_meter():
    global _METER
    with _METER_LOCK:
        if _METER is None:
            root = (
                Path(os.environ.get("LOCALAPPDATA", Path.home()))
                / "SIGEH"
                / "telemetry"
            )
            options = {}
            config = root / "budget.json"
            try:
                if config.exists():
                    options = json.loads(config.read_text(encoding="utf-8"))
                _METER = TransferMeter(root / "transfer.sqlite", **options)
            except (OSError, ValueError, TypeError):
                LOG.warning(
                    "TRANSFER_BUDGET_CONFIG_INVALID: usando presupuesto predeterminado"
                )
                _METER = TransferMeter(root / "transfer.sqlite")
        return _METER


def billing_period(today: date, start_day: int = 1) -> str:
    if not 1 <= start_day <= 31:
        raise ValueError("El día inicial debe estar entre 1 y 31.")
    year, month = today.year, today.month
    if today.day < min(start_day, monthrange(year, month)[1]):
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return date(year, month, min(start_day, monthrange(year, month)[1])).isoformat()


def payload_bytes(value):
    if isinstance(value, (bytes, bytearray, memoryview)):
        return len(value)
    return len(json.dumps(value, default=str, ensure_ascii=False).encode("utf-8"))


def query_stream(query) -> str:
    text = str(query).lower()
    for table, stream in (
        ("admission_patient_directory", "patients"),
        ("admission_sync_events", "attention"),
        ("admission_attention_projection", "projection"),
    ):
        if table in text:
            return stream
    return "other"


def history_page_window(limit, offset, has_pending):
    if has_pending:
        return limit + offset, 0, offset
    return limit, offset, 0


class TransferMeter:
    """Durable per-station counters. Equal station allowances are configurable."""

    def __init__(
        self, path, *, quota_bytes=DEFAULT_QUOTA_BYTES, stations=1, start_day=1
    ):
        if quota_bytes <= 0 or stations < 1 or stations > quota_bytes:
            raise ValueError("Cuota y número de estaciones deben ser positivos.")
        billing_period(date.today(), start_day)
        self.path = Path(path)
        self.allowance = quota_bytes // stations
        self.start_day = start_day
        self._ready = False
        self._warned = False

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=1)
        if not self._ready:
            connection.execute("""CREATE TABLE IF NOT EXISTS transfer_usage(
                period TEXT NOT NULL, stream TEXT NOT NULL,
                requests INTEGER NOT NULL DEFAULT 0, responses INTEGER NOT NULL DEFAULT 0,
                rows INTEGER NOT NULL DEFAULT 0, request_bytes INTEGER NOT NULL DEFAULT 0,
                response_bytes INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(period,stream))""")
            connection.execute("""CREATE TABLE IF NOT EXISTS transfer_alerts(
                period TEXT PRIMARY KEY, level INTEGER NOT NULL)""")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS transfer_identity(singleton INTEGER PRIMARY KEY CHECK(singleton=1), station_id TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT OR IGNORE INTO transfer_identity VALUES(1,?)",
                (str(uuid.uuid4()),),
            )
            connection.commit()
            self._ready = True
        return connection

    def _unavailable(self):
        if not self._warned:
            LOG.warning("TRANSFER_METER_UNAVAILABLE: atención y facturación continúan")
            self._warned = True

    def record(
        self,
        stream,
        *,
        requests=0,
        responses=0,
        rows=0,
        request_bytes=0,
        response_bytes=0,
        today=None,
    ):
        period = billing_period(today or date.today(), self.start_day)
        stream = stream if stream in STREAMS else "other"
        values = tuple(
            max(0, int(v))
            for v in (requests, responses, rows, request_bytes, response_bytes)
        )
        try:
            with closing(self._connect()) as con, con:
                con.execute(
                    """INSERT INTO transfer_usage VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(period,stream) DO UPDATE SET
                    requests=requests+excluded.requests, responses=responses+excluded.responses,
                    rows=rows+excluded.rows, request_bytes=request_bytes+excluded.request_bytes,
                    response_bytes=response_bytes+excluded.response_bytes""",
                    (period, stream, *values),
                )
                self._record_alert(con, period)
        except (OSError, sqlite3.Error):
            self._unavailable()

    def _record_alert(self, con, period):
        total = con.execute(
            "SELECT SUM(response_bytes) FROM transfer_usage WHERE period=?", (period,)
        ).fetchone()[0]
        level = self._level(total or 0)
        previous = con.execute(
            "SELECT level FROM transfer_alerts WHERE period=?", (period,)
        ).fetchone()
        if level and (not previous or level > previous[0]):
            con.execute(
                "INSERT INTO transfer_alerts VALUES(?,?) ON CONFLICT(period) DO UPDATE SET level=excluded.level",
                (period, level),
            )
            LOG.warning(
                "TRANSFER_BUDGET_ALERT period=%s level=%s estimated_bytes=%s station_allowance=%s",
                period,
                level,
                total,
                self.allowance,
            )

    def _level(self, total):
        return max(
            (level for level in (50, 70, 85) if total * 100 >= self.allowance * level),
            default=0,
        )

    def snapshot(self, today=None):
        period = billing_period(today or date.today(), self.start_day)
        try:
            with closing(self._connect()) as con:
                rows = con.execute(
                    "SELECT stream,requests,responses,rows,request_bytes,response_bytes FROM transfer_usage WHERE period=?",
                    (period,),
                ).fetchall()
                station_id = con.execute(
                    "SELECT station_id FROM transfer_identity WHERE singleton=1"
                ).fetchone()[0]
        except (OSError, sqlite3.Error):
            self._unavailable()
            return {"available": False, "period": period, "alert_percent": 0}
        names = (
            "requests",
            "responses",
            "rows",
            "request_bytes_estimated",
            "response_bytes_estimated",
        )
        totals = {
            name: sum(row[index + 1] for row in rows)
            for index, name in enumerate(names)
        }
        return {
            "available": True,
            "period": period,
            "station_id": station_id,
            "captured_at": datetime.now(timezone.utc).isoformat(),
            **totals,
            "station_allowance_bytes": self.allowance,
            "alert_percent": self._level(totals["response_bytes_estimated"]),
            "streams": {row[0]: dict(zip(names, row[1:])) for row in rows},
        }

    def background_delay(self, today=None):
        level = self.snapshot(today)["alert_percent"]
        return {0: 30, 50: 60, 70: 120, 85: 300}[level]


class MeasuredCursor:
    """Preserve DB-API results and measure only their size; no payload is stored."""

    def __init__(self, cursor, meter, stream):
        self.cursor, self.meter, self.stream = cursor, meter, stream

    def __getattr__(self, name):
        return getattr(self.cursor, name)

    def _record(self, rows):
        size = sum(payload_bytes(value) for row in rows for value in row)
        self.meter.record(self.stream, rows=len(rows), response_bytes=size)
        return rows

    def fetchone(self):
        row = self.cursor.fetchone()
        if row is not None:
            self._record([row])
        return row

    def fetchall(self):
        return self._record(self.cursor.fetchall())

    def fetchmany(self, size=None):
        rows = self.cursor.fetchmany() if size is None else self.cursor.fetchmany(size)
        return self._record(rows)

    def __iter__(self):
        return self

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row


class AdaptivePoll:
    """Back off idle polls while keeping a fixed maximum propagation delay."""

    def __init__(self, minimum=10, maximum=30):
        if not 0 < minimum <= maximum:
            raise ValueError("Intervalo de consulta inválido.")
        self.minimum, self.maximum = minimum, maximum
        self.interval = minimum

    def completed(self, *, changed):
        self.interval = (
            self.minimum if changed else min(self.maximum, self.interval * 2)
        )
        return self.interval


def aggregate_snapshots(snapshots, quota_bytes=DEFAULT_QUOTA_BYTES):
    """Combine latest exports per station; never count the same PC twice."""
    latest: dict[str, dict] = {}
    for snapshot in snapshots:
        if not snapshot.get("available"):
            raise ValueError("Hay una estación sin medición disponible.")
        key = snapshot["station_id"]
        if key not in latest or snapshot["captured_at"] > latest[key]["captured_at"]:
            latest[key] = snapshot
    periods = {row["period"] for row in latest.values()}
    if len(periods) > 1:
        raise ValueError("Los archivos corresponden a ciclos distintos.")
    total = sum(row["response_bytes_estimated"] for row in latest.values())
    return {
        "stations": len(latest),
        "period": next(iter(periods), None),
        "response_bytes_estimated": total,
        "quota_bytes": quota_bytes,
        "alert_percent": max(
            (p for p in (50, 70, 85) if total * 100 >= quota_bytes * p), default=0
        ),
    }
