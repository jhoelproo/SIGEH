from __future__ import annotations

from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from admission_hybrid import (
    AdmissionCloudRepository,
    AdmissionWriteGuard,
    OfflineAdmissionStore,
    OperationalSession,
    StationRole,
    SyncEvent,
    evaluate_attention_billing_eligibility,
)


def session() -> OperationalSession:
    return OperationalSession(
        operational_session_id="session-1",
        active_username="FERNANDO",
        active_user_id="7",
        primary_device_id="PC-1",
        primary_login_session_id="login-1",
        turn_id=281,
        operational_source_id="source-1",
        status="ACTIVE",
        generation=2,
    )


class HybridAdmissionTests(unittest.TestCase):
    @staticmethod
    def _create_v15_like_database(path: Path):
        con = sqlite3.connect(path)
        try:
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
                INSERT INTO dias_operativos VALUES(1,'2026-08-11','2026-08-11','2026-08-12','ABIERTO');
                INSERT INTO turnos VALUES(1,1,'2026-08-11','2026-08-11','ABIERTO');
                """
            )
            con.commit()
        finally:
            con.close()

    def test_blank_cedula_is_not_a_billing_rejection(self):
        result = evaluate_attention_billing_eligibility(
            {
                "attention_id": 9,
                "source_status": "ACTIVA",
                "service_type": "EMERGENCIA",
                "canonical_ars": "HUMANO",
                "cedula_snapshot": "",
            },
            {"role": "facturador de auditoria"},
            ars_billing_enabled=True,
        )
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reason_code"], "ELIGIBLE_PENDING")

    def test_guard_blocks_secondary_with_another_user(self):
        decision = AdmissionWriteGuard().can_write_admission(
            login_user="ADMIN",
            device_id="PC-2",
            session=session(),
            generation=2,
            role=StationRole.SECONDARY,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "SECONDARY_USER_MISMATCH")

    def test_guard_accepts_valid_offline_lease(self):
        decision = AdmissionWriteGuard().can_write_admission(
            login_user="FERNANDO",
            device_id="PC-1",
            session=session(),
            generation=2,
            role=StationRole.PRIMARY,
            offline=True,
            offline_lease_valid=True,
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.offline)

    def test_guard_never_treats_missing_role_as_administrator(self):
        decision = AdmissionWriteGuard().can_write_admission(
            login_user="ADMIN",
            device_id="PC-2",
            session=session(),
            generation=2,
            role=StationRole.SECONDARY,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "SECONDARY_USER_MISMATCH")

    def test_guard_keeps_explicit_auditor_read_only_even_when_identity_matches(self):
        decision = AdmissionWriteGuard().can_write_admission(
            login_user="FERNANDO",
            login_user_id="7",
            login_role="facturador de auditoria",
            device_id="PC-1",
            session=session(),
            generation=2,
            role=StationRole.PRIMARY,
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.code, "READONLY_AUDIT_DEFAULT")

    def test_attention_mutation_creates_outbox_in_same_sqlite_database(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pacientes.db"
            con = sqlite3.connect(database)
            try:
                con.executescript(
                    """
                    CREATE TABLE pacientes(id INTEGER PRIMARY KEY, nombre TEXT);
                    CREATE TABLE atenciones(
                      id INTEGER PRIMARY KEY AUTOINCREMENT,paciente_id INTEGER,turno_id INTEGER,
                      nombre TEXT,sexo TEXT,edad_num INTEGER,unidad TEXT,cedula TEXT,telefono TEXT,
                      direccion TEXT,nacionalidad TEXT,ars TEXT,hoja TEXT,fecha TEXT,hora TEXT,
                      tipo_atencion TEXT,estado TEXT,nss TEXT
                    );
                    INSERT INTO pacientes(id,nombre) VALUES(1,'Paciente');
                    """
                )
                con.commit()
            finally:
                con.close()
            store = OfflineAdmissionStore(database)
            store.configure_runtime_context(session(), device_id="PC-1")
            con = sqlite3.connect(database)
            try:
                con.execute(
                    """INSERT INTO atenciones(
                         paciente_id,turno_id,nombre,sexo,edad_num,unidad,cedula,telefono,
                         direccion,nacionalidad,ars,hoja,fecha,hora,tipo_atencion,estado,nss
                       ) VALUES(1,281,'Paciente','Femenino',5,'Años','','','','','HUMANO',
                                'GENERAL','2026-08-10','08:00','EMERGENCIA','ACTIVA','')"""
                )
                outbox = con.execute("SELECT entity_uuid,operation,payload_json FROM sync_outbox").fetchone()
                attention = con.execute("SELECT global_attention_id,version FROM atenciones").fetchone()
                con.commit()
            finally:
                con.close()
            self.assertIsNotNone(outbox)
            self.assertEqual(outbox[1], "CREATE")
            self.assertEqual(outbox[0], attention[0])
            self.assertEqual(attention[1], 1)

    def test_preexisting_attention_is_reconciled_into_outbox_once(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pacientes.db"
            self._create_v15_like_database(database)
            with closing(sqlite3.connect(database)) as con:
                patient_id = con.execute(
                    "INSERT INTO pacientes(nombre,ars,nss) VALUES('Paciente','HUMANO','0001')"
                ).lastrowid
                con.execute(
                    """INSERT INTO atenciones(
                         paciente_id,dia_operativo_id,turno_id,nombre,ars,hoja,fecha,hora,
                         tipo_atencion,estado,nss
                       ) VALUES(?,1,1,'Paciente','HUMANO','GENERAL','2026-08-11',
                                '10:00','EMERGENCIA','ACTIVA','0001')""",
                    (patient_id,),
                )
                con.commit()

            store = OfflineAdmissionStore(database)
            store.configure_runtime_context(session(), device_id="PC-1")
            self.assertEqual(store.queue_missing_attention_events(), 1)
            self.assertEqual(store.queue_missing_attention_events(), 0)
            with closing(sqlite3.connect(database)) as con:
                operation, payload, entity_uuid = con.execute(
                    "SELECT operation,payload_json,entity_uuid FROM sync_outbox"
                ).fetchone()
                attention_uuid = con.execute(
                    "SELECT global_attention_id FROM atenciones"
                ).fetchone()[0]
            self.assertEqual(operation, "RECONCILE")
            self.assertIn('"event_type": "ATTENTION_RECONCILED"', payload)
            self.assertEqual(entity_uuid, attention_uuid)

    def test_remote_attention_is_materialized_once_without_outbox_echo(self):
        with tempfile.TemporaryDirectory() as directory:
            first_path = Path(directory) / "pc1.db"
            second_path = Path(directory) / "pc2.db"
            self._create_v15_like_database(first_path)
            self._create_v15_like_database(second_path)
            first = OfflineAdmissionStore(first_path)
            second = OfflineAdmissionStore(second_path)
            first.configure_runtime_context(session(), device_id="PC-1")
            second.configure_runtime_context(session(), device_id="PC-2")

            with closing(sqlite3.connect(first_path)) as con:
                patient_id = con.execute(
                    "INSERT INTO pacientes(nombre,ars,nss) VALUES('Paciente remoto','HUMANO','0012')"
                ).lastrowid
                con.execute(
                    """INSERT INTO atenciones(
                         paciente_id,dia_operativo_id,turno_id,nombre,sexo,edad_num,unidad,
                         cedula,telefono,direccion,nacionalidad,ars,hoja,fecha,hora,
                         tipo_atencion,estado,nss
                       ) VALUES(?,1,1,'Paciente remoto','Femenino',9,'Años','','','','',
                                'HUMANO','GENERAL','2026-08-11','10:00','EMERGENCIA','ACTIVA','0012')""",
                    (patient_id,),
                )
                con.commit()
            event = first.pending_events(1)[0]
            remote = {
                "sequence": 1,
                "event_uuid": event.event_uuid,
                "entity_type": event.entity_type,
                "entity_uuid": event.entity_uuid,
                "operation": event.operation,
                "payload_json": dict(event.payload),
                "operational_session_id": event.operational_session_id,
                "generation": event.generation,
                "origin_device_id": event.device_id,
                "resulting_version": 1,
            }
            self.assertTrue(second.apply_remote_event(remote))
            self.assertTrue(second.apply_remote_event(remote))
            with closing(sqlite3.connect(second_path)) as con:
                rows = con.execute(
                    "SELECT global_attention_id,nombre,origin_device_id FROM atenciones"
                ).fetchall()
                echoed = con.execute("SELECT COUNT(*) FROM sync_outbox").fetchone()[0]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0][0], event.entity_uuid)
            self.assertEqual(rows[0][1], "Paciente remoto")
            self.assertEqual(rows[0][2], "PC-1")
            self.assertEqual(echoed, 0)

    def test_editing_a_remote_attention_creates_next_version_event(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "pc2.db"
            self._create_v15_like_database(database)
            store = OfflineAdmissionStore(database)
            store.configure_runtime_context(session(), device_id="PC-2")
            entity_uuid = "a" * 32
            remote = {
                "sequence": 1,
                "event_uuid": "b" * 32,
                "entity_type": "attention",
                "entity_uuid": entity_uuid,
                "operation": "CREATE",
                "payload_json": {
                    "global_attention_id": entity_uuid,
                    "global_patient_id": "c" * 32,
                    "attention_id": 77,
                    "patient_id": 88,
                    "turn_id": 281,
                    "name": "Paciente",
                    "ars": "HUMANO",
                    "source_status": "ACTIVA",
                    "version": 1,
                    "operational_source_id": "source-1",
                },
                "operational_session_id": "session-1",
                "generation": 2,
                "origin_device_id": "PC-1",
                "resulting_version": 1,
            }
            store.apply_remote_event(remote)
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "UPDATE atenciones SET telefono='8095550000' WHERE global_attention_id=?",
                    (entity_uuid,),
                )
                con.commit()
            event = store.pending_events(1)[0]
            self.assertEqual(event.operation, "UPDATE")
            self.assertEqual(event.base_version, 1)
            self.assertEqual(int(event.payload["version"]), 2)

    def test_cloud_push_validates_session_and_materializes_projection(self):
        class CursorResult:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class FakeConnection:
            def __init__(self):
                self.queries = []

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, query, params=()):
                compact = " ".join(str(query).split())
                self.assert_placeholders(compact, params)
                self.queries.append((compact, tuple(params)))
                if "SELECT active_username,generation,status" in compact:
                    return CursorResult(("FERNANDO", 2, "ACTIVE"))
                if "SELECT station_role FROM admission_operational_devices" in compact:
                    return CursorResult(("PRIMARY",))
                if "SELECT sequence,resulting_version" in compact:
                    return CursorResult(None)
                if "SELECT COALESCE(MAX(resulting_version),0)" in compact:
                    return CursorResult((0,))
                if "INSERT INTO admission_sync_events" in compact:
                    return CursorResult((41,))
                if "UPDATE admission_attention_projection" in compact:
                    return CursorResult(None)
                return CursorResult(None)

            @staticmethod
            def assert_placeholders(query, params):
                if params:
                    assert query.count("%s") == len(params), (query, params)

        connection = FakeConnection()
        repository = AdmissionCloudRepository(lambda: connection)
        event = SyncEvent(
            event_uuid="11111111-1111-4111-8111-111111111111",
            entity_type="attention",
            entity_uuid="22222222-2222-4222-8222-222222222222",
            operation="CREATE",
            payload={
                "attention_id": 1,
                "patient_id": 2,
                "global_attention_id": "22222222-2222-4222-8222-222222222222",
                "global_patient_id": "33333333-3333-4333-8333-333333333333",
                "turn_id": 281,
                "name": "Paciente",
                "ars": "HUMANO",
                "nss": "072907673",
                "service_date": "2026-08-11",
                "service_time": "12:00",
                "source_instance_id": "source-local-1",
                "operational_source_id": "44444444-4444-4444-8444-444444444444",
                "admission_username": "FERNANDO",
            },
            operational_session_id="55555555-5555-4555-8555-555555555555",
            generation=2,
            device_id="PC-1",
            created_at="2026-08-11T12:00:00+00:00",
            base_version=0,
        )
        self.assertEqual(repository.push_event(event), 41)
        self.assertTrue(
            any("INSERT INTO admission_attention_projection" in query for query, _ in connection.queries)
        )
        flattened_params = {
            str(value)
            for _query, params in connection.queries
            for value in params
            if value is not None
        }
        self.assertIn("LISTA", flattened_params)

    def test_two_independent_stations_converge_without_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / "pc1.db", Path(directory) / "pc2.db"]
            for path in paths:
                self._create_v15_like_database(path)
            stores = [OfflineAdmissionStore(path) for path in paths]
            stores[0].configure_runtime_context(session(), device_id="PC-1")
            stores[1].configure_runtime_context(session(), device_id="PC-2")

            def create(store_path, suffix):
                with closing(sqlite3.connect(store_path)) as con:
                    patient_id = con.execute(
                        "INSERT INTO pacientes(nombre,ars,nss) VALUES(?,?,?)",
                        (f"Paciente {suffix}", "HUMANO", f"00{suffix}"),
                    ).lastrowid
                    con.execute(
                        """INSERT INTO atenciones(
                             paciente_id,dia_operativo_id,turno_id,nombre,ars,hoja,
                             fecha,hora,tipo_atencion,estado,nss
                           ) VALUES(?,1,1,?,'HUMANO','GENERAL','2026-08-11',
                                    '10:00','EMERGENCIA','ACTIVA',?)""",
                        (patient_id, f"Paciente {suffix}", f"00{suffix}"),
                    )
                    con.commit()

            def envelope(event, sequence):
                return {
                    "sequence": sequence,
                    "event_uuid": event.event_uuid,
                    "entity_type": event.entity_type,
                    "entity_uuid": event.entity_uuid,
                    "operation": event.operation,
                    "payload_json": dict(event.payload),
                    "operational_session_id": event.operational_session_id,
                    "generation": event.generation,
                    "origin_device_id": event.device_id,
                    "resulting_version": 1,
                }

            create(paths[0], "1")
            event_pc1 = stores[0].pending_events(1)[0]
            self.assertTrue(stores[1].apply_remote_event(envelope(event_pc1, 1)))
            create(paths[1], "2")
            event_pc2 = [
                event for event in stores[1].pending_events(10)
                if event.device_id == "PC-2"
            ][0]
            self.assertTrue(stores[0].apply_remote_event(envelope(event_pc2, 2)))

            for path in paths:
                with closing(sqlite3.connect(path)) as con:
                    rows = con.execute(
                        "SELECT global_attention_id FROM atenciones ORDER BY global_attention_id"
                    ).fetchall()
                self.assertEqual(len(rows), 2)
                self.assertEqual(len({row[0] for row in rows}), 2)


if __name__ == "__main__":
    unittest.main()
