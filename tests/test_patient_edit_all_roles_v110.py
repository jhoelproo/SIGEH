from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADMISSION_SOURCE = ROOT / "admission_source"
for source_path in (ROOT, ADMISSION_SOURCE):
    if str(source_path) not in sys.path:
        sys.path.insert(0, str(source_path))

from admission_source.emergency_core.session_context import (  # noqa: E402
    CAP_EDIT_PATIENT,
    CAP_EDIT_RECORDS,
    ROLE_ADMIN,
    ROLE_AUXILIARY,
    ROLE_BILLING_AUDIT,
    ROLE_MEDICAL_AUDIT,
    AdmissionSessionContext,
)
from admission_v15_adapter import (  # noqa: E402
    _HybridAdmissionRuntime,
    _HybridDatabaseProxy,
    v15_capabilities_for_role,
)
from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import (  # noqa: E402
    DatabaseManager,
)
from patient_directory import (  # noqa: E402
    CentralPatientDirectoryRepository,
    PatientDirectoryConflict,
    PatientDirectoryService,
)


ALL_SIGEH_ROLES = (
    ROLE_AUXILIARY,
    ROLE_ADMIN,
    ROLE_BILLING_AUDIT,
    ROLE_MEDICAL_AUDIT,
)


@pytest.mark.parametrize("role", ALL_SIGEH_ROLES)
def test_every_authenticated_sigeh_role_can_edit_patient_master(role: str):
    session = AdmissionSessionContext(
        username="usuario",
        full_name="Usuario habilitado",
        role=role,
        session_id="session-valid",
        launched_from_billing=True,
    )

    assert session.allows(CAP_EDIT_PATIENT)
    assert CAP_EDIT_PATIENT in v15_capabilities_for_role({"role": role})


@pytest.mark.parametrize(
    ("role", "can_edit_attention"),
    (
        (ROLE_AUXILIARY, True),
        (ROLE_ADMIN, True),
        (ROLE_BILLING_AUDIT, False),
        (ROLE_MEDICAL_AUDIT, False),
    ),
)
def test_patient_permission_does_not_expand_attention_permission(
    role: str, can_edit_attention: bool
):
    session = AdmissionSessionContext(
        username="usuario",
        full_name="Usuario habilitado",
        role=role,
        session_id="session-valid",
        launched_from_billing=True,
    )

    assert session.allows(CAP_EDIT_PATIENT)
    assert session.allows(CAP_EDIT_RECORDS) is can_edit_attention


