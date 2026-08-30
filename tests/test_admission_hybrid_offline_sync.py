from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from admission_hybrid import (
    POSTGRES_HYBRID_SCHEMA,
    SQLITE_HYBRID_SCHEMA,
    SYNC_TICK_SECONDS,
    AdmissionCloudRepository,
    AdmissionWriteBlocked,
    AdmissionWriteGuard,
    OfflineAdmissionStore,
    OperationalSession,
    StationRole,
    SyncEvent,
    deterministic_event_order_key,
    select_effective_turn_interval,
)
from offline_auth import OfflineAuthCache


def operational_session() -> OperationalSession:
    return OperationalSession(
        operational_session_id="55555555-5555-4555-8555-555555555555",
        active_username="ADMIN",
        active_user_id="7",
        active_user_display_name="Administrador",
        primary_device_id="PC-1",
        primary_login_session_id="login-1",
        turn_id=316,
        operational_source_id="44444444-4444-4444-8444-444444444444",
        status="ACTIVE",
        generation=5,
    )


def create_v15_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as con:
        con.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY AUTOINCREMENT,nombre TEXT NOT NULL,sexo TEXT,
              edad_num INTEGER,unidad TEXT,cedula TEXT,telefono TEXT,direccion TEXT,
              nacionalidad TEXT,ars TEXT,nss TEXT
            );
            CREATE TABLE dias_operativos(
              id INTEGER PRIMARY KEY,fecha_base TEXT,fecha_inicio TEXT,fecha_fin TEXT,
              estado TEXT
            );
            CREATE TABLE turnos(
              id INTEGER PRIMARY KEY,dia_operativo_id INTEGER,fecha_inicio TEXT,
              fecha_inicio_real TEXT,estado TEXT
            );
            CREATE TABLE atenciones(
              id INTEGER PRIMARY KEY AUTOINCREMENT,paciente_id INTEGER NOT NULL,
              dia_operativo_id INTEGER NOT NULL,turno_id INTEGER NOT NULL,nombre TEXT NOT NULL,
              sexo TEXT,edad_num INTEGER,unidad TEXT,cedula TEXT,telefono TEXT,direccion TEXT,
              nacionalidad TEXT,ars TEXT,hoja TEXT,fecha TEXT,hora TEXT,tipo_atencion TEXT,
              estado TEXT,nss TEXT
            );
            INSERT INTO dias_operativos VALUES(1,'2026-08-12','2026-08-12','2026-08-13','ABIERTO');
            INSERT INTO turnos VALUES(1,1,'2026-08-12','2026-08-12','ABIERTO');
            """
        )


def create_attention(path: Path, suffix: int) -> None:
    with closing(sqlite3.connect(path)) as con:
        patient_id = con.execute(
            "INSERT INTO pacientes(nombre,ars,nss) VALUES(?,?,?)",
            (f"P{suffix}", "HUMANO", f"{suffix:09d}"),
        ).lastrowid
        con.execute(
            """INSERT INTO atenciones(
                 paciente_id,dia_operativo_id,turno_id,nombre,ars,hoja,fecha,hora,
                 tipo_atencion,estado,nss
               ) VALUES(?,1,1,?,'HUMANO','GENERAL','2026-08-12','12:00',
                        'EMERGENCIA','ACTIVA',?)""",
            (patient_id, f"P{suffix}", f"{suffix:09d}"),
        )
        con.commit()


def envelope(event, cloud_sequence: int) -> dict:
    return {
        "sequence": cloud_sequence,
        "event_uuid": event.event_uuid,
        "entity_type": event.entity_type,
        "entity_uuid": event.entity_uuid,
        "operation": event.operation,
        "payload_json": dict(event.payload),
        "operational_session_id": event.operational_session_id,
        "operational_source_id": event.operational_source_id,
        "turn_id": event.turn_id,
        "generation": event.generation,
        "origin_device_id": event.device_id,
        "origin_user_id": event.origin_user_id,
        "origin_username": event.origin_username,
        "created_at_device": event.created_at_device,
        "created_at_effective_utc": event.created_at_effective_utc,
        "device_local_sequence": event.device_local_sequence,
        "resulting_version": int(event.payload.get("version") or 1),
    }


def test_local_creation_assigns_uuid_sequence_and_atomic_outbox(tmp_path: Path):
    database = tmp_path / "pc1.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(operational_session(), device_id="PC-1")
    create_attention(database, 1)
    create_attention(database, 2)
    events = store.pending_events(10)
    assert len(events) == 2
    assert [event.device_local_sequence for event in events] == [1, 2]
    assert all(uuid.UUID(event.entity_uuid) for event in events)
    assert all(uuid.UUID(str(event.payload["global_patient_id"])) for event in events)
    assert all(event.operational_source_id == operational_session().operational_source_id for event in events)
    assert all(event.turn_id == 316 for event in events)
    assert store.pending_count() == 2


def test_sync_schemas_contain_required_hybrid_metadata():
    for column in (
        "operational_source_id",
        "turn_id",
        "origin_user_id",
        "origin_username",
        "created_at_device",
        "created_at_effective_utc",
        "device_local_sequence",
        "server_time_offset_ms",
    ):
        assert column in SQLITE_HYBRID_SCHEMA
        assert column in POSTGRES_HYBRID_SCHEMA
    assert "cloud_event_seq" in POSTGRES_HYBRID_SCHEMA


def test_same_timestamp_has_deterministic_tie_breaker():
    values = [
        {
            "created_at_effective_utc": "2026-08-12T14:31:20.000+00:00",
            "origin_device_id": device,
            "device_local_sequence": sequence,
            "global_attention_id": attention,
        }
        for device, sequence, attention in (
            ("PC-2", 1, "b"),
            ("PC-1", 2, "c"),
            ("PC-1", 1, "a"),
        )
    ]
    first = sorted(values, key=deterministic_event_order_key)
    reordered = list(values)
    reordered.reverse()
    second = sorted(reordered, key=deterministic_event_order_key)
    assert first == second
    assert [(row["origin_device_id"], row["device_local_sequence"]) for row in first] == [
        ("PC-1", 1),
        ("PC-1", 2),
        ("PC-2", 1),
    ]


def test_turn_interval_boundary_belongs_to_new_turn():
    intervals = [
        {
            "turn_id": 10,
            "started_at": "2026-08-12T08:00:00+00:00",
            "ended_at": "2026-08-12T09:00:00+00:00",
        },
        {
            "turn_id": 11,
            "started_at": "2026-08-12T09:00:00+00:00",
            "ended_at": None,
        },
    ]
    assert select_effective_turn_interval(
        intervals, "2026-08-12T08:59:59.999+00:00"
    )["turn_id"] == 10
    assert select_effective_turn_interval(
        intervals, "2026-08-12T09:00:00.000+00:00"
    )["turn_id"] == 11


def test_old_generation_creation_is_reassigned_without_falsifying_capture_user():
    class Result:
        def fetchone(self):
            return {
                "generation": 6,
                "turn_id": 317,
                "active_user_id": "9",
                "active_username": "FERNANDO",
            }

    class Connection:
        def execute(self, _query, _params):
            return Result()

    event = SyncEvent(
        event_uuid=str(uuid.uuid4()),
        entity_type="attention",
        entity_uuid=str(uuid.uuid4()),
        operation="CREATE",
        payload={"admission_username": "ADMIN", "turn_id": 316},
        operational_session_id=operational_session().operational_session_id,
        generation=5,
        device_id="PC-2",
        created_at="2026-08-12T09:05:00+00:00",
        operational_source_id=operational_session().operational_source_id,
        turn_id=316,
        origin_user_id="7",
        origin_username="ADMIN",
        created_at_device="2026-08-12T09:05:00+00:00",
        created_at_effective_utc="2026-08-12T09:05:00+00:00",
        device_local_sequence=4,
    )
    reconciled = AdmissionCloudRepository._reconcile_stale_creation(
        Connection(), event
    )
    assert reconciled.turn_id == 317
    assert reconciled.payload["generation"] == 6
    assert reconciled.payload["captured_by_username"] == "ADMIN"
    assert reconciled.payload["reconciliation_status"] == "OFFLINE_ADJUSTED"


def test_offline_turn_change_is_always_blocked():
    with pytest.raises(AdmissionWriteBlocked, match="conexión"):
        AdmissionWriteGuard().require_primary_turn_change(
            role=StationRole.PRIMARY,
            login_user="ADMIN",
            login_user_id="7",
            device_id="PC-1",
            session=operational_session(),
            generation=5,
            offline=True,
            offline_lease_valid=True,
        )


def test_clock_offset_is_stored_and_large_drift_is_reported(tmp_path: Path):
    database = tmp_path / "pc.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    local = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    result = store.update_server_time_offset(local + timedelta(minutes=6), measured_at=local)
    assert result == {"server_time_offset_ms": 360_000, "drift_detected": True}
    assert store.server_time_offset_ms() == 360_000


def test_cloud_newer_revision_wins_and_stale_local_update_is_audited(tmp_path: Path):
    database = tmp_path / "pc2.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(operational_session(), device_id="PC-2")
    entity_uuid = str(uuid.uuid4())
    patient_uuid = str(uuid.uuid4())
    remote_create = {
        "sequence": 1,
        "event_uuid": str(uuid.uuid4()),
        "entity_type": "attention",
        "entity_uuid": entity_uuid,
        "operation": "CREATE",
        "payload_json": {
            "global_attention_id": entity_uuid,
            "global_patient_id": patient_uuid,
            "name": "P",
            "turn_id": 316,
            "ars": "HUMANO",
            "version": 1,
        },
        "origin_device_id": "PC-1",
        "resulting_version": 1,
    }
    store.apply_remote_event(remote_create)
    with closing(sqlite3.connect(database)) as con:
        con.execute(
            "UPDATE atenciones SET telefono='8090000000' WHERE global_attention_id=?",
            (entity_uuid,),
        )
        con.commit()
    remote_update = {
        **remote_create,
        "event_uuid": str(uuid.uuid4()),
        "operation": "UPDATE",
        "resulting_version": 2,
        "payload_json": {**remote_create["payload_json"], "version": 2, "phone": "8091111111"},
    }
    assert store.apply_remote_event(remote_update) is True
    with closing(sqlite3.connect(database)) as con:
        assert con.execute("SELECT COUNT(*) FROM sync_conflicts").fetchone()[0] == 1
        row = con.execute(
            "SELECT telefono,sync_state,server_revision FROM atenciones WHERE global_attention_id=?",
            (entity_uuid,),
        ).fetchone()
        assert row == ("8091111111", "SYNCED", 2)
        assert con.execute(
            "SELECT sync_status,last_error FROM sync_outbox WHERE entity_uuid=?",
            (entity_uuid,),
        ).fetchone() == ("CONFLICT", "SYNC_STALE_UPDATE_REJECTED")


def test_remote_attention_is_not_requeued_as_legacy_local_creation(tmp_path: Path):
    database = tmp_path / "pc2.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(operational_session(), device_id="PC-2")
    entity_uuid = str(uuid.uuid4())
    store.apply_remote_event(
        {
            "sequence": 1,
            "event_uuid": str(uuid.uuid4()),
            "entity_type": "attention",
            "entity_uuid": entity_uuid,
            "operation": "CREATE",
            "payload_json": {
                "global_attention_id": entity_uuid,
                "global_patient_id": str(uuid.uuid4()),
                "name": "P",
                "turn_id": 316,
                "ars": "HUMANO",
                "version": 1,
                "origin_device_id": "PC-1",
            },
            "origin_device_id": "PC-1",
            "resulting_version": 1,
        }
    )
    assert store.pending_count() == 0
    assert store.queue_missing_attention_events(limit=100) == 0
    assert store.pending_count() == 0


def test_self_origin_cloud_event_restores_a_missing_local_attention(tmp_path: Path):
    database = tmp_path / "pc1-restored.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(operational_session(), device_id="PC-1")
    entity_uuid = str(uuid.uuid4())
    was_applied = store.apply_remote_event(
        {
            "sequence": 7,
            "event_uuid": str(uuid.uuid4()),
            "entity_type": "attention",
            "entity_uuid": entity_uuid,
            "operation": "CREATE",
            "payload_json": {
                "global_attention_id": entity_uuid,
                "global_patient_id": str(uuid.uuid4()),
                "name": "P",
                "turn_id": 316,
                "ars": "HUMANO",
                "version": 1,
                "origin_device_id": "PC-1",
            },
            "origin_device_id": "PC-1",
            "resulting_version": 1,
        }
    )
    assert was_applied is True
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM atenciones WHERE global_attention_id=?",
            (entity_uuid,),
        ).fetchone()[0] == 1


def test_legacy_cloud_identity_collision_creates_alias_not_duplicate(tmp_path: Path):
    database = tmp_path / "legacy-alias.db"
    create_v15_database(database)
    store = OfflineAdmissionStore(database)
    store.configure_runtime_context(operational_session(), device_id="PC-2")
    patient_uuid = str(uuid.uuid4())

    def remote_event(attention_uuid: str, device: str, sequence: int) -> dict:
        return {
            "sequence": sequence,
            "event_uuid": str(uuid.uuid4()),
            "entity_type": "attention",
            "entity_uuid": attention_uuid,
            "operation": "RECONCILE",
            "payload_json": {
                "global_attention_id": attention_uuid,
                "global_patient_id": patient_uuid,
                "name": "P",
                "turn_id": 316,
                "ars": "HUMANO",
                "version": 1,
                "origin_device_id": device,
            },
            "origin_device_id": device,
            "resulting_version": 1,
        }

    first_uuid, alias_uuid = str(uuid.uuid4()), str(uuid.uuid4())
    store.apply_remote_event(remote_event(first_uuid, "PC-1", 1))
    store.apply_remote_event(remote_event(alias_uuid, "CENTRAL-LEGACY", 2))
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1
        alias = connection.execute(
            """SELECT local_attention_id,reason FROM sync_attention_aliases
               WHERE remote_global_attention_id=?""",
            (alias_uuid,),
        ).fetchone()
        assert alias and alias[1] == "LEGACY_CLINICAL_IDENTITY_COLLISION"


def test_offline_auth_is_device_bound_expires_and_stores_no_plaintext(tmp_path: Path):
    cache = OfflineAuthCache(tmp_path / "auth.db")
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    user = {"id": 7, "username": "ADMIN", "full_name": "Admin", "role": "administrador"}
    cache.store_online_auth(user, "secret-123", "PC-1", now=now)
    authenticated = cache.authenticate("admin", "secret-123", "PC-1", now=now)
    assert authenticated and authenticated["_offline_login"] is True
    assert cache.authenticate("ADMIN", "bad", "PC-1", now=now) is None
    assert cache.authenticate("ADMIN", "secret-123", "PC-2", now=now) is None
    assert cache.authenticate(
        "ADMIN", "secret-123", "PC-1", now=now + timedelta(days=31)
    ) is None
    assert cache.list_valid_usernames("PC-1", now=now) == ["ADMIN"]
    assert cache.contains_plaintext_password("secret-123") is False


def test_stress_two_pcs_200_attentions_converge_without_loss_or_duplicates(tmp_path: Path):
    paths = [tmp_path / "pc1.db", tmp_path / "pc2.db"]
    stores = []
    for index, path in enumerate(paths, start=1):
        create_v15_database(path)
        store = OfflineAdmissionStore(path)
        store.configure_runtime_context(operational_session(), device_id=f"PC-{index}")
        stores.append(store)
    for number in range(100):
        create_attention(paths[0], number)
        create_attention(paths[1], 100 + number)
    fixed_time = "2026-08-12T14:31:20.000+00:00"
    for path in paths:
        with closing(sqlite3.connect(path)) as con:
            con.execute(
                "UPDATE sync_outbox SET created_at_effective_utc=?",
                (fixed_time,),
            )
            con.execute(
                "UPDATE atenciones SET created_at_effective_utc=?",
                (fixed_time,),
            )
            con.commit()
    events = [store.pending_events(200) for store in stores]
    assert len(events[0]) == len(events[1]) == 100
    for sequence, event in enumerate(events[0], start=1):
        stores[1].apply_remote_event(envelope(event, sequence))
    for sequence, event in enumerate(events[1], start=101):
        stores[0].apply_remote_event(envelope(event, sequence))
    ordered_ids = []
    for path in paths:
        with closing(sqlite3.connect(path)) as con:
            rows = con.execute(
                """SELECT global_attention_id FROM atenciones
                   ORDER BY created_at_effective_utc,origin_device_id,
                            device_local_sequence,global_attention_id"""
            ).fetchall()
        identities = [str(row[0]) for row in rows]
        assert len(identities) == 200
        assert len(set(identities)) == 200
        ordered_ids.append(identities)
    assert ordered_ids[0] == ordered_ids[1]
    assert SYNC_TICK_SECONDS == 10
