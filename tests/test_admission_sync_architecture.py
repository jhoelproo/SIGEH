from __future__ import annotations

import sqlite3
import hashlib
import threading
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from admission_hybrid import (
    AdmissionSeedService,
    AdmissionSyncService,
    OfflineAdmissionStore,
    OperationalSession,
    SyncConflict,
)
from sqlite_write_coordinator import (
    SQLITE_BUSY_TIMEOUT_MS,
    assert_private_local_database,
    connect_local_sqlite,
    prepare_sqlite_database,
)


def _session() -> OperationalSession:
    return OperationalSession(
        operational_session_id="55555555-5555-4555-8555-555555555555",
        active_username="ADMIN",
        active_user_id="7",
        active_user_display_name="Administrador",
        primary_device_id="PC-1",
        primary_login_session_id="login-1",
        turn_id=350,
        operational_source_id="44444444-4444-4444-8444-444444444444",
        status="ACTIVE",
        generation=80,
    )


def _database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
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
              estado TEXT,nss TEXT,anulada_at TEXT,anulada_por TEXT,anulada_motivo TEXT
            );
            INSERT INTO dias_operativos VALUES(1,'2026-08-14','2026-08-14','2026-08-15','ABIERTO');
            INSERT INTO turnos VALUES(1,1,'2026-08-14','2026-08-14','ABIERTO');
            """
        )


def _create_attention(path: Path, number: int) -> None:
    connection = connect_local_sqlite(path, operation="test-create-attention")
    try:
        connection.execute("BEGIN IMMEDIATE")
        patient_id = connection.execute(
            "INSERT INTO pacientes(nombre,ars,nss) VALUES(?,?,?)",
            (f"P{number}", "HUMANO", f"{number:09d}"),
        ).lastrowid
        connection.execute(
            """INSERT INTO atenciones(
                 paciente_id,dia_operativo_id,turno_id,nombre,ars,hoja,fecha,hora,
                 tipo_atencion,estado,nss
               ) VALUES(?,1,1,?,'HUMANO','GENERAL','2026-08-14','12:00',
                        'EMERGENCIA','ACTIVA',?)""",
            (patient_id, f"P{number}", f"{number:09d}"),
        )
        connection.commit()
    finally:
        connection.close()


class _MemoryCloud:
    def __init__(self):
        self.events: list[dict] = []
        self.by_event: dict[str, int] = {}
        self.latest: dict[str, dict] = {}
        self.seeds: dict[str, dict] = {}
        self._lock = threading.Lock()

    def server_time(self):
        return datetime.now(timezone.utc)

    def backfill_projection_events(self, *, limit=500):
        return 0

    def rematerialize_attention_events(self, _entity_ids):
        return 0

    def push_events(self, events):
        return {event.event_uuid: self.push_event(event) for event in events}

    def push_event(self, event):
        with self._lock:
            if event.event_uuid in self.by_event:
                return self.by_event[event.event_uuid]
            latest = self.latest.get(event.entity_uuid)
            operation = event.operation.upper()
            current_revision = int((latest or {}).get("resulting_version") or 0)
            latest_deleted = bool(latest and latest["operation"] == "DELETE")
            if latest_deleted and operation not in {"DELETE", "RESTORE_ATTENTION"}:
                raise SyncConflict('{"reason_code":"STALE_RECORD_SUPPRESSED_BY_TOMBSTONE"}')
            if operation not in {"DELETE", "RECONCILE"} and current_revision != event.base_version:
                raise SyncConflict('{"reason_code":"SYNC_STALE_UPDATE_REJECTED"}')
            sequence = len(self.events) + 1
            revision = current_revision + 1
            envelope = {
                "sequence": sequence,
                "cloud_event_seq": sequence,
                "event_uuid": event.event_uuid,
                "entity_type": event.entity_type,
                "entity_uuid": event.entity_uuid,
                "operation": operation,
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
                "resulting_version": revision,
            }
            self.events.append(envelope)
            self.by_event[event.event_uuid] = sequence
            self.latest[event.entity_uuid] = envelope
            return sequence

    def events_after(self, cursor, *, limit=200):
        with self._lock:
            return [dict(event) for event in self.events if event["sequence"] > cursor][:limit]

    def begin_seed(
        self,
        *,
        central_seed_id,
        legacy_source_instance_id,
        source_fingerprint,
        origin_device_id,
        schema_version=1,
    ):
        row = self.seeds.get(central_seed_id)
        if row and row["status"] == "COMPLETED":
            return False
        self.seeds[central_seed_id] = {
            "status": "RUNNING",
            "source": legacy_source_instance_id,
            "fingerprint": source_fingerprint,
            "device": origin_device_id,
            "schema": schema_version,
        }
        return True

    def complete_seed(self, *, central_seed_id, imported_records):
        self.seeds[central_seed_id].update(
            status="COMPLETED", imported_records=imported_records
        )


def _store(path: Path, device: str) -> OfflineAdmissionStore:
    store = OfflineAdmissionStore(path)
    store.configure_runtime_context(_session(), device_id=device)
    return store


def test_wal_busy_timeout_and_single_writer_remove_lock_collisions(tmp_path: Path):
    path = tmp_path / "private-pc.db"
    assert prepare_sqlite_database(path) == "WAL"
    setup = connect_local_sqlite(path)
    setup.execute("CREATE TABLE writes(id INTEGER PRIMARY KEY AUTOINCREMENT,source TEXT)")
    setup.commit()
    setup.close()
    failures: list[BaseException] = []

    def writer(source: str):
        try:
            for _ in range(40):
                connection = connect_local_sqlite(path, operation=source)
                try:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("INSERT INTO writes(source) VALUES(?)", (source,))
                    connection.commit()
                finally:
                    connection.close()
        except BaseException as exc:  # test captures worker failures
            failures.append(exc)

    threads = [
        threading.Thread(target=writer, args=(name,), name=name)
        for name in ("sync-pull", "attention-create", "pdf-metadata", "excel-state")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert failures == []
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].upper() == "WAL"
        connection.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 160


def test_private_replica_rejects_onedrive_and_unc_paths():
    for path in (r"C:\Users\demo\OneDrive\patients.db", r"\\server\share\patients.db"):
        try:
            assert_private_local_database(path)
        except ValueError:
            pass
        else:
            raise AssertionError("A shared SQLite path was accepted")


def test_distribution_internal_data_is_an_isolated_replica_location():
    path = r"C:\Users\demo\OneDrive\Desktop\Hospital\_internal\data\admission.db"
    assert assert_private_local_database(path).endswith(r"_internal\data\admission.db")


def test_two_pcs_propagate_incrementally_and_bootstrap_full_history(tmp_path: Path):
    pc1_path, pc2_path, new_path = (
        tmp_path / "pc1.db",
        tmp_path / "pc2.db",
        tmp_path / "new.db",
    )
    for path in (pc1_path, pc2_path, new_path):
        _database(path)
    cloud = _MemoryCloud()
    pc1 = _store(pc1_path, "PC-1")
    pc2 = _store(pc2_path, "PC-2")
    sync1 = AdmissionSyncService(pc1, cloud)
    sync2 = AdmissionSyncService(pc2, cloud)
    _create_attention(pc1_path, 1)
    started = time.perf_counter()
    assert sync1.synchronize_once()["pushed"] == 1
    assert sync2.synchronize_once()["pulled"] >= 1
    propagation_ms = (time.perf_counter() - started) * 1000.0
    with closing(sqlite3.connect(pc2_path)) as connection:
        assert connection.execute("SELECT nombre FROM atenciones").fetchone()[0] == "P1"
    new_store = _store(new_path, "PC-3")
    downloaded = AdmissionSyncService(new_store, cloud).bootstrap_replica(batch_size=1)
    assert downloaded == 1
    with closing(sqlite3.connect(new_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1
    assert propagation_ms < 2_000


def test_secondary_detail_sheet_event_is_visible_on_primary(tmp_path: Path):
    pc1_path, pc2_path = tmp_path / "pc1.db", tmp_path / "pc2.db"
    for path in (pc1_path, pc2_path):
        _database(path)
    cloud = _MemoryCloud()
    pc1, pc2 = _store(pc1_path, "PC-1"), _store(pc2_path, "PC-2")
    sync1, sync2 = AdmissionSyncService(pc1, cloud), AdmissionSyncService(pc2, cloud)
    _create_attention(pc1_path, 1)
    sync1.synchronize_once()
    sync2.synchronize_once()
    with closing(sqlite3.connect(pc2_path)) as connection:
        local_attention_id = connection.execute("SELECT id FROM atenciones").fetchone()[0]
        entity_uuid = connection.execute(
            "SELECT global_attention_id FROM atenciones WHERE id=?", (local_attention_id,)
        ).fetchone()[0]
    assert pc2.queue_detail_sheet_generated(local_attention_id) is True
    assert sync2.synchronize_once()["pushed"] == 1
    sync1.synchronize_once()
    assert cloud.latest[entity_uuid]["operation"] == "DETAIL_SHEET_GENERATED"
    assert cloud.latest[entity_uuid]["payload_json"]["turn_id"] == 350
    with closing(sqlite3.connect(pc1_path)) as connection:
        assert connection.execute(
            "SELECT hoja,sync_state FROM atenciones WHERE global_attention_id=?",
            (entity_uuid,),
        ).fetchone() == ("GENERAL", "SYNCED")


def test_remote_tombstone_suppresses_stale_offline_update(tmp_path: Path):
    pc1_path, pc2_path = tmp_path / "pc1.db", tmp_path / "pc2.db"
    for path in (pc1_path, pc2_path):
        _database(path)
    cloud = _MemoryCloud()
    pc1, pc2 = _store(pc1_path, "PC-1"), _store(pc2_path, "PC-2")
    sync1, sync2 = AdmissionSyncService(pc1, cloud), AdmissionSyncService(pc2, cloud)
    _create_attention(pc1_path, 1)
    sync1.synchronize_once()
    sync2.synchronize_once()
    with closing(sqlite3.connect(pc2_path)) as connection:
        entity_uuid = connection.execute("SELECT global_attention_id FROM atenciones").fetchone()[0]
        connection.execute(
            "UPDATE atenciones SET telefono='8091111111' WHERE global_attention_id=?",
            (entity_uuid,),
        )
        connection.commit()
    with closing(sqlite3.connect(pc1_path)) as connection:
        connection.execute(
            """UPDATE atenciones SET estado='ANULADA',anulada_at=?,anulada_por='7',
                      anulada_motivo='Eliminación autorizada'
                WHERE global_attention_id=?""",
            (datetime.now(timezone.utc).isoformat(), entity_uuid),
        )
        connection.commit()
    sync1.synchronize_once()
    sync2.synchronize_once()
    assert cloud.latest[entity_uuid]["operation"] == "DELETE"
    with closing(sqlite3.connect(pc2_path)) as connection:
        row = connection.execute(
            "SELECT estado,is_deleted,sync_state FROM atenciones WHERE global_attention_id=?",
            (entity_uuid,),
        ).fetchone()
        assert row == ("ANULADA", 1, "TOMBSTONED")
        assert connection.execute(
            "SELECT sync_status,last_error FROM sync_outbox WHERE entity_uuid=?",
            (entity_uuid,),
        ).fetchone() == ("SUPERSEDED", "SYNC_TOMBSTONE_WON")


def test_offline_explicit_delete_wins_over_normal_remote_update(tmp_path: Path):
    pc1_path, pc2_path = tmp_path / "pc1.db", tmp_path / "pc2.db"
    for path in (pc1_path, pc2_path):
        _database(path)
    cloud = _MemoryCloud()
    pc1, pc2 = _store(pc1_path, "PC-1"), _store(pc2_path, "PC-2")
    sync1, sync2 = AdmissionSyncService(pc1, cloud), AdmissionSyncService(pc2, cloud)
    _create_attention(pc1_path, 1)
    sync1.synchronize_once()
    sync2.synchronize_once()
    with closing(sqlite3.connect(pc1_path)) as connection:
        entity_uuid = connection.execute("SELECT global_attention_id FROM atenciones").fetchone()[0]
    with closing(sqlite3.connect(pc2_path)) as connection:
        connection.execute("UPDATE atenciones SET estado='ANULADA' WHERE global_attention_id=?", (entity_uuid,))
        connection.commit()
    with closing(sqlite3.connect(pc1_path)) as connection:
        connection.execute("UPDATE atenciones SET telefono='8092222222' WHERE global_attention_id=?", (entity_uuid,))
        connection.commit()
    sync1.synchronize_once()
    sync2.synchronize_once()
    sync1.synchronize_once()
    assert cloud.latest[entity_uuid]["operation"] == "DELETE"
    with closing(sqlite3.connect(pc1_path)) as connection:
        assert connection.execute(
            "SELECT is_deleted FROM atenciones WHERE global_attention_id=?", (entity_uuid,)
        ).fetchone()[0] == 1


def test_historical_seed_is_idempotent_and_uuid_mapping_is_stable(tmp_path: Path):
    path = tmp_path / "history.db"
    _database(path)
    for number in range(3):
        _create_attention(path, number)
    cloud = _MemoryCloud()
    store = _store(path, "PC-1")
    with closing(sqlite3.connect(path)) as connection:
        first_ids = [row[0] for row in connection.execute(
            "SELECT global_attention_id FROM atenciones ORDER BY id"
        )]
    service = AdmissionSeedService(AdmissionSyncService(store, cloud))
    first = service.seed_local_history(origin_device_id="PC-1", batch_size=2)
    second = service.seed_local_history(origin_device_id="PC-1", batch_size=2)
    OfflineAdmissionStore(path).initialize()
    with closing(sqlite3.connect(path)) as connection:
        second_ids = [row[0] for row in connection.execute(
            "SELECT global_attention_id FROM atenciones ORDER BY id"
        )]
    assert first["imported"] == 3
    assert second == {
        "central_seed_id": first["central_seed_id"],
        "imported": 0,
        "already_completed": True,
    }
    assert len(cloud.latest) == 3
    assert first_ids == second_ids
    assert len(set(first_ids)) == 3
    assert all(uuid.UUID(value) for value in first_ids)


def test_seed_resumes_when_local_ack_exists_but_central_marker_was_lost(tmp_path: Path):
    path = tmp_path / "history-resume.db"
    _database(path)
    for number in range(3):
        _create_attention(path, number)
    store = _store(path, "PC-1")
    source_id = store.legacy_source_instance_id()
    fingerprint = hashlib.sha256(
        f"{source_id}:v{AdmissionSeedService.SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()
    seed_id = str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"hospital-admission-central-seed:{fingerprint}",
        )
    )
    first_cloud = _MemoryCloud()
    store.queue_missing_attention_events(limit=10, central_seed_id=seed_id)
    AdmissionSyncService(store, first_cloud).push_outbox(limit=20)

    replacement_cloud = _MemoryCloud()
    result = AdmissionSeedService(
        AdmissionSyncService(store, replacement_cloud)
    ).seed_local_history(origin_device_id="PC-1", batch_size=10)

    assert result["imported"] == 3
    assert len(replacement_cloud.latest) == 3