def _create_local_patient_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY,nombre TEXT,cedula TEXT,telefono TEXT,
              direccion TEXT,nacionalidad TEXT,ars TEXT,nss TEXT,nss_clean TEXT,
              cedula_clean TEXT,telefono_clean TEXT,provisional INTEGER,
              updated_at TEXT,global_patient_id TEXT,server_revision INTEGER
            );
            CREATE TABLE paciente_identificadores(
              id INTEGER PRIMARY KEY AUTOINCREMENT,paciente_id INTEGER,tipo TEXT,
              valor_normalizado TEXT,activo INTEGER,conflicto INTEGER,
              UNIQUE(paciente_id,tipo,valor_normalizado)
            );
            CREATE TABLE atenciones(
              id INTEGER PRIMARY KEY,paciente_id INTEGER,nombre TEXT,cedula TEXT,
              telefono TEXT,direccion TEXT,nacionalidad TEXT,ars TEXT,nss TEXT,
              global_attention_id TEXT,turn_id INTEGER,server_revision INTEGER
            );
            """
        )
        connection.execute(
            """INSERT INTO pacientes VALUES(
                   1,'PACIENTE MAESTRO','00100000001','8095550001','DIRECCION A',
                   'DOMINICANA','HUMANO','123456','123456','00100000001',
                   '8095550001',0,NULL,?,7)""",
            (str(uuid.uuid4()),),
        )
        connection.execute(
            """INSERT INTO paciente_identificadores(
                   paciente_id,tipo,valor_normalizado,activo,conflicto
               ) VALUES(1,'CEDULA','00100000001',1,0),(1,'NSS','123456',1,0)"""
        )
        connection.execute(
            """INSERT INTO atenciones VALUES(
                   10,1,'SNAPSHOT HISTORICO','00100000001','8095550001',
                   'DIRECCION HISTORICA','DOMINICANA','HUMANO','123456',?,3946,11)""",
            (str(uuid.uuid4()),),
        )


def _database_manager_for(path: Path):
    database = object.__new__(DatabaseManager)
    database._connect = lambda: sqlite3.connect(path)
    database.session_context = SimpleNamespace(
        audit_actor="usuario_b", role=ROLE_BILLING_AUDIT
    )
    audit_calls = []
    database._registrar_auditoria_conn = lambda *args, **kwargs: audit_calls.append(
        (args, kwargs)
    )
    return database, audit_calls


def test_patient_edit_preserves_patient_and_attention_identity(tmp_path: Path):
    database_path = tmp_path / "patients.db"
    _create_local_patient_database(database_path)
    database, audit_calls = _database_manager_for(database_path)

    before = database.buscar_paciente_para_edicion("A:10")
    assert before["nombre"] == "PACIENTE MAESTRO"
    assert before["server_revision"] == 7
    attention_before = (
        sqlite3.connect(database_path)
        .execute("SELECT * FROM atenciones WHERE id=10")
        .fetchone()
    )
    result = database.actualizar_datos_paciente_por_identidad(
        "A:10",
        {
            "Nombre": "PACIENTE CORREGIDO",
            "Cédula": "00100000001",
            "Teléfono": "8095559999",
            "NSS": "123456",
            "Dirección": "DIRECCION NUEVA",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "HUMANO",
        },
    )

    with sqlite3.connect(database_path) as connection:
        patient_after = connection.execute(
            "SELECT id,nombre,telefono,direccion,global_patient_id FROM pacientes WHERE id=1"
        ).fetchone()
        attention_after = connection.execute(
            "SELECT * FROM atenciones WHERE id=10"
        ).fetchone()
    assert result == (0, 1)
    assert patient_after[:4] == (
        1,
        "PACIENTE CORREGIDO",
        "8095559999",
        "DIRECCION NUEVA",
    )
    assert patient_after[4] == before["global_patient_id"]
    assert attention_after == attention_before
    assert audit_calls and audit_calls[0][0][2] == "PATIENT_UPDATE"


@pytest.mark.parametrize(
    ("changed_field", "duplicate_value", "identifier_type"),
    (("NSS", "999999", "NSS"), ("Cédula", "00200000002", "CEDULA")),
)
def test_local_patient_edit_rejects_document_owned_by_another_patient(
    tmp_path: Path,
    changed_field: str,
    duplicate_value: str,
    identifier_type: str,
):
    database_path = tmp_path / "patients.db"
    _create_local_patient_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO pacientes(
                   id,nombre,nss,nss_clean,provisional,global_patient_id,server_revision
               ) VALUES(2,'OTRO','999999','999999',0,?,1)""",
            (str(uuid.uuid4()),),
        )
        connection.execute(
            """INSERT INTO paciente_identificadores(
                   paciente_id,tipo,valor_normalizado,activo,conflicto
               ) VALUES(2,'NSS','999999',1,0),(2,'CEDULA','00200000002',1,0)"""
        )
    database, _audit_calls = _database_manager_for(database_path)
    changes = {
        "Nombre": "PACIENTE MAESTRO",
        "Cédula": "00100000001",
        "Teléfono": "8095550001",
        "NSS": "123456",
        "Dirección": "DIRECCION A",
        "Nacionalidad": "DOMINICANA",
        "Aseguradora (ARS)": "HUMANO",
    }
    changes[changed_field] = duplicate_value

    with pytest.raises(ValueError, match=identifier_type + ".*otra ficha"):
        database.actualizar_datos_paciente_por_identidad(
            "P:1",
            changes,
        )


def test_local_patient_edit_supports_document_lookup_and_empty_documents(
    tmp_path: Path,
):
    database_path = tmp_path / "patients.db"
    _create_local_patient_database(database_path)
    database, _audit_calls = _database_manager_for(database_path)

    assert database.actualizar_datos_paciente_por_identidad("", {}) == (0, 0)
    assert database.actualizar_datos_paciente_por_identidad("NO-EXISTE", {}) == (
        0,
        0,
    )
    result = database.actualizar_datos_paciente_por_identidad(
        "123456",
        {
            "Nombre": "PACIENTE SIN DOCUMENTOS",
            "Cédula": "",
            "Teléfono": "",
            "NSS": "",
            "Dirección": "DIRECCION",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "SIN SEGURO",
        },
    )

    assert result == (0, 1)
    with sqlite3.connect(database_path) as connection:
        identifiers = connection.execute(
            "SELECT COUNT(*) FROM paciente_identificadores WHERE paciente_id=1"
        ).fetchone()[0]
    assert identifiers == 0


