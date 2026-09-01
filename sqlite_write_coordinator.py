from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
import traceback
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar


LOGGER = logging.getLogger("hospital.admission.sqlite")
SQLITE_BUSY_TIMEOUT_MS = 5_000
SQLITE_WRITE_TIMEOUT_SECONDS = 8.0
SQLITE_LONG_TRANSACTION_MS = 2_000.0
SQLITE_BUSY_RETRY_DELAYS_SECONDS = (0.08, 0.20)
_T = TypeVar("_T")


class SQLiteWriteTimeout(sqlite3.OperationalError):
    """Raised when the in-process writer queue cannot acquire the database."""


def _canonical_database_path(database: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(database)))


def assert_private_local_database(database: str | Path) -> str:
    """Reject locations that can make one SQLite file writable by several PCs."""
    path = _canonical_database_path(database)
    lowered = path.casefold()
    parts = tuple(part.casefold() for part in Path(path).parts)
    bundled_private_data = len(parts) >= 3 and parts[-3:-1] == ("_internal", "data")
    if path.startswith("\\\\") or (
        not bundled_private_data and any(
        token in lowered for token in ("\\onedrive\\", "\\dropbox\\", "\\google drive\\")
        )
    ):
        raise ValueError(
            "La réplica SQLite de Admisión debe estar en un disco local privado; "
            "no puede ubicarse en OneDrive ni en una carpeta de red."
        )
    return path


class SQLiteWriteCoordinator:
    """FIFO, re-entrant, single-writer gate shared by every local component."""

    def __init__(self, database: str | Path):
        self.database = assert_private_local_database(database)
        self._condition = threading.Condition()
        self._owner_thread_id: int | None = None
        self._owner_depth = 0
        self._owner_operation = ""
        self._owner_thread_name = ""
        self._owner_started_at = 0.0
        self._owner_wait_ms = 0.0
        self._owner_connection_id = 0
        self._owner_stack = ""
        self._waiters: deque[object] = deque()

    @property
    def queue_depth(self) -> int:
        with self._condition:
            return len(self._waiters)

    @property
    def active_operation(self) -> str:
        with self._condition:
            return self._owner_operation

    def diagnostic_snapshot(self) -> dict[str, object]:
        """Return lock ownership details without exposing SQLite connections."""
        with self._condition:
            hold_ms = (
                (time.perf_counter() - self._owner_started_at) * 1000.0
                if self._owner_thread_id is not None
                else 0.0
            )
            return {
                "operation": self._owner_operation,
                "thread": self._owner_thread_name,
                "hold_ms": hold_ms,
                "queue_depth": len(self._waiters),
                "connection_id": self._owner_connection_id,
                "thread_id": self._owner_thread_id,
                "owner_stack": self._owner_stack,
            }

    def acquire(
        self,
        operation: str,
        timeout: float = SQLITE_WRITE_TIMEOUT_SECONDS,
        *,
        connection_id: int = 0,
    ) -> None:
        thread_id = threading.get_ident()
        started = time.perf_counter()
        token = object()
        with self._condition:
            if self._owner_thread_id == thread_id:
                self._owner_depth += 1
                return
            self._waiters.append(token)
            if self._owner_thread_id is not None or self._waiters[0] is not token:
                holder = self.diagnostic_snapshot()
                LOGGER.info(
                    "SQLITE_WRITE_WAIT_START operation=%s thread=%s queue_depth=%s "
                    "lock_holder_operation=%s lock_holder_thread=%s lock_holder_hold_ms=%.1f",
                    operation,
                    threading.current_thread().name,
                    len(self._waiters),
                    holder["operation"],
                    holder["thread"],
                    float(holder["hold_ms"]),
                )
            while self._owner_thread_id is not None or self._waiters[0] is not token:
                remaining = timeout - (time.perf_counter() - started)
                if remaining <= 0:
                    self._waiters.remove(token)
                    elapsed_ms = (time.perf_counter() - started) * 1000.0
                    holder = self.diagnostic_snapshot()
                    LOGGER.error(
                        "SQLITE_WRITE_TIMEOUT operation=%s thread=%s elapsed_ms=%.1f "
                        "database_path=%s connection_id=%s writer_queue_depth=%s "
                        "lock_holder_operation=%s lock_holder_thread=%s "
                        "lock_holder_thread_id=%s lock_holder_connection_id=%s "
                        "lock_holder_hold_ms=%.1f lock_holder_stack=%s",
                        operation,
                        threading.current_thread().name,
                        elapsed_ms,
                        self.database,
                        connection_id,
                        len(self._waiters),
                        holder["operation"],
                        holder["thread"],
                        holder["thread_id"],
                        holder["connection_id"],
                        float(holder["hold_ms"]),
                        holder["owner_stack"],
                    )
                    raise SQLiteWriteTimeout(
                        "La réplica local continúa ocupada; la operación quedó pendiente."
                    )
                self._condition.wait(min(remaining, 0.25))
            self._waiters.popleft()
            self._owner_thread_id = thread_id
            self._owner_depth = 1
            self._owner_operation = str(operation or "sqlite-write")
            self._owner_thread_name = threading.current_thread().name
            self._owner_started_at = time.perf_counter()
            self._owner_wait_ms = (self._owner_started_at - started) * 1000.0
            self._owner_connection_id = int(connection_id or 0)
            self._owner_stack = " > ".join(
                frame.name for frame in traceback.extract_stack(limit=8)[:-1]
            )
            LOGGER.info(
                "SQLITE_WRITE_ACQUIRED operation=%s thread=%s thread_id=%s "
                "connection_id=%s wait_ms=%.1f queue_depth=%s",
                self._owner_operation,
                self._owner_thread_name,
                self._owner_thread_id,
                self._owner_connection_id,
                self._owner_wait_ms,
                len(self._waiters),
            )

    def release(self) -> None:
        thread_id = threading.get_ident()
        with self._condition:
            if self._owner_thread_id != thread_id:
                return
            self._owner_depth -= 1
            if self._owner_depth > 0:
                return
            hold_ms = (time.perf_counter() - self._owner_started_at) * 1000.0
            LOGGER.info(
                "SQLITE_WRITE_RELEASED operation=%s thread=%s thread_id=%s "
                "connection_id=%s wait_ms=%.1f hold_ms=%.1f queue_depth=%s",
                self._owner_operation,
                self._owner_thread_name,
                self._owner_thread_id,
                self._owner_connection_id,
                self._owner_wait_ms,
                hold_ms,
                len(self._waiters),
            )
            if hold_ms >= SQLITE_LONG_TRANSACTION_MS:
                LOGGER.warning(
                    "SQLITE_LONG_TRANSACTION operation=%s database_path=%s "
                    "thread=%s thread_id=%s connection_id=%s duration_ms=%.1f "
                    "owner_stack=%s",
                    self._owner_operation,
                    self.database,
                    self._owner_thread_name,
                    self._owner_thread_id,
                    self._owner_connection_id,
                    hold_ms,
                    self._owner_stack,
                )
            self._owner_thread_id = None
            self._owner_depth = 0
            self._owner_operation = ""
            self._owner_thread_name = ""
            self._owner_started_at = 0.0
            self._owner_wait_ms = 0.0
            self._owner_connection_id = 0
            self._owner_stack = ""
            self._condition.notify_all()

    @contextmanager
    def write(
        self,
        operation: str,
        timeout: float = SQLITE_WRITE_TIMEOUT_SECONDS,
    ) -> Iterator[None]:
        self.acquire(operation, timeout)
        try:
            yield
        finally:
            self.release()


