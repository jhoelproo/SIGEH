"""Small event-driven refresh primitives shared by the Admission UI."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from time import perf_counter


class CoalescedRefreshGate:
    """Debounce refresh requests and keep at most one follow-up while busy."""

    def __init__(
        self,
        *,
        schedule: Callable[[int, Callable[[], None]], object],
        cancel: Callable[[object], None],
        start: Callable[[str, Callable[..., None]], None],
        debounce_ms: int = 200,
        logger: logging.Logger | None = None,
        log_prefix: str = "HISTORY_REFRESH",
    ) -> None:
        self._schedule = schedule
        self._cancel = cancel
        self._start = start
        self._debounce_ms = max(0, int(debounce_ms))
        self._logger = logger or logging.getLogger(__name__)
        self._log_prefix = str(log_prefix or "REFRESH")
        self._scheduled_token: object | None = None
        self._busy = False
        self._pending = False
        self._pending_reason = ""
        self._active_reason = ""
        self._started_at = 0.0
        self._closed = False
        self.run_count = 0

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def pending(self) -> bool:
        return self._pending or self._scheduled_token is not None

    def request(self, reason: str, *, immediate: bool = False) -> str:
        if self._closed:
            return "CLOSED"
        normalized_reason = str(reason or "unspecified")
        self._logger.info("%s_REQUEST reason=%s", self._log_prefix, normalized_reason)
        if self._busy:
            self._pending = True
            self._pending_reason = normalized_reason
            self._logger.info(
                "%s_SKIPPED reason=%s state=BUSY_PENDING",
                self._log_prefix,
                normalized_reason,
            )
            return "PENDING"
        if self._scheduled_token is not None:
            try:
                self._cancel(self._scheduled_token)
            except Exception:
                self._logger.debug("No se pudo cancelar un refresco reemplazado", exc_info=True)
        delay = 0 if immediate else self._debounce_ms
        self._pending_reason = normalized_reason
        self._scheduled_token = self._schedule(delay, self._run)
        return "SCHEDULED"

    def _run(self) -> None:
        if self._closed:
            return
        self._scheduled_token = None
        if self._busy:
            self._pending = True
            return
        self._busy = True
        self._active_reason = self._pending_reason or "unspecified"
        self._pending_reason = ""
        self._started_at = perf_counter()
        self.run_count += 1
        self._logger.info(
            "%s_START reason=%s run=%s",
            self._log_prefix,
            self._active_reason,
            self.run_count,
        )
        try:
            self._start(self._active_reason, self.finish)
        except (AttributeError, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            self.finish(changed=False, error=exc)

    def finish(
        self,
        *,
        changed: bool = False,
        error: Exception | None = None,
        rows: int | None = None,
    ) -> None:
        if not self._busy:
            return
        elapsed_ms = (perf_counter() - self._started_at) * 1000.0
        if error is not None:
            self._logger.error(
                "%s_DONE reason=%s rows=%s changed=false error=%s elapsed_ms=%.1f",
                self._log_prefix,
                self._active_reason,
                "unknown" if rows is None else int(rows),
                type(error).__name__,
                elapsed_ms,
            )
        else:
            self._logger.info(
                "%s_DONE reason=%s rows=%s changed=%s elapsed_ms=%.1f",
                self._log_prefix,
                self._active_reason,
                "unknown" if rows is None else int(rows),
                str(bool(changed)).lower(),
                elapsed_ms,
            )
        self._busy = False
        self._active_reason = ""
        if self._closed or not self._pending:
            return
        reason = self._pending_reason or "pending_event"
        self._pending = False
        self._pending_reason = ""
        self.request(reason)

    def close(self) -> None:
        self._closed = True
        self._pending = False
        if self._scheduled_token is None:
            return
        try:
            self._cancel(self._scheduled_token)
        except Exception:
            self._logger.debug("No se pudo cancelar el refresco al cerrar", exc_info=True)
        finally:
            self._scheduled_token = None


def _first_text(row: Mapping, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value)
    return ""


def _first_int(row: Mapping, *keys: str) -> int:
    value = _first_text(row, *keys)
    return int(value) if value else 0


def history_rows_fingerprint(rows) -> tuple:
    """Stable clinical identity/revision fingerprint without patient data in logs."""

    result = []
    for raw in rows or ():
        row = dict(raw or {})
        global_id = _first_text(row, "global_attention_id").replace("-", "").lower()
        identity = global_id or f"local:{row.get('id') or ''}"
        result.append(
            (
                identity,
                _first_text(row, "latest_event_uuid"),
                _first_int(row, "latest_sequence", "version"),
                _first_text(row, "source_status", "estado"),
                _first_text(row, "fecha"),
                _first_text(row, "hora"),
                _first_text(row, "nombre", "patient_name"),
                _first_text(row, "hoja", "specialty"),
                _first_text(row, "ars", "canonical_ars"),
                _first_text(row, "nss", "nss_snapshot"),
                _first_text(row, "cedula", "cedula_snapshot"),
                _first_text(row, "tipo_atencion", "service_type"),
            )
        )
    return tuple(result)
