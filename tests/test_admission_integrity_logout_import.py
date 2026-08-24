import json
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from pathlib import Path

import pytest

import CALCULOS_QT
from admission_database_import import AdmissionDatabaseImporter
from admission_hybrid import (
    AdmissionCloudRepository,
    AdmissionIdentity,
    AdmissionSyncService,
    OfflineAdmissionStore,
    OperationalSession,
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


def _session() -> OperationalSession:
    return OperationalSession(
        operational_session_id="55555555-5555-4555-8555-555555555555",
        active_username="admin",
        active_user_id="1",
        active_user_display_name="Administrador",
        primary_device_id="PC1",
        primary_login_session_id="login",
        turn_id=12,
        operational_source_id="44444444-4444-4444-8444-444444444444",
        status="ACTIVE",
        generation=3,
    )


def _remote_event(global_id: str, *, operation="CREATE", deleted=False):
    payload = {
        "global_attention_id": global_id,
        "attention_id": 77,
        "legacy_source_instance_id": "REMOTE-PC",
        "legacy_attention_id": 77,
        "source_instance_id": "REMOTE-PC",
        "global_patient_id": "33333333-3333-4333-8333-333333333333",
        "patient_id": 8,
        "legacy_patient_id": 8,
        "name": "PACIENTE CORRECTO",
        "ars": "HUMANO",
        "nss": "123456789",
        "service_date": "2026-08-14",
        "service_time": "10:00:00",
        "service_type": "EMERGENCIA",
        "detail_sheet": "GENERAL",
        "turn_id": 12,
        "operational_session_id": _session().operational_session_id,
        "operational_source_id": _session().operational_source_id,
        "generation": 3,
        "origin_device_id": "PC1",
        "admission_username": "admin",
        "created_at_device": "2026-08-14T10:00:00+00:00",
        "created_at_effective_utc": "2026-08-14T10:00:00+00:00",
        "device_local_sequence": 9,
        "version": 2 if deleted else 1,
        "source_status": "ANULADA" if deleted else "ACTIVA",
        "is_deleted": deleted,
        "deleted_at": "2026-08-14T10:01:00+00:00" if deleted else "",
        "deleted_by_user_id": "1" if deleted else "",
        "delete_event_uuid": str(uuid.uuid4()) if deleted else "",
        "delete_reason": "Registro duplicado" if deleted else "",
    }
    return {
        "sequence": 2 if deleted else 1,
        "event_uuid": payload["delete_event_uuid"] or str(uuid.uuid4()),
        "entity_type": "attention",
        "entity_uuid": global_id,
        "operation": operation,
        "payload_json": payload,
        "operational_session_id": _session().operational_session_id,
        "operational_source_id": _session().operational_source_id,
        "turn_id": 12,
        "generation": 3,
        "origin_device_id": "PC1",
        "resulting_version": payload["version"],
    }


class _ReadThroughCloud:
    def __init__(self, event):
        self.event = event

    def get_attention_by_global_id(self, global_id, *, include_deleted=True):
        del global_id, include_deleted
        return {"event": self.event, "is_deleted": self.event["payload_json"]["is_deleted"]}


def test_readthrough_hydrates_secondary_and_central_cancel_hydrates_tombstone(tmp_path):
    path = tmp_path / "secondary.db"
    _database(path)
    store = OfflineAdmissionStore(path)
    store.configure_runtime_context(_session(), device_id="PC2")
    global_id = str(uuid.uuid4())
    created = _remote_event(global_id)
    service = AdmissionSyncService(store, _ReadThroughCloud(created))

    row = service.get_attention_by_global_id(global_id, online=True)
    assert row["nombre"] == "PACIENTE CORRECTO"
    assert row["global_attention_id"] == global_id

    deleted = _remote_event(global_id, operation="DELETE", deleted=True)
    service.cloud.event = deleted
    result = service.get_attention_by_global_id(
        global_id, online=True, force_central=True, include_deleted=True
    )
    assert result["is_deleted"] == 1
    assert result["sync_state"] == "TOMBSTONED"


def test_remote_identity_never_aliases_by_patient_and_day(tmp_path):
    path = tmp_path / "identity.db"
    _database(path)
    store = OfflineAdmissionStore(path)
    store.configure_runtime_context(_session(), device_id="PC2")
    with closing(sqlite3.connect(path)) as connection:
        patient = connection.execute(
            "INSERT INTO pacientes(nombre,ars,nss) VALUES('LOCAL','HUMANO','1')"
        ).lastrowid
        connection.execute(
            """INSERT INTO atenciones(
                 paciente_id,dia_operativo_id,turno_id,nombre,fecha,hora,
                 tipo_atencion,estado
               ) VALUES(?,1,1,'LOCAL','2026-08-14','09:00','EMERGENCIA','ACTIVA')""",
            (patient,),
        )
        connection.commit()
    with closing(sqlite3.connect(path)) as connection:
        original_global_id = connection.execute(
            "SELECT global_attention_id FROM atenciones WHERE nombre='LOCAL'"
        ).fetchone()[0]
    original = store.get_attention_by_global_id(original_global_id)
    remote_id = str(uuid.uuid4())
    store.hydrate_remote_events([_remote_event(remote_id)])
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute(
            "SELECT id,global_attention_id,nombre FROM atenciones ORDER BY id"
        ).fetchall()
    assert len(rows) == 2
    assert original["global_attention_id"] != remote_id
    assert rows[1][1] == remote_id


class _Cursor:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _ImportConnection:
    def __init__(self, cloud_rows):
        self.cloud_rows = cloud_rows
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        if "SELECT * FROM admission_attention_projection" in sql:
            return _Cursor(self.cloud_rows)
        return _Cursor()


class _StatefulImportConnection:
    def __init__(self):
        self.batches = {}
        self.staging = {}
        self.projections = {}
        self.dataset_epoch = 0
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=None):
        params = params or ()
        self.statements.append((sql, params))
        compact = " ".join(sql.split())
        if "SELECT * FROM admission_attention_projection" in compact:
            return _Cursor(list(self.projections.values()))
        if compact.startswith("INSERT INTO admission_import_batches"):
            self.batches[str(params[0])] = "ANALYZED"
        elif compact.startswith("INSERT INTO admission_import_staging"):
            batch_id = str(params[0])
            self.staging.setdefault(batch_id, []).append({
                "import_batch_id": batch_id,
                "row_number": int(params[1]),
                "global_attention_id": str(params[2]),
                "legacy_source_instance_id": str(params[3]),
                "legacy_attention_id": int(params[4]),
                "local_revision": int(params[5]),
                "cloud_revision": int(params[6]),
                "classification": str(params[7]),
                "payload_json": json.loads(params[8]),
            })
        elif "SELECT status FROM admission_import_batches" in compact:
            status = self.batches.get(str(params[0]))
            return _Cursor([(status,)] if status else [])
        elif compact.startswith("UPDATE admission_import_batches SET status='APPLYING'"):
            self.batches[str(params[0])] = "APPLYING"
        elif "SELECT * FROM admission_import_staging" in compact:
            return _Cursor([
                row for row in self.staging.get(str(params[0]), [])
                if row["classification"] in {"INSERT", "UPDATE"}
            ])
        elif "SELECT is_deleted,server_revision FROM admission_attention_projection" in compact:
            row = self.projections.get(str(params[0]))
            return _Cursor(
                [(row["is_deleted"], row["server_revision"])] if row else []
            )
        elif "SELECT * FROM admission_operational_sessions" in compact:
            return _Cursor([{
                "operational_session_id": _session().operational_session_id,
                "operational_source_id": _session().operational_source_id,
                "generation": _session().generation,
            }])
        elif "SET status='COMPLETED'" in compact:
            self.batches[str(params[1])] = "COMPLETED"
        elif compact.startswith("UPDATE admission_dataset_state"):
            self.dataset_epoch += 1
        return _Cursor()


