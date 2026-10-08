from unittest.mock import Mock
from types import SimpleNamespace
import threading

import pytest

from admission_hybrid import AdmissionSyncService, is_temporary_connection_error
from network_retry import NetworkRetryGate, is_service_restriction


class RestrictedError(RuntimeError):
    status_code = 402


def test_restriction_is_recoverable_without_configuration_changes():
    assert is_temporary_connection_error(RestrictedError("Payment Required"))


def test_restricted_batch_does_not_retry_every_patient():
    store = Mock()
    store.last_cloud_cursor.return_value = 0
    store.pending_events.return_value = []
    cloud = Mock()
    cloud.push_events.side_effect = RestrictedError("Payment Required")
    service = AdmissionSyncService(store, cloud)
    with pytest.raises(RestrictedError):
        service.push_outbox()
    cloud.push_event.assert_not_called()
    store.mark_uploaded_batch.assert_not_called()


@pytest.mark.parametrize("attribute,value", [("status_code", 402), ("pgcode", "25006")])
def test_structured_service_restrictions(attribute, value):
    error = RuntimeError("provider error")
    setattr(error, attribute, value)
    assert is_service_restriction(error)


def test_response_restriction():
    error = RuntimeError("provider error")
    error.response = SimpleNamespace(status_code=402)
    assert is_service_restriction(error)


@pytest.mark.parametrize(
    "message",
    [
        "payment required",
        "egress exceeded",
        "egress quota",
        "project is paused",
        "project is restricted",
    ],
)
def test_documented_restriction_messages(message):
    assert is_service_restriction(RuntimeError(message))


@pytest.mark.parametrize(
    "message",
    ["permission denied", "invalid password", "undefined column", "", "402 patients"],
)
def test_unrelated_errors_are_not_quota_restrictions(message):
    assert not is_service_restriction(RuntimeError(message))


def test_network_retries_grow_are_bounded_and_reset():
    now = [100.0]
    gate = NetworkRetryGate(lambda: now[0])
    assert gate.ready
    for delay in (10, 20, 40, 80, 160, 300, 300):
        gate.failed(ConnectionError("connection refused"))
        now[0] += delay - 0.01
        assert not gate.ready
        now[0] += 0.01
        assert gate.ready
    gate.succeeded()
    gate.failed(ConnectionError("connection refused"))
    now[0] += 10
    assert gate.ready


def test_restriction_defers_recovery_probe_for_fifteen_minutes():
    now = [0.0]
    gate = NetworkRetryGate(lambda: now[0])
    gate.failed(RestrictedError("Payment Required"))
    now[0] = 899.99
    assert not gate.ready
    now[0] = 900
    assert gate.ready
    gate.succeeded()
    assert gate.ready


def test_deferred_admission_sync_performs_no_central_queries():
    from admission_v15_adapter import _HybridAdmissionRuntime

    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime._lock = threading.RLock()
    runtime.store = Mock()
    runtime.sync_service = Mock()
    runtime.state = Mock(return_value={"offline": True})
    runtime._backup_schedule = None
    runtime._network_retry_gate = NetworkRetryGate(lambda: 100)
    runtime._network_retry_gate.failed(RestrictedError())
    runtime.connection_supervisor = Mock()
    assert runtime.synchronize() == {"offline": True, "network_retry_deferred": True}
    runtime.connection_supervisor.recover.assert_not_called()
    runtime.sync_service.synchronize_once.assert_not_called()
