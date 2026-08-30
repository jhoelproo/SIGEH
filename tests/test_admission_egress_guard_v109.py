from __future__ import annotations

from datetime import datetime, timezone

from admission_hybrid import (
    AdmissionCloudRepository,
    AdmissionSyncService,
    OfflineAdmissionStore,
    SYNC_TICK_SECONDS,
    SyncConflict,
)
from admission_v15_adapter import (
    _HybridCoordinator,
    _bind_operational_file_logging,
    bounded_sync_retry_delay,
)


CURRENT_SOURCE = "748d96bb-808f-4f66-b3fe-d256326b20f9"
LEGACY_SOURCE = "11111111-1111-4111-8111-111111111111"
EVENT_UUID = "22222222-2222-4222-8222-222222222222"
ENTITY_UUID = "33333333-3333-4333-8333-333333333333"


class _CursorStore:
    def __init__(self, cursor: int = 49, *, block: bool = True):
        self.cursor = cursor
        self.block = block
        self.queue_calls = 0

    def last_cloud_cursor(self) -> int:
        return self.cursor

    def set_last_cloud_cursor(self, cursor: int) -> None:
        self.cursor = max(self.cursor, int(cursor))

    def update_server_time_offset(self, _server_time):
        return {"server_time_offset_ms": 0, "drift_detected": False}

    def pending_events(self, _limit):
        return []

    def mark_uploaded_batch(self, _event_ids):
        return None

    def queue_missing_attention_events(self, *, limit):
        self.queue_calls += 1
        return 0

    def apply_remote_events(self, events):
        if events and not self.block:
            self.cursor = max(int(event["sequence"]) for event in events)
            return len(events)
        return 0

    def is_remote_event_materialized(self, _event):
        return not self.block

    def hydrate_remote_events(self, events):
        return len(list(events))


class _MeasuredCloud:
    def __init__(self, *, blocker_source: str = LEGACY_SOURCE, blocker_turn: int = 240):
        self.latest_sequence = 249
        self.blocker_source = blocker_source
        self.blocker_turn = blocker_turn
        self.event_queries = 0
        self.window_queries = 0
        self.current_turn_queries = 0
        self.projection_checks = 0

    def event_window(self):
        self.window_queries += 1
        return {
            "minimum_available_sequence": 0,
            "checkpoint_sequence": 0,
            "latest_sequence": self.latest_sequence,
            "server_time": datetime.now(timezone.utc),
        }

    def events_after(self, cursor, *, limit=200):
        self.event_queries += 1
        return [
            {
                "sequence": sequence,
                "event_uuid": EVENT_UUID[:-3] + f"{sequence:03d}",
                "entity_type": "attention",
                "entity_uuid": ENTITY_UUID,
                "operation": "RECONCILE",
                "payload_json": {
                    "global_attention_id": ENTITY_UUID,
                    "operational_source_id": self.blocker_source,
                    "turn_id": self.blocker_turn,
                    "padding": "x" * 1200,
                },
            }
            for sequence in range(
                int(cursor) + 1, min(self.latest_sequence, int(cursor) + limit) + 1
            )
        ]

    def projection_has_attention(self, _entity_uuid):
        self.projection_checks += 1
        return True

    def current_turn_attention_events(self, **_kwargs):
        self.current_turn_queries += 1
        return []

    def push_events(self, _events):
        return {}


class _CallbackStore(_CursorStore):
    def __init__(self):
        super().__init__(cursor=0, block=False)
        self.applied: set[str] = set()
        self.materialized: set[str] = set()
        self.discarded: list[str] = []

    def already_applied(self, event_uuid):
        return event_uuid in self.applied

    def is_remote_event_materialized(self, event):
        return str(event.get("event_uuid") or "") in self.materialized

    def discard_applied_event(self, event_uuid):
        self.applied.discard(event_uuid)
        self.discarded.append(event_uuid)

    def mark_applied_and_advance(self, event_uuid, sequence):
        self.applied.add(event_uuid)
        self.cursor = max(self.cursor, int(sequence))


def _cycle(service: AdmissionSyncService):
    return service.synchronize_once(
        operational_source_id=CURRENT_SOURCE,
        turn_id=3946,
        device_id="PC-A",
    )