def test_admin_import_preview_protects_tombstone_and_cloud_newer(tmp_path):
    path = tmp_path / "seed.sqlite"
    _database(path)
    identities = [str(uuid.uuid4()) for _ in range(3)]
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("ALTER TABLE atenciones ADD COLUMN global_attention_id TEXT")
        connection.execute("ALTER TABLE atenciones ADD COLUMN server_revision INTEGER DEFAULT 0")
        for index, global_id in enumerate(identities, start=1):
            patient = connection.execute(
                "INSERT INTO pacientes(nombre) VALUES(?)", (f"P{index}",)
            ).lastrowid
            connection.execute(
                """INSERT INTO atenciones(
                     paciente_id,dia_operativo_id,turno_id,nombre,fecha,hora,
                     tipo_atencion,estado,global_attention_id,server_revision
                   ) VALUES(?,1,1,?,'2026-08-14','10:00','EMERGENCIA','ACTIVA',?,0)""",
                (patient, f"P{index}", global_id),
            )
        connection.commit()
    cloud_rows = [
        {
            "global_attention_id": identities[1], "source_instance_id": "cloud",
            "attention_id": 2, "is_deleted": True, "server_revision": 4,
        },
        {
            "global_attention_id": identities[2], "source_instance_id": "cloud",
            "attention_id": 3, "is_deleted": False, "server_revision": 5,
            "patient_name": "CENTRAL MAS NUEVO",
        },
    ]
    connection = _ImportConnection(cloud_rows)
    importer = AdmissionDatabaseImporter(lambda: connection)
    result = importer.analyze(
        path, mode="MERGE", current_user={"id": 1, "username": "admin", "role": "Administrador"}
    )
    assert result["INSERT"] == 1
    assert result["SKIP_TOMBSTONED"] == 1
    assert result["SKIPPED_CLOUD_NEWER"] == 1
    assert not any(
        "INSERT INTO admission_attention_projection" in sql
        or "INSERT INTO admission_sync_events" in sql
        for sql, _params in connection.statements
    )
    with pytest.raises(PermissionError):
        importer.analyze(
            path, mode="SEED", current_user={"username": "aux", "role": "Auxiliar"}
        )


