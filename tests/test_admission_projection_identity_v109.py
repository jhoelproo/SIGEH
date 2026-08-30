from __future__ import annotations

import pytest

from admission_hybrid import (
    AdmissionCloudRepository,
    SyncEvent,
    _resolve_projection_turn_id,
)


@pytest.mark.parametrize(
    ("payload_turn_id", "event_turn_id", "existing_turn_id", "expected"),
    [
        (None, 3946, 3946, 3946),
        ("0", 0, 3946, 3946),
        (3947, 3946, 3946, 3947),
        (None, None, None, 0),
    ],
)
def test_projection_turn_identity_never_downgrades_to_zero(
    payload_turn_id, event_turn_id, existing_turn_id, expected
):
    assert _resolve_projection_turn_id(
        payload_turn_id=payload_turn_id,
        event_turn_id=event_turn_id,
        existing_turn_id=existing_turn_id,
    ) == expected


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _ProjectionConnection:
    def __init__(self):
        self.projection_update_params = None

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split()).upper()
        if normalized.startswith("SELECT IS_DELETED,SERVER_REVISION,TURN_ID"):
            return _Result((False, 1, 3946, 3946))
        if normalized.startswith("UPDATE ADMISSION_ATTENTION_PROJECTION SET PATIENT_ID"):
            self.projection_update_params = params
            return _Result((31,))
        if normalized.startswith("UPDATE ADMISSION_ATTENTION_PROJECTION SET IS_DELETED"):
            return _Result()
        raise AssertionError(f"Unexpected SQL: {normalized}")


def _event(operation, *, payload_turn_id=None, event_turn_id=3946):
    payload = {
        "attention_id": 31,
        "patient_id": 99,
        "name": "PACIENTE PRUEBA",
        "service_date": "2026-08-29",
        "service_time": "07:35 PM",
        "service_type": "EMERGENCIA",
        "source_status": "ACTIVA",
        "source_instance_id": "source-instance",
        "operational_source_id": "748d96bb-808f-4f66-b3fe-d256326b20f9",
    }
    if payload_turn_id is not None:
        payload["turn_id"] = payload_turn_id
    return SyncEvent(
        event_uuid="699631c3-a201-46c8-acab-d722b78b7e9e",
        entity_type="attention",
        entity_uuid="11111111-2222-4333-8444-555555555555",
        operation=operation,
        payload=payload,
        operational_session_id="ed81da91-a6bc-414f-b4c8-119b1d9909e1",
        generation=7,
        device_id="device-hospital",
        created_at="2026-08-29T23:35:29+00:00",
        turn_id=event_turn_id,
    )


@pytest.mark.parametrize(
    "event",
    [
        _event("DETAIL_SHEET_GENERATED"),
        _event("DELETE", payload_turn_id="0", event_turn_id=0),
    ],
)
def test_partial_or_invalid_event_cannot_overwrite_existing_projection_turn(event):
    connection = _ProjectionConnection()

    AdmissionCloudRepository._materialize_attention(connection, event, version=2)

    assert connection.projection_update_params is not None
    assert connection.projection_update_params[1] == 3946