def test_historical_collision_is_checkpointed_once_after_current_turn_reconcile():
    store = _CursorStore()
    cloud = _MeasuredCloud()
    service = AdmissionSyncService(store, cloud)

    result = _cycle(service)

    assert result["pull_requests"] == 1
    assert result["pull_rows"] == 200
    assert result["checkpoint_recovered"] == 249
    assert result["cursor_before"] == 49
    assert result["cursor_after"] == 249
    assert cloud.event_queries == 1
    assert cloud.current_turn_queries == 1
    assert cloud.projection_checks == 1


def test_fifteen_idle_minutes_do_not_redownload_the_historical_page():
    store = _CursorStore()
    cloud = _MeasuredCloud()
    service = AdmissionSyncService(store, cloud)
    cycles = (15 * 60) // SYNC_TICK_SECONDS

    results = [_cycle(service) for _ in range(cycles)]

    assert cycles == 90
    assert cloud.window_queries == 90
    assert cloud.event_queries == 1
    assert sum(result["pull_rows"] for result in results) == 200
    assert sum(result["pull_bytes_estimated"] for result in results) < 400_000
    assert store.queue_calls == 1


def test_two_idle_pcs_each_download_the_blocked_page_only_once():
    pairs = [(_CursorStore(), _MeasuredCloud()) for _ in range(2)]

    for store, cloud in pairs:
        service = AdmissionSyncService(store, cloud)
        for _ in range(90):
            _cycle(service)

    assert sum(cloud.event_queries for _store, cloud in pairs) == 2
    assert all(store.cursor == 249 for store, _cloud in pairs)


def test_current_turn_conflict_is_not_skipped_and_guard_blocks_repeat_payload():
    store = _CursorStore()
    cloud = _MeasuredCloud(blocker_source=CURRENT_SOURCE, blocker_turn=3946)
    service = AdmissionSyncService(store, cloud)

    first = _cycle(service)
    second = _cycle(service)

    assert first["checkpoint_recovered"] == 0
    assert first["pull_rows"] == 200
    assert second["pull_requests"] == 0
    assert second["pull_rows"] == 0
    assert store.cursor == 49
    assert cloud.event_queries == 1
    assert cloud.projection_checks == 0


def test_expired_cursor_uses_current_turn_projection_without_full_history_pull():
    store = _CursorStore()
    cloud = _MeasuredCloud()
    original_window = cloud.event_window

    def expired_window():
        window = original_window()
        window["minimum_available_sequence"] = 100
        return window

    cloud.event_window = expired_window
    service = AdmissionSyncService(store, cloud)

    result = _cycle(service)

    assert result["checkpoint_recovered"] == 249
    assert result["pull_requests"] == 0
    assert result["pull_rows"] == 0
    assert store.cursor == 249
    assert cloud.event_queries == 0
    assert cloud.current_turn_queries == 1


def test_new_event_after_idle_checkpoint_uses_one_incremental_pull():
    store = _CursorStore(block=False)
    store.cursor = 249
    cloud = _MeasuredCloud()
    cloud.latest_sequence = 249
    service = AdmissionSyncService(store, cloud)

    idle = _cycle(service)
    cloud.latest_sequence = 250
    changed = _cycle(service)
    idle_again = _cycle(service)

    assert idle["pull_requests"] == 0
    assert changed["pull_requests"] == 1
    assert changed["pull_rows"] == 1
    assert changed["cursor_after"] == 250
    assert idle_again["pull_requests"] == 0
    assert cloud.event_queries == 1


def test_retry_delay_is_bounded_exponential_with_jitter():
    delays = [bounded_sync_retry_delay(index, "PC-A") for index in range(1, 8)]

    assert 5 <= delays[0] <= 6
    assert 10 <= delays[1] <= 12
    assert 20 <= delays[2] <= 24
    assert 40 <= delays[3] <= 48
    assert all(60 <= delay <= 72 for delay in delays[4:])


def test_durable_cursor_is_monotonic_and_survives_restart(tmp_path):
    database = tmp_path / "cursor.sqlite3"
    first_store = OfflineAdmissionStore(database)
    first_store.set_last_cloud_cursor(first_store.last_cloud_cursor())
    first_store.set_last_cloud_cursor(249)
    first_store.set_last_cloud_cursor(49)

    restarted_store = OfflineAdmissionStore(database)

    assert restarted_store.last_cloud_cursor() == 249