_COORDINATORS: dict[str, SQLiteWriteCoordinator] = {}
_COORDINATORS_LOCK = threading.Lock()


def get_sqlite_write_coordinator(database: str | Path) -> SQLiteWriteCoordinator:
    path = assert_private_local_database(database)
    with _COORDINATORS_LOCK:
        coordinator = _COORDINATORS.get(path)
        if coordinator is None:
            coordinator = SQLiteWriteCoordinator(path)
            _COORDINATORS[path] = coordinator
        return coordinator


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA synchronous=NORMAL")


def prepare_sqlite_database(database: str | Path) -> str:
    """Enable WAL once, outside normal connection creation and critical work."""
    path = assert_private_local_database(database)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    coordinator = get_sqlite_write_coordinator(path)
    with coordinator.write("prepare-local-replica"):
        connection = sqlite3.connect(path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0)
        try:
            connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).upper()
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA foreign_keys=ON")
            return mode
        finally:
            connection.close()


_WRITE_PREFIXES = (
    "ALTER ",
    "BEGIN",
    "CREATE ",
    "DELETE ",
    "DROP ",
    "INSERT ",
    "REINDEX",
    "REPLACE ",
    "UPDATE ",
    "VACUUM",
)


def _is_write_statement(statement: str) -> bool:
    normalized = str(statement or "").lstrip().upper()
    if normalized.startswith("PRAGMA JOURNAL_MODE"):
        return True
    return normalized.startswith(_WRITE_PREFIXES)