def test_admin_import_apply_is_idempotent_and_increments_dataset_epoch(
    monkeypatch, tmp_path
):
    path = tmp_path / "seed.db"
    _database(path)
    with closing(sqlite3.connect(path)) as connection:
        patient = connection.execute(
            "INSERT INTO pacientes(nombre,ars,nss) VALUES('SEED','HUMANO','9')"
        ).lastrowid
        connection.execute(
            """INSERT INTO atenciones(
                 paciente_id,dia_operativo_id,turno_id,nombre,ars,nss,fecha,hora,
                 tipo_atencion,estado
               ) VALUES(?,1,1,'SEED','HUMANO','9','2026-08-14','11:00',
                        'EMERGENCIA','ACTIVA')""",
            (patient,),
        )
        connection.commit()
    central = _StatefulImportConnection()
    audit_events = []
    materialized_events = []
    importer = AdmissionDatabaseImporter(
        lambda: central,
        audit=lambda event, details: audit_events.append((event, dict(details))),
    )
    admin = {"id": 1, "username": "admin", "role": "Administrador"}

    def materialize(_connection, event, revision):
        materialized_events.append(event)
        payload = dict(event.payload)
        central.projections[event.entity_uuid] = {
            "global_attention_id": event.entity_uuid,
            "source_instance_id": payload["source_instance_id"],
            "attention_id": payload["attention_id"],
            "patient_name": payload["name"],
            "service_date": payload["service_date"],
            "service_time": payload["service_time"],
            "canonical_ars": payload["ars"],
            "nss_snapshot": payload["nss"],
            "cedula_snapshot": payload["cedula"],
            "service_type": payload["service_type"],
            "source_status": payload["source_status"],
            "is_deleted": False,
            "server_revision": revision,
        }

    monkeypatch.setattr(
        AdmissionCloudRepository,
        "_materialize_attention",
        staticmethod(materialize),
    )
    first_preview = importer.analyze(path, mode="SEED", current_user=admin)
    applied = importer.apply(
        first_preview["import_batch_id"], current_user=admin, device_id="PC-ADMIN"
    )
    second_preview = importer.analyze(path, mode="MERGE", current_user=admin)

    assert applied["APPLIED_INSERT"] == 1
    assert materialized_events[0].payload["admission_username"] == "IMPORTACION HISTORICA"
    assert materialized_events[0].payload["reconciliation_status"] == "ADMIN_HISTORICAL_IMPORT"
    assert not any(
        "admission_operational_sessions" in sql
        for sql, _params in central.statements
    )
    assert central.dataset_epoch == 1
    assert second_preview["EXISTING"] == 1
    assert second_preview.get("INSERT", 0) == 0
    assert [event for event, _details in audit_events] == [
        "ADMIN_DATABASE_IMPORT_ANALYZED",
        "ADMIN_DATABASE_IMPORT_APPLIED",
        "ADMIN_DATABASE_IMPORT_ANALYZED",
    ]
    with pytest.raises(PermissionError):
        importer.apply(
            first_preview["import_batch_id"],
            current_user={"username": "aux", "role": "Auxiliar"},
            device_id="PC-AUX",
        )


class _DocumentConnection:
    def __init__(self, row):
        self.row = row
        self.query = ""
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params
        return _Cursor([self.row])