def test_restart_after_recovery_does_not_redownload_history():
    store = _CursorStore()
    cloud = _MeasuredCloud()

    _cycle(AdmissionSyncService(store, cloud))
    restarted_service = AdmissionSyncService(store, cloud)
    result = _cycle(restarted_service)

    assert store.cursor == 249
    assert cloud.event_queries == 1
    assert result["pull_requests"] == 0
    assert result["pull_rows"] == 0


def test_projection_presence_validates_uuid_and_uses_indexed_identity_query():
    statements = []

    class _Cursor:
        def fetchone(self):
            return (1,)

    class _Connection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, statement, params):
            statements.append((statement, params))
            return _Cursor()

    repository = AdmissionCloudRepository(_Connection)

    assert repository.projection_has_attention("invalid") is False
    assert repository.projection_has_attention(ENTITY_UUID) is True
    assert "global_attention_id=%s::UUID" in statements[0][0]


def test_callback_pull_advances_applied_and_duplicate_events_without_replay():
    store = _CallbackStore()
    store.applied.add(EVENT_UUID)
    store.materialized.add(EVENT_UUID)
    cloud = _MeasuredCloud()
    cloud.latest_sequence = 2
    cloud.events_after = lambda *_args, **_kwargs: [
        {"sequence": 1, "event_uuid": EVENT_UUID},
        {"sequence": 2, "event_uuid": ENTITY_UUID},
    ]
    service = AdmissionSyncService(store, cloud)

    def materialize(event):
        store.materialized.add(str(event["event_uuid"]))

    applied = service.pull_cloud_changes(materialize, limit=10)

    assert applied == 1
    assert store.cursor == 2
    assert ENTITY_UUID in store.applied


def test_callback_conflict_stops_page_without_advancing_cursor():
    store = _CallbackStore()
    cloud = _MeasuredCloud()
    cloud.latest_sequence = 1
    cloud.events_after = lambda *_args, **_kwargs: [
        {"sequence": 1, "event_uuid": EVENT_UUID}
    ]
    service = AdmissionSyncService(store, cloud)

    def conflict(_event):
        raise SyncConflict("test")

    assert service.pull_cloud_changes(conflict, limit=10) == 0
    assert store.cursor == 0


def test_historical_recovery_refuses_unverified_or_unmaterialized_blocker():
    store = _CursorStore()
    cloud = _MeasuredCloud()
    service = AdmissionSyncService(store, cloud)

    assert (
        service.recover_stalled_historical_cursor(
            operational_source_id=CURRENT_SOURCE,
            turn_id=3946,
        )
        == 0
    )

    service._stalled_event = {
        "sequence": 50,
        "entity_uuid": ENTITY_UUID,
        "payload_json": {
            "operational_source_id": LEGACY_SOURCE,
            "turn_id": 240,
        },
    }
    service._last_pull_metrics["latest_sequence"] = 249
    cloud.projection_has_attention = lambda _value: False
    assert (
        service.recover_stalled_historical_cursor(
            operational_source_id=CURRENT_SOURCE,
            turn_id=3946,
        )
        == 0
    )

    cloud.projection_has_attention = lambda _value: True
    cloud.current_turn_attention_events = lambda **_kwargs: [
        {"sequence": 249, "event_uuid": EVENT_UUID}
    ]
    assert (
        service.recover_stalled_historical_cursor(
            operational_source_id=CURRENT_SOURCE,
            turn_id=3946,
        )
        == 0
    )


def test_coordinator_backoff_resets_after_success_and_grows_after_failure():
    class _Logger:
        def warning(self, *_args):
            return None

    class _Runtime:
        device_id = "PC-A"
        logger = _Logger()

    coordinator = _HybridCoordinator(_Runtime())
    coordinator._record_connection_failure()
    first_deadline = coordinator._retry_not_before
    coordinator._record_connection_failure()

    assert coordinator._failure_count == 2
    assert coordinator._retry_not_before > first_deadline

    coordinator._sync_succeeded({"offline": False})

    assert coordinator._failure_count == 0
    assert coordinator._retry_not_before == 0
    coordinator.stop()


def test_operational_logging_binding_is_noop_without_rotating_handler():
    class _Database:
        __module__ = "module_not_loaded_for_test"

    import logging

    logger = logging.getLogger("test.egress.no-handler")
    _bind_operational_file_logging(logger, _Database)

    assert logger.handlers == []
