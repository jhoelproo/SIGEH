"""Bound retries while preserving local pending work during service outages."""

from collections.abc import Callable
from time import monotonic

RESTRICTION_RETRY_SECONDS = 900.0
MAX_NETWORK_RETRY_SECONDS = 300.0
INITIAL_NETWORK_RETRY_SECONDS = 10.0


def is_service_restriction(error: BaseException) -> bool:
    response = getattr(error, "response", None)
    if getattr(error, "status_code", None) == 402:
        return True
    if getattr(response, "status_code", None) == 402:
        return True
    if getattr(error, "pgcode", None) == "25006":
        return True
    text = str(error).casefold()
    return any(
        marker in text
        for marker in (
            "payment required",
            "egress quota",
            "egress exceeded",
            "project is paused",
            "project is restricted",
        )
    )


def raise_service_restriction(error: BaseException) -> None:
    if is_service_restriction(error):
        raise error


class NetworkRetryGate:
    """Permit periodic recovery probes without sleeping in a worker or UI."""

    def __init__(self, clock: Callable[[], float] = monotonic):
        self._clock = clock
        self._failures = 0
        self._retry_at = 0.0

    @property
    def ready(self) -> bool:
        return self._clock() >= self._retry_at

    def failed(self, error: BaseException) -> None:
        self._failures = min(self._failures + 1, 6)
        delay = min(
            INITIAL_NETWORK_RETRY_SECONDS * 2 ** (self._failures - 1),
            MAX_NETWORK_RETRY_SECONDS,
        )
        if is_service_restriction(error):
            delay = RESTRICTION_RETRY_SECONDS
        self._retry_at = self._clock() + delay

    def succeeded(self) -> None:
        self._failures = 0
        self._retry_at = 0.0
