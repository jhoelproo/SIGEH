from __future__ import annotations

import uuid
from types import SimpleNamespace

from admission_hybrid import (
    AdmissionCloudRepository,
    AdmissionSyncService,
    OfflineAdmissionStore,
    SyncConflict,
)


def _event(sequence: int) -> dict[str, object]:
    global_attention_id = str(uuid.uuid4())
    return {
        "sequence": sequence,
        "event_uuid": str(uuid.uuid4()),
        "entity_type": "attention",
        "entity_uuid": global_attention_id,
        "operation": "UPDATE",
        "payload_json": {"global_attention_id": global_attention_id},
    }


def test_failed_materialization_does_not_ack_or_skip_the_cursor(tmp_path, monkeypatch):
    store = OfflineAdmissionStore(tmp_path / "replica.db")
    store.initialize()
    first, failed, later = _event(1), _event(2), _event(3)
    materialized: set[str] = set()

    def apply_remote_event(_self, event):
        if event["event_uuid"] == failed["event_uuid"]:
            raise SyncConflict("turn mirror pending")
        materialized.add(str(event["event_uuid"]))
        return True

    monkeypatch.setattr(OfflineAdmissionStore, "apply_remote_event", apply_remote_event)
    monkeypatch.setattr(
        OfflineAdmissionStore,
        "_remote_event_is_materialized",
        lambda _self, _con, event: str(event["event_uuid"]) in materialized,
    )

    assert store.apply_remote_events([first, failed, later]) == 1
    assert store.last_cloud_cursor() == 1
    assert store.already_applied(str(first["event_uuid"])) is True
    assert store.already_applied(str(failed["event_uuid"])) is False
    assert store.already_applied(str(later["event_uuid"])) is False


def test_stale_ack_is_repaired_before_a_hydration_is_accepted(tmp_path, monkeypatch):
    store = OfflineAdmissionStore(tmp_path / "replica.db")
    store.initialize()
    event = _event(1)
    materialized: set[str] = set()

    def apply_remote_event(_self, value):
        materialized.add(str(value["event_uuid"]))
        return True

    monkeypatch.setattr(OfflineAdmissionStore, "apply_remote_event", apply_remote_event)
    monkeypatch.setattr(
        OfflineAdmissionStore,
        "_remote_event_is_materialized",
        lambda _self, _con, value: str(value["event_uuid"]) in materialized,
    )
    store.mark_applied_and_advance(str(event["event_uuid"]), 1)

    assert store.hydrate_remote_events([event]) == 1
    assert store.already_applied(str(event["event_uuid"])) is True
    assert str(event["event_uuid"]) in materialized


def test_current_turn_reconciliation_repairs_only_missing_projection_ids():
    events = [_event(1), _event(2), _event(3)]

    class Replica:
        def __init__(self):
            self.materialized = {str(events[0]["event_uuid"]), str(events[2]["event_uuid"])}
            self.hydrated: list[dict[str, object]] = []

        def is_remote_event_materialized(self, event):
            return str(event["event_uuid"]) in self.materialized

        def hydrate_remote_events(self, values):
            self.hydrated = list(values)
            self.materialized.update(str(value["event_uuid"]) for value in values)
            return 1

    class Projection:
        def __init__(self):
            self.calls = 0

        def current_turn_attention_events(self, **kwargs):
            self.calls += 1
            assert kwargs["operational_source_id"] == "source-a"
            assert kwargs["turn_id"] == 77
            return list(events)

    replica = Replica()
    projection = Projection()
    service = AdmissionSyncService(replica, projection)

    assert service.reconcile_current_turn(
        operational_source_id="source-a", turn_id=77
    ) == 1
    assert {value["event_uuid"] for value in replica.hydrated} == {
        value["event_uuid"] for value in events
    }
    assert service.reconcile_current_turn(
        operational_source_id="source-a", turn_id=77
    ) == 0
    assert projection.calls == 1


def test_current_turn_projection_query_uses_both_distributed_identity_parts():
    global_attention_id = str(uuid.uuid4())

    class Cursor:
        def fetchall(self):
            return [
                {
                    "global_attention_id": global_attention_id,
                    "source_instance_id": "legacy-a",
                    "attention_id": 8,
                    "global_patient_id": str(uuid.uuid4()),
                    "patient_id": 3,
                    "operational_session_id": "central-session",
                    "operational_source_id": "source-a",
                    "turn_id": 77,
                    "generation": 4,
                    "origin_device_id": "PC-A",
                    "origin_user_id": "10",
                    "admission_username": "aux-test",
                    "captured_by_username": "aux-test",
                    "patient_name": "",
                    "canonical_ars": "",
                    "nss_snapshot": "",
                    "cedula_snapshot": "",
                    "service_date": "2026-08-23",
                    "service_time": "08:00:00",
                    "service_type": "EMERGENCIA",
                    "specialty": "GENERAL",
                    "has_detail_sheet": False,
                    "source_status": "ACTIVA",
                    "created_at_device": "2026-08-23T08:00:00+00:00",
                    "created_at_effective_utc": "2026-08-23T08:00:00+00:00",
                    "device_local_sequence": 1,
                    "server_revision": 1,
                    "is_deleted": False,
                    "deleted_at": None,
                    "deleted_by_user_id": None,
                    "delete_event_uuid": None,
                    "delete_reason": None,
                    "latest_event_uuid": str(uuid.uuid4()),
                    "latest_sequence": 9,
                    "latest_operation": "UPDATE",
                    "latest_payload": {},
                }
            ]

    class Connection:
        def __init__(self):
            self.sql = ""
            self.params = ()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, params):
            self.sql = str(sql)
            self.params = tuple(params)
            return Cursor()

    connection = Connection()
    repository = AdmissionCloudRepository(lambda: connection)
    events = repository.current_turn_attention_events(
        operational_source_id="source-a", turn_id=77
    )

    assert "p.operational_source_id::TEXT=%s" in connection.sql
    assert "p.turn_id=%s" in connection.sql
    assert connection.params == ("source-a", 77, 500)
    assert events[0]["entity_uuid"] == global_attention_id


def test_authenticated_user_never_opens_or_creates_a_new_turn_from_a_central_snapshot():
    from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App

    applied: list[dict[str, object]] = []
    app = SimpleNamespace(
        session_context=SimpleNamespace(launched_from_billing=True, role="auxiliar"),
        _snapshot_operacional_integrado=lambda: {
            "operational_session_id": "central-session",
            "turn_id": 77,
            "operational_source_id": "source-a",
            "writable": True,
        },
        apply_operational_snapshot=lambda snapshot: applied.append(dict(snapshot)),
    )

    assert App._asegurar_turno_de_sesion(app) is True
    assert applied == [
        {
            "operational_session_id": "central-session",
            "turn_id": 77,
            "operational_source_id": "source-a",
            "writable": True,
        }
    ]