def test_local_patient_edit_handles_master_without_attention(tmp_path: Path):
    database_path = tmp_path / "patients.db"
    _create_local_patient_database(database_path)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """INSERT INTO pacientes(
                   id,nombre,provisional,global_patient_id,server_revision
               ) VALUES(2,'SIN ATENCION',1,?,1)""",
            (str(uuid.uuid4()),),
        )
    database, audit_calls = _database_manager_for(database_path)

    missing = database.actualizar_datos_paciente_por_identidad(
        "P:99", {"Nombre": "NO EXISTE"}
    )
    updated = database.actualizar_datos_paciente_por_identidad(
        "P:2",
        {
            "Nombre": "SIN ATENCION CORREGIDO",
            "Teléfono": "8095550002",
            "Dirección": "DIRECCION 2",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "SIN SEGURO",
        },
    )

    assert missing == (0, 0)
    assert updated == (0, 1)
    assert audit_calls == []


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _PatientConnection:
    def __init__(self, row: dict, *, duplicate=False):
        self.row = dict(row)
        self.duplicate = duplicate
        self.events = []
        self.update_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=()):
        compact = " ".join(str(query).split())
        if compact.startswith("SELECT pg_advisory_xact_lock"):
            return _Result((None,))
        if compact.startswith("SELECT * FROM admission_patient_directory"):
            return _Result(dict(self.row) if self.row is not None else None)
        if compact.startswith("SELECT global_patient_id::TEXT"):
            return _Result((str(uuid.uuid4()),) if self.duplicate else None)
        if compact.startswith("UPDATE admission_patient_directory SET"):
            (
                name,
                cedula,
                cedula_normalized,
                nss,
                nss_normalized,
                phone,
                address,
                nationality,
                ars,
                revision,
                _patient_id,
            ) = params
            self.row.update(
                patient_name=name,
                cedula=cedula,
                cedula_normalized=cedula_normalized,
                nss=nss,
                nss_normalized=nss_normalized,
                phone=phone,
                address=address,
                nationality=nationality,
                canonical_ars=ars,
                server_revision=revision,
            )
            self.update_count += 1
            return _Result(dict(self.row))
        if compact.startswith("INSERT INTO admission_patient_directory_events"):
            self.events.append(json.loads(params[4]))
            return _Result(None)
        raise AssertionError(compact)


def _central_patient_row() -> dict:
    return {
        "global_patient_id": str(uuid.uuid4()),
        "patient_name": "PACIENTE",
        "cedula": "00100000001",
        "cedula_normalized": "00100000001",
        "nss": "123456",
        "nss_normalized": "123456",
        "phone": "8095550001",
        "address": "DIRECCION A",
        "nationality": "DOMINICANA",
        "canonical_ars": "HUMANO",
        "legacy_source_instance_id": "PC-A",
        "legacy_patient_id": 1,
        "server_revision": 3,
        "is_deleted": False,
        "deleted_at": None,
    }


def test_central_patient_update_is_audited_idempotent_and_revision_guarded():
    connection = _PatientConnection(_central_patient_row())
    repository = CentralPatientDirectoryRepository(lambda: connection)
    changes = {"phone": "8095559999", "address": "DIRECCION NUEVA"}

    updated = repository.update_patient(
        connection.row["global_patient_id"],
        changes,
        expected_revision=3,
        actor_user="audit_user",
        actor_role=ROLE_BILLING_AUDIT,
    )
    repeated = repository.update_patient(
        connection.row["global_patient_id"],
        changes,
        expected_revision=3,
        actor_user="audit_user",
        actor_role=ROLE_BILLING_AUDIT,
    )

    assert updated["server_revision"] == repeated["server_revision"] == 4
    assert connection.update_count == 1
    assert connection.events[0]["audit"]["operation"] == "PATIENT_UPDATE"
    assert connection.events[0]["audit"]["fields_changed"] == ["phone", "address"]
    with pytest.raises(PatientDirectoryConflict, match="otra estación"):
        repository.update_patient(
            connection.row["global_patient_id"],
            {"phone": "8095551111"},
            expected_revision=3,
            actor_user="other",
            actor_role=ROLE_AUXILIARY,
        )