def test_document_resolution_uses_uuid_and_rejects_payload_identity_mismatch(monkeypatch, tmp_path):
    selected = str(uuid.uuid4())
    other = str(uuid.uuid4())
    row = {
        "global_attention_id": selected,
        "source_instance_id": "PC1",
        "attention_id": 15,
        "patient_name": "HOY",
        "service_date": "2026-08-14",
        "service_time": "12:00",
        "specialty": "GENERAL",
        "document_payload": {"global_attention_id": other, "service_date": "2026-02-23"},
    }
    connection = _DocumentConnection(row)
    resolver = CALCULOS_QT.AdmissionDocumentResolver(lambda: connection)
    with pytest.raises(CALCULOS_QT.DocumentIdentityMismatch):
        resolver.build_document(
            global_attention_id=selected,
            attention_id=999,
            source_instance_id="PC2",
        )
    assert "p.global_attention_id=%s::UUID" in connection.query
    assert " OR " not in connection.query
    assert connection.params == (selected,)

    captured = {}
    valid_row = dict(row)
    valid_row["document_payload"] = {
        "global_attention_id": selected,
        "service_date": "2026-08-14",
        "name": "HOY",
        "detail_sheet": "GENERAL",
    }
    valid_connection = _DocumentConnection(valid_row)
    output = tmp_path / "today.pdf"

    class DocumentModule:
        def __init__(self):
            self.RUTA_HOJAS = {"GENERAL": "template"}

        @staticmethod
        def crear_pdf_temporal(sheet, data, mostrar_error=False):
            captured.update(sheet=sheet, data=data, mostrar_error=mostrar_error)
            output.write_bytes(b"pdf")
            return str(output)

    monkeypatch.setattr(CALCULOS_QT, "load_v15_application_module", DocumentModule)
    valid_resolver = CALCULOS_QT.AdmissionDocumentResolver(lambda: valid_connection)
    assert valid_resolver.build_document(global_attention_id=selected) == str(output.resolve())
    assert captured["data"]["Fecha"] == "2026-08-14"
    assert captured["data"]["Nombre"] == "HOY"


class _Signal:
    def __init__(self):
        self.emitted = False

    def emit(self):
        self.emitted = True


def test_logout_returns_immediately_while_remote_close_runs(monkeypatch):
    finished = threading.Event()

    class Dummy:
        def __init__(self):
            self._logout_finalizing = False
            self.current_user = {"username": "admin", "role": "Administrador"}
            self.session_id = "session-1"
            self.logout_requested = _Signal()

        def _cancel_session_work_without_waiting(self):
            return None

        def _complete_remote_logout(self, *_args):
            time.sleep(0.25)
            finished.set()

        def _clear_sensitive_session_references(self):
            self.current_user = {}
            self.session_id = ""

        def setEnabled(self, _value):
            return None

        def hide(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(CALCULOS_QT, "release_local_session_mutex", lambda _session: None)
    monkeypatch.setattr(CALCULOS_QT, "write_runtime_log", lambda _message: None)
    dummy = Dummy()
    started = time.perf_counter()
    CALCULOS_QT.MainWindow.force_logout(dummy, "LOGOUT", "test")
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    assert elapsed_ms < 100.0
    assert dummy.logout_requested.emitted is True
    assert finished.wait(1.0)


def test_administrative_primary_transfer_shows_one_notice_and_logs_out(monkeypatch):
    notices = []
    logouts = []

    class Dummy:
        _logout_finalizing = False

        def force_logout(self, reason, source_module="Sistema"):
            logouts.append((reason, source_module))
            self._logout_finalizing = True

    monkeypatch.setattr(
        CALCULOS_QT.QMessageBox,
        "information",
        lambda _parent, title, message: notices.append((title, message)),
    )
    dummy = Dummy()
    status = {"logout_reason": "PRIMARY_TRANSFERRED_ADMINISTRATIVELY"}
    CALCULOS_QT.MainWindow._handle_inactive_login_session(
        dummy, "fallback", "SessionHealthWorker", status=status
    )
    CALCULOS_QT.MainWindow._handle_inactive_login_session(
        dummy, "fallback", "SessionHealthWorker", status=status
    )
    assert len(notices) == 1
    assert "transferido administrativamente" in notices[0][1]
    assert logouts == [
        ("PRIMARY_TRANSFERRED_ADMINISTRATIVELY", "SessionHealthWorker")
    ]


def test_admission_identity_requires_uuid_or_composite_legacy_identity():
    identity = AdmissionIdentity.from_mapping({"global_attention_id": str(uuid.uuid4())})
    assert identity.global_attention_id
    with pytest.raises(ValueError):
        AdmissionIdentity.from_mapping({"attention_id": 7})