class _CoordinatedCursor:
    def __init__(self, cursor: sqlite3.Cursor, owner: "CoordinatedSQLiteConnection"):
        self._cursor = cursor
        self._owner = owner

    def execute(self, statement: str, parameters: Any = ()) -> "_CoordinatedCursor":
        self._owner._before_sql(statement)
        self._cursor.execute(statement, parameters)
        return self

    def executemany(self, statement: str, parameters: Any) -> "_CoordinatedCursor":
        self._owner._before_sql(statement)
        self._cursor.executemany(statement, parameters)
        return self

    def executescript(self, script: str) -> "_CoordinatedCursor":
        self._owner._ensure_write_lock("sqlite-script")
        self._cursor.executescript(script)
        return self

    def __iter__(self):
        return iter(self._cursor)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class CoordinatedSQLiteConnection:
    """Connection proxy that acquires the shared gate only for write transactions."""

    def __init__(
        self,
        database: str | Path,
        *,
        operation: str = "sqlite-write",
        lock_timeout: float = SQLITE_WRITE_TIMEOUT_SECONDS,
    ):
        self.database = assert_private_local_database(database)
        self.operation = operation
        self.lock_timeout = max(0.0, float(lock_timeout))
        self.coordinator = get_sqlite_write_coordinator(self.database)
        self._write_locked = False
        self._connection = sqlite3.connect(
            self.database,
            timeout=SQLITE_BUSY_TIMEOUT_MS / 1000.0,
        )
        configure_sqlite_connection(self._connection)

    def _ensure_write_lock(self, operation: str | None = None) -> None:
        if self._write_locked:
            return
        self.coordinator.acquire(
            operation or self.operation,
            timeout=self.lock_timeout,
            connection_id=id(self._connection),
        )
        self._write_locked = True

    def _before_sql(self, statement: str) -> None:
        if _is_write_statement(statement):
            self._ensure_write_lock(self.operation)

    @property
    def row_factory(self):
        return self._connection.row_factory

    @row_factory.setter
    def row_factory(self, value) -> None:
        self._connection.row_factory = value

    def cursor(self, *args, **kwargs) -> _CoordinatedCursor:
        return _CoordinatedCursor(self._connection.cursor(*args, **kwargs), self)

    def execute(self, statement: str, parameters: Any = ()):
        self._before_sql(statement)
        return self._connection.execute(statement, parameters)

    def executemany(self, statement: str, parameters: Any):
        self._before_sql(statement)
        return self._connection.executemany(statement, parameters)

    def executescript(self, script: str):
        self._ensure_write_lock("sqlite-script")
        return self._connection.executescript(script)

    def commit(self) -> None:
        try:
            self._connection.commit()
        finally:
            self._release_write_lock()

    def rollback(self) -> None:
        try:
            self._connection.rollback()
        finally:
            self._release_write_lock()

    def _release_write_lock(self) -> None:
        if not self._write_locked:
            return
        self._write_locked = False
        self.coordinator.release()

    def close(self) -> None:
        try:
            self._connection.close()
        finally:
            self._release_write_lock()

    def __enter__(self) -> "CoordinatedSQLiteConnection":
        return self

    def __exit__(self, exc_type, _exc, _tb) -> bool:
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        self.close()
        return False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


def connect_local_sqlite(
    database: str | Path,
    *,
    operation: str = "sqlite-write",
    lock_timeout: float = SQLITE_WRITE_TIMEOUT_SECONDS,
) -> CoordinatedSQLiteConnection:
    return CoordinatedSQLiteConnection(
        database,
        operation=operation,
        lock_timeout=lock_timeout,
    )


def run_with_bounded_sqlite_busy_retry(
    operation: str,
    callback: Callable[[], _T],
) -> _T:
    """Retry a local durable unit only for SQLite BUSY/LOCKED failures.

    Callers must execute this helper outside the GUI thread.  The same callback
    and payload are reused so an admission identity cannot change between
    attempts.
    """
    started = time.perf_counter()
    delays = (0.0, *SQLITE_BUSY_RETRY_DELAYS_SECONDS)
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            time.sleep(delay)
        try:
            return callback()
        except (SQLiteWriteTimeout, sqlite3.OperationalError) as exc:
            message = str(exc or "").casefold()
            is_busy = isinstance(exc, SQLiteWriteTimeout) or any(
                token in message for token in ("locked", "busy", "ocupada")
            )
            if not is_busy or attempt >= len(delays):
                raise
            LOGGER.warning(
                "SQLITE_BUSY_RETRYING operation=%s retry_attempt=%s "
                "busy_elapsed_ms=%.1f exception_type=%s",
                str(operation or "sqlite-write"),
                attempt,
                (time.perf_counter() - started) * 1000.0,
                type(exc).__name__,
            )
    raise AssertionError("Unreachable SQLite retry state")
