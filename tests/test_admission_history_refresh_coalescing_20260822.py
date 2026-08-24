from admission_refresh_coordinator import CoalescedRefreshGate, history_rows_fingerprint


class _FakeScheduler:
    def __init__(self):
        self.callbacks = {}
        self.next_token = 0

    def schedule(self, _delay_ms, callback):
        self.next_token += 1
        self.callbacks[self.next_token] = callback
        return self.next_token

    def cancel(self, token):
        self.callbacks.pop(token, None)

    def fire_next(self):
        token = min(self.callbacks)
        callback = self.callbacks.pop(token)
        callback()


def test_burst_is_debounced_to_one_history_reload():
    scheduler = _FakeScheduler()
    completions = []

    gate = CoalescedRefreshGate(
        schedule=scheduler.schedule,
        cancel=scheduler.cancel,
        start=lambda _reason, done: completions.append(done),
        debounce_ms=200,
    )

    for index in range(10):
        gate.request(f"event-{index}")

    assert len(scheduler.callbacks) == 1
    scheduler.fire_next()
    assert gate.run_count == 1
    assert len(completions) == 1
    completions.pop()(changed=True)
    assert not scheduler.callbacks


def test_events_while_busy_produce_exactly_one_follow_up_reload():
    scheduler = _FakeScheduler()
    completions = []
    gate = CoalescedRefreshGate(
        schedule=scheduler.schedule,
        cancel=scheduler.cancel,
        start=lambda _reason, done: completions.append(done),
    )

    gate.request("initial", immediate=True)
    scheduler.fire_next()
    for index in range(10):
        gate.request(f"busy-{index}")

    assert gate.run_count == 1
    completions.pop()(changed=True)
    assert len(scheduler.callbacks) == 1
    scheduler.fire_next()
    assert gate.run_count == 2
    completions.pop()(changed=False)
    assert not scheduler.callbacks


def test_sixty_seconds_idle_does_not_schedule_periodic_full_reload():
    scheduler = _FakeScheduler()
    completions = []
    gate = CoalescedRefreshGate(
        schedule=scheduler.schedule,
        cancel=scheduler.cancel,
        start=lambda _reason, done: completions.append(done),
    )

    gate.request("open", immediate=True)
    scheduler.fire_next()
    completions.pop()(changed=False)

    assert gate.run_count == 1
    assert scheduler.callbacks == {}


def test_history_fingerprint_detects_update_and_cancel_without_patient_logging():
    original = [{"global_attention_id": "same", "latest_sequence": 1, "estado": "ACTIVA"}]
    updated = [{"global_attention_id": "same", "latest_sequence": 2, "estado": "ACTIVA"}]
    cancelled = [{"global_attention_id": "same", "latest_sequence": 3, "estado": "ANULADA"}]

    assert history_rows_fingerprint(original) != history_rows_fingerprint(updated)
    assert history_rows_fingerprint(updated) != history_rows_fingerprint(cancelled)


def test_gate_reports_state_closes_scheduled_work_and_rejects_new_requests():
    scheduler = _FakeScheduler()
    gate = CoalescedRefreshGate(
        schedule=scheduler.schedule,
        cancel=scheduler.cancel,
        start=lambda _reason, _done: None,
    )

    assert gate.busy is False
    assert gate.pending is False
    gate.request("scheduled")
    assert gate.pending is True
    gate.close()

    assert scheduler.callbacks == {}
    assert gate.request("after-close") == "CLOSED"
    gate.finish(changed=False)


def test_start_error_releases_gate_and_close_without_token_is_safe():
    scheduler = _FakeScheduler()

    def fail_start(_reason, _done):
        raise RuntimeError("expected")

    gate = CoalescedRefreshGate(
        schedule=scheduler.schedule,
        cancel=scheduler.cancel,
        start=fail_start,
    )
    gate.request("failure", immediate=True)
    scheduler.fire_next()

    assert gate.busy is False
    assert gate.run_count == 1
    gate.close()


def test_cancel_failure_and_reentrant_run_do_not_create_extra_active_job():
    scheduler = _FakeScheduler()
    completions = []
    gate = CoalescedRefreshGate(
        schedule=scheduler.schedule,
        cancel=lambda _token: (_ for _ in ()).throw(RuntimeError("cancel")),
        start=lambda _reason, done: completions.append(done),
    )
    gate.request("first")
    gate.request("replacement")
    # The cancelled callback may still exist in a hostile scheduler. The gate
    # still allows only one active operation and records one pending follow-up.
    scheduler.fire_next()
    gate._run()

    assert gate.busy is True
    assert gate.pending is True
    completions.pop()(changed=True)
    gate.close()
