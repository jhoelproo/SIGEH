"""Durable local receipt commands, retained until central confirmation."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from datetime import date
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any
import uuid

from billing_money import line_total, money, sum_money
from network_retry import NetworkRetryGate
from sqlite_write_coordinator import connect_local_sqlite, prepare_sqlite_database

JOB_FIELDS = (
    "patient",
    "date_str",
    "dx_raw",
    "ars_name",
    "coverage",
    "sala",
    "grouped",
    "total_general",
    "is_backdated",
    "authorization_number",
    "admission_attention",
    "admission_session_id",
    "verification_bypass",
    "payment_status",
    "exemption_reason",
)
USER_FIELDS = ("username", "full_name", "role")


def receipt_command(job: dict[str, Any]) -> tuple[str, str]:
    """Validate local amounts and omit login secrets from the command."""
    if job.get("editing_id") is not None:
        raise ValueError("Los recibos centrales no se editan sin conexión.")
    payload = {field: job.get(field) for field in JOB_FIELDS}
    user = dict(job.get("current_user") or {})
    payload["current_user"] = {field: user.get(field, "") for field in USER_FIELDS}
    _require_actor_and_patient(payload, user)
    date.fromisoformat(str(payload["date_str"]))
    total = _command_total(payload)
    if total <= 0 or money(payload["total_general"]) != total:
        raise ValueError("El total no coincide con los cargos del recibo.")
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, allow_nan=False
    )
    return serialized, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _require_actor_and_patient(payload: dict[str, Any], user: dict[str, Any]) -> None:
    if not str(payload["patient"] or "").strip():
        raise ValueError("El paciente es obligatorio.")
    if not str(user.get("username") or "").strip():
        raise ValueError("El usuario es obligatorio.")


def _command_total(payload: dict[str, Any]):
    totals = [money(payload["sala"])]
    for _category, items in payload["grouped"] or ():
        for name, price, quantity, subtotal, _ars in items:
            if not str(name).strip() or int(quantity) < 1:
                raise ValueError("El ítem y su cantidad son obligatorios.")
            amount = line_total(price, quantity)
            if amount != money(subtotal):
                raise ValueError("El subtotal del ítem no coincide con su cantidad.")
            totals.append(amount)
    return sum_money(totals)


class LocalReceiptStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        prepare_sqlite_database(self.path)
        with closing(self._connect()) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS local_receipts(
                    request_id TEXT PRIMARY KEY, username TEXT NOT NULL,
                    payload_json TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'PENDING'
                        CHECK(state IN ('PENDING','SYNCED','REVIEW')),
                    central_id INTEGER, central_number INTEGER,
                    error_type TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS local_receipt_cache(
                    cache_key TEXT PRIMARY KEY,value_json TEXT NOT NULL
                )"""
            )
            connection.commit()

    def _connect(self):
        connection = connect_local_sqlite(self.path, operation="local-receipts")
        connection.row_factory = sqlite3.Row
        return connection

    def enqueue(self, job: dict[str, Any], request_id: str = "") -> str:
        identity = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
        payload, digest = receipt_command(job)
        username = str(job["current_user"]["username"])
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO local_receipts(request_id,username,payload_json,payload_hash)
                   VALUES(?,?,?,?) ON CONFLICT(request_id) DO NOTHING""",
                (identity, username, payload, digest),
            )
            row = connection.execute(
                "SELECT payload_hash FROM local_receipts WHERE request_id=?",
                (identity,),
            ).fetchone()
            if row["payload_hash"] != digest:
                raise ValueError("El identificador local pertenece a otro contenido.")
        return identity

    def rows(
        self, username: str, *, pending_only: bool = False
    ) -> list[dict[str, Any]]:
        state = "PENDING" if pending_only else ""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """SELECT * FROM local_receipts WHERE username=?
                   AND (?='' OR state=?)
                   ORDER BY CASE WHEN ? THEN rowid END ASC,rowid DESC LIMIT 100""",
                (username, state, state, pending_only),
            ).fetchall()
        return [dict(row) for row in rows]

    def confirm(self, request_id: str, central_id: int, central_number: int) -> None:
        if central_id < 1 or central_number < 1:
            raise ValueError("La confirmación central no es válida.")
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """UPDATE local_receipts SET state='SYNCED',central_id=?,
                   central_number=?,error_type='' WHERE request_id=? AND state='PENDING'""",
                (central_id, central_number, request_id),
            )

    def record_failure(
        self, request_id: str, error: Exception, *, review: bool
    ) -> None:
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """UPDATE local_receipts SET state=?,error_type=?
                   WHERE request_id=? AND state='PENDING'""",
                ("REVIEW" if review else "PENDING", type(error).__name__, request_id),
            )

    def cache(self, key: str, value: Any) -> None:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False, default=str)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO local_receipt_cache(cache_key,value_json) VALUES(?,?)
                   ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json""",
                (key, serialized),
            )

    def cached(self, key: str, default: Any = None) -> Any:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT value_json FROM local_receipt_cache WHERE cache_key=?", (key,)
            ).fetchone()
        return json.loads(row[0]) if row else default

    def row_for_user(self, request_id: str, username: str) -> dict[str, Any]:
        identity = str(uuid.UUID(request_id))
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM local_receipts WHERE request_id=? AND username=?",
                (identity, username),
            ).fetchone()
        if row is None:
            raise PermissionError("No se encontró un recibo local de este usuario.")
        return dict(row)


class LocalReceiptSynchronizer:
    def __init__(
        self,
        store: LocalReceiptStore,
        publish: Callable[[dict[str, Any], str, str], tuple[int, int]],
        recoverable: Callable[[BaseException], bool],
        *,
        retry_gate: NetworkRetryGate | None = None,
    ):
        self.store = store
        self.publish = publish
        self.recoverable = recoverable
        self.retry_gate = retry_gate or NetworkRetryGate()

    def run(self, username: str) -> dict[str, int]:
        result = {"confirmed": 0, "pending": 0, "review": 0, "deferred": 0}
        if not self.retry_gate.ready:
            result["deferred"] = 1
            return result
        for row in self.store.rows(username, pending_only=True):
            try:
                digest = hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest()
                if digest != row["payload_hash"]:
                    raise ValueError("El contenido local cambió después de guardarse.")
                receipt_id, number = self.publish(
                    json.loads(row["payload_json"]),
                    row["request_id"],
                    row["payload_hash"],
                )
                self.store.confirm(row["request_id"], receipt_id, number)
            except Exception as error:
                temporary = self.recoverable(error)
                self.store.record_failure(
                    row["request_id"], error, review=not temporary
                )
                if temporary:
                    self.retry_gate.failed(error)
                    result["pending"] += 1
                    break
                result["review"] += 1
            else:
                self.retry_gate.succeeded()
                result["confirmed"] += 1
        return result