def test_central_patient_update_rejects_duplicate_document():
    connection = _PatientConnection(_central_patient_row(), duplicate=True)
    repository = CentralPatientDirectoryRepository(lambda: connection)

    with pytest.raises(PatientDirectoryConflict, match="pertenece a otro paciente"):
        repository.update_patient(
            connection.row["global_patient_id"],
            {"nss": "999999"},
            expected_revision=3,
            actor_user="admin",
            actor_role=ROLE_ADMIN,
        )


@pytest.mark.parametrize(
    ("patient_id", "row", "changes", "message"),
    (
        ("not-a-uuid", _central_patient_row(), {"phone": "1"}, "identidad global"),
        (str(uuid.uuid4()), None, {"phone": "1"}, "no está disponible"),
        (
            str(uuid.uuid4()),
            {**_central_patient_row(), "is_deleted": True},
            {"phone": "1"},
            "no está disponible",
        ),
        (
            str(uuid.uuid4()),
            _central_patient_row(),
            {"patient_name": ""},
            "nombre del paciente",
        ),
    ),
)
def test_central_patient_update_rejects_invalid_or_unavailable_patient(
    patient_id, row, changes, message
):
    connection = _PatientConnection(row or _central_patient_row())
    if row is None:
        connection.row = None
    repository = CentralPatientDirectoryRepository(lambda: connection)

    with pytest.raises(ValueError, match=message):
        repository.update_patient(
            patient_id,
            changes,
            expected_revision=3,
            actor_user="admin",
            actor_role=ROLE_ADMIN,
        )


def test_hybrid_patient_edit_does_not_require_operational_write_or_primary():
    patient_id = str(uuid.uuid4())

    class _LocalDatabase:
        def actualizar_datos_paciente_por_identidad(self, *_args, **_kwargs):
            raise AssertionError("La ruta local legacy no debe ejecutarse online")

        def buscar_paciente_para_edicion(self, _identity):
            return {"global_patient_id": patient_id, "server_revision": 8}

    calls = []
    runtime = SimpleNamespace(
        update_patient_directory=lambda *args, **kwargs: calls.append((args, kwargs)),
        verify_patient_with_cloud=lambda **_query: None,
    )
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)

    result = proxy.actualizar_datos_paciente_por_identidad(
        "P:1", {"Nombre": "PACIENTE", "Teléfono": "8095559999"}
    )

    assert result == (0, 1)
    assert calls[0][0][0] == patient_id
    assert calls[0][1]["expected_revision"] == 8


