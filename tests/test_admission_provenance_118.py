import json
import sqlite3
from dataclasses import replace

from admission_hybrid import OfflineAdmissionStore
from tests import test_admission_hybrid as fixtures


def test_edit_after_relay_preserves_admission_author_and_origin_turn(tmp_path):
    database = tmp_path / "admission.db"
    fixtures.HybridAdmissionTests._create_v15_like_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(fixtures.session(), device_id="PC-1")
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO pacientes(id,nombre) VALUES(1,'SYNTHETIC')")
        connection.execute("""INSERT INTO atenciones(
            paciente_id,dia_operativo_id,turno_id,nombre,estado,tipo_atencion,hoja
        ) VALUES(1,1,1,'SYNTHETIC','ACTIVA','EMERGENCIA','GENERAL')""")
        original = json.loads(
            connection.execute(
                "SELECT payload_json FROM sync_outbox WHERE operation='CREATE'"
            ).fetchone()[0]
        )
    store.configure_runtime_context(
        replace(
            fixtures.session(),
            turn_id=282,
            active_username="EDITOR",
            active_user_id="8",
        ),
        device_id="PC-1",
    )
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE atenciones SET hoja='PEDIATRIA' WHERE id=1")
        payload, actor = connection.execute("""SELECT payload_json,origin_username
            FROM sync_outbox WHERE operation='DETAIL_SHEET_GENERATED'""").fetchone()
    corrected = json.loads(payload)
    assert corrected["admission_username"] == original["admission_username"]
    assert corrected["turn_id"] == original["turn_id"]
    assert actor == "EDITOR"
    assert corrected["operational_username"] == "EDITOR"
    assert corrected["captured_by_username"] == original["captured_by_username"]
    assert corrected["created_at_device"] == original["created_at_device"]
    assert corrected["created_at_effective_utc"] == original["created_at_effective_utc"]
    assert corrected["origin_device_id"] == original["origin_device_id"]

    replica = tmp_path / "replica.db"
    fixtures.HybridAdmissionTests._create_v15_like_database(replica)
    second = OfflineAdmissionStore(replica)
    second.configure_runtime_context(fixtures.session(), device_id="PC-2")
    for sequence, event in enumerate(store.pending_events(10), 1):
        assert second.apply_remote_event(
            {
                "sequence": sequence,
                "event_uuid": event.event_uuid,
                "entity_type": event.entity_type,
                "entity_uuid": event.entity_uuid,
                "operation": event.operation,
                "payload_json": dict(event.payload),
                "operational_session_id": event.operational_session_id,
                "generation": event.generation,
                "turn_id": event.turn_id,
                "origin_username": event.origin_username,
                "origin_device_id": event.device_id,
                "resulting_version": sequence,
            }
        )
    with sqlite3.connect(replica) as connection:
        assert (
            connection.execute("""SELECT admission_username,
            operational_turn_id,captured_by_username,hoja FROM atenciones""").fetchone()
            == ("FERNANDO", 281, "FERNANDO", "PEDIATRIA")
        )