def test_hybrid_patient_edit_recovers_missing_global_identity_from_central():
    patient_id = str(uuid.uuid4())

    class _LegacyPatientDatabase:
        def actualizar_datos_paciente_por_identidad(self, *_args, **_kwargs):
            raise AssertionError("La ruta local legacy no debe ejecutarse online")

        def buscar_paciente_para_edicion(self, _identity):
            return {"cedula": "00100000001", "server_revision": 0}

    calls = []
    runtime = SimpleNamespace(
        verify_patient_with_cloud=lambda **query: {
            "global_patient_id": patient_id,
            "server_revision": 6,
            "query": query,
        },
        update_patient_directory=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    proxy = _HybridDatabaseProxy(_LegacyPatientDatabase(), runtime)

    proxy.actualizar_datos_paciente_por_identidad(
        "A:10", {"Nombre": "PACIENTE", "Cédula": "00100000001"}
    )

    assert calls[0][0][0] == patient_id
    assert calls[0][1]["expected_revision"] == 6


def test_hybrid_patient_edit_can_recover_identity_by_nss():
    patient_id = str(uuid.uuid4())

    class _NssOnlyDatabase:
        def actualizar_datos_paciente_por_identidad(self, *_args, **_kwargs):
            return 0, 0

        def buscar_paciente_para_edicion(self, _identity):
            return {"nss": "123456", "server_revision": 0}

    verified_queries = []
    calls = []
    runtime = SimpleNamespace(
        verify_patient_with_cloud=lambda **query: (
            verified_queries.append(query)
            or {"global_patient_id": patient_id, "server_revision": 5}
        ),
        update_patient_directory=lambda *args, **kwargs: calls.append((args, kwargs)),
    )
    proxy = _HybridDatabaseProxy(_NssOnlyDatabase(), runtime)

    proxy.actualizar_datos_paciente_por_identidad(
        "A:10", {"Nombre": "PACIENTE", "NSS": "123456"}
    )

    assert verified_queries == [{"nss": "123456"}]
    assert calls[0][0][0] == patient_id


def test_hybrid_proxy_returns_non_callable_legacy_attribute_unchanged():
    local_database = SimpleNamespace(actualizar_datos_paciente_por_identidad="marker")
    proxy = _HybridDatabaseProxy(local_database, SimpleNamespace())

    assert proxy.actualizar_datos_paciente_por_identidad == "marker"


@pytest.mark.parametrize(
    "patient",
    (None, {"cedula": "", "nss": "", "server_revision": 0}),
)
def test_hybrid_patient_edit_rejects_missing_patient_identity(patient):
    class _MissingIdentityDatabase:
        def actualizar_datos_paciente_por_identidad(self, *_args, **_kwargs):
            return 0, 0

        def buscar_paciente_para_edicion(self, _identity):
            return patient

    runtime = SimpleNamespace(
        verify_patient_with_cloud=lambda **_query: None,
        update_patient_directory=lambda *_args, **_kwargs: None,
    )
    proxy = _HybridDatabaseProxy(_MissingIdentityDatabase(), runtime)

    with pytest.raises(ValueError, match="paciente|identidad global"):
        proxy.actualizar_datos_paciente_por_identidad("P:1", {"Nombre": "PACIENTE"})


def test_patient_directory_service_commits_central_then_hydrates_local():
    updated = {"global_patient_id": str(uuid.uuid4()), "server_revision": 2}
    central_calls = []
    local_calls = []
    service = PatientDirectoryService.__new__(PatientDirectoryService)
    service.is_online = lambda: True
    service.central = SimpleNamespace(
        update_patient=lambda *args, **kwargs: (
            central_calls.append((args, kwargs)) or updated
        )
    )
    service.local = SimpleNamespace(hydrate=lambda row: local_calls.append(row))

    result = service.update_patient(
        updated["global_patient_id"],
        {"phone": "8095559999"},
        expected_revision=1,
        actor_user="usuario",
        actor_role=ROLE_AUXILIARY,
    )

    assert result == updated
    assert central_calls and local_calls == [updated]


def test_runtime_patient_update_requires_session_and_directory():
    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime.host = SimpleNamespace(user={}, session_id="")
    runtime.patient_directory = None
    with pytest.raises(PermissionError, match="sesión autenticada"):
        runtime.update_patient_directory(str(uuid.uuid4()), {})

    runtime.host = SimpleNamespace(
        user={"username": "audit", "role": ROLE_BILLING_AUDIT},
        session_id="session-valid",
    )
    with pytest.raises(RuntimeError, match="directorio de pacientes"):
        runtime.update_patient_directory(str(uuid.uuid4()), {})


def test_runtime_patient_update_uses_authenticated_actor_without_turn_guard():
    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime.host = SimpleNamespace(
        user={"username": "audit", "role": ROLE_BILLING_AUDIT},
        session_id="session-valid",
    )
    calls = []
    runtime.patient_directory = SimpleNamespace(
        update_patient=lambda *args, **kwargs: (
            calls.append((args, kwargs)) or {"server_revision": 2}
        )
    )

    result = runtime.update_patient_directory(
        str(uuid.uuid4()), {"phone": "8095559999"}, expected_revision=1
    )

    assert result == {"server_revision": 2}
    assert calls[0][1]["actor_user"] == "audit"
    assert calls[0][1]["actor_role"] == ROLE_BILLING_AUDIT


def test_offline_patient_service_never_reports_false_success():
    service = PatientDirectoryService.__new__(PatientDirectoryService)
    service.is_online = lambda: False

    with pytest.raises(RuntimeError, match="requiere conexión central"):
        service.update_patient(
            str(uuid.uuid4()),
            {"phone": "8095559999"},
            expected_revision=1,
            actor_user="usuario",
            actor_role=ROLE_AUXILIARY,
        )
