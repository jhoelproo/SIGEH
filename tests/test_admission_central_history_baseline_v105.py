import inspect
import sqlite3
from contextlib import contextmanager

import pytest

import admission_hybrid
import admission_v15_adapter
import CALCULOS_QT


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("Administrador", True),
        ("Auxiliar", True),
        ("Facturador de Auditoría", False),
        ("Auditoría Médica y Cuentas", False),
    ],
)
def test_attention_void_permission_matrix(role, allowed):
    assert admission_hybrid.user_can_void_attention({"role": role}) is allowed


def test_v15_capabilities_hide_mutations_from_audit_roles():
    admin = admission_v15_adapter.v15_capabilities_for_role("Administrador")
    auxiliary = admission_v15_adapter.v15_capabilities_for_role("Auxiliar")
    billing_audit = admission_v15_adapter.v15_capabilities_for_role(
        "Facturador de Auditoría"
    )
    medical_audit = admission_v15_adapter.v15_capabilities_for_role(
        "Auditoría Médica y Cuentas"
    )

    assert {"records.edit", "records.void"} <= admin
    assert {"records.edit", "records.void"} <= auxiliary
    assert "records.edit" not in billing_audit
    assert "records.void" not in billing_audit
    assert "records.edit" not in medical_audit
    assert "records.void" not in medical_audit


def test_correction_events_include_reason_and_before_after_snapshots():
    source = inspect.getsource(admission_hybrid.OfflineAdmissionStore)

    assert "correction_reason" in source
    assert "previous_values" in source
    assert "new_values" in source
    assert "changed_fields" in source


@pytest.mark.parametrize(
    "role", ["Facturador de Auditoría", "Auditoría Médica y Cuentas"]
)
def test_void_service_boundaries_reject_audit_roles_before_storage(role):
    store = admission_hybrid.OfflineAdmissionStore("unused.sqlite3")
    with pytest.raises(PermissionError):
        store.cancel_attention_local(
            "11111111-1111-4111-8111-111111111111",
            current_user={"username": "auditor", "role": role},
            reason="motivo suficiente",
        )

    repository = admission_hybrid.AdmissionCloudRepository(
        lambda: (_ for _ in ()).throw(AssertionError("no database access"))
    )
    with pytest.raises(PermissionError):
        repository.cancel_attention(
            "11111111-1111-4111-8111-111111111111",
            current_user={"username": "auditor", "role": role},
            reason="motivo suficiente",
            operational_session=admission_hybrid.OperationalSession(
                operational_session_id="55555555-5555-4555-8555-555555555555",
                active_username="aux",
                active_user_id="1",
                active_user_display_name="Auxiliar",
                primary_device_id="PC-1",
                primary_login_session_id="login",
                turn_id=12,
                operational_source_id="44444444-4444-4444-8444-444444444444",
                status="ACTIVE",
                generation=1,
            ),
            device_id="PC-1",
        )


def test_receipt_link_carries_stable_global_attention_identity():
    values = CALCULOS_QT._admission_values(
        {
            "attention_id": 7,
            "patient_id": 9,
            "global_attention_id": "11111111-1111-4111-8111-111111111111",
        }
    )

    assert values[0] == 7
    assert values[15] == "11111111-1111-4111-8111-111111111111"


def test_auxiliary_can_create_an_idempotent_local_attention_tombstone(tmp_path):
    attention_id = "11111111-1111-4111-8111-111111111111"
    database_path = tmp_path / "offline.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY,nombre TEXT,sexo TEXT,edad_num INTEGER,
              unidad TEXT,cedula TEXT,telefono TEXT,direccion TEXT,
              nacionalidad TEXT,ars TEXT,nss TEXT
            );
            CREATE TABLE atenciones(
              id INTEGER PRIMARY KEY,paciente_id INTEGER,turno_id INTEGER,
              nombre TEXT,sexo TEXT,edad_num INTEGER,unidad TEXT,cedula TEXT,
              telefono TEXT,direccion TEXT,nacionalidad TEXT,ars TEXT,hoja TEXT,
              fecha TEXT,hora TEXT,tipo_atencion TEXT,estado TEXT,nss TEXT
            );
            INSERT INTO pacientes(id,nombre) VALUES(1,'Paciente');
            """
        )
    store = admission_hybrid.OfflineAdmissionStore(database_path)
    store.initialize()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO atenciones(id,paciente_id,nombre,global_attention_id,estado,is_deleted) "
            "VALUES(1,1,'Paciente',?,'ACTIVA',0)",
            (attention_id,),
        )

    cancelled = store.cancel_attention_local(
        attention_id,
        current_user={"id": 9, "username": "aux.test", "role": "Auxiliar"},
        reason="Registro duplicado",
    )
    repeated = store.cancel_attention_local(
        attention_id,
        current_user={"id": 9, "username": "aux.test", "role": "Auxiliar"},
        reason="Registro duplicado",
    )

    assert cancelled["estado"] == "ANULADA"
    assert cancelled["deleted_by_user_id"] == "9"
    assert repeated["global_attention_id"] == attention_id


def test_auxiliary_cloud_cancellation_emits_a_delete_event(monkeypatch):
    attention_id = "11111111-1111-4111-8111-111111111111"
    repository = admission_hybrid.AdmissionCloudRepository(lambda: None)
    reads = iter(
        (
            {
                "server_revision": 4,
                "turn_id": 316,
                "event": {"payload_json": {"name": "Paciente"}},
            },
            {"global_attention_id": attention_id, "is_deleted": True},
        )
    )
    pushed = []
    monkeypatch.setattr(
        repository,
        "get_attention_by_global_id",
        lambda *_args, **_kwargs: next(reads),
    )
    monkeypatch.setattr(
        repository, "push_event", lambda event: pushed.append(event) or 5
    )
    session = admission_hybrid.OperationalSession(
        operational_session_id="55555555-5555-4555-8555-555555555555",
        active_username="aux.operativo",
        active_user_id="7",
        active_user_display_name="Auxiliar Operativo",
        primary_device_id="PC-PRIMARY",
        primary_login_session_id="LOGIN-1",
        turn_id=316,
        operational_source_id="44444444-4444-4444-8444-444444444444",
        status="ACTIVE",
        generation=42,
    )

    result = repository.cancel_attention(
        attention_id,
        current_user={"id": 9, "username": "aux.test", "role": "Auxiliar"},
        reason="Registro duplicado",
        operational_session=session,
        device_id="PC-SECONDARY",
    )

    assert result["is_deleted"] is True
    assert pushed[0].operation == "DELETE"
    assert pushed[0].payload["admission_username"] == "aux.operativo"
    assert pushed[0].payload["captured_by_username"] == "aux.test"


@pytest.mark.parametrize(
    ("linked_attention", "expected_deleted"),
    [
        (None, False),
        ({"is_deleted": True, "source_status": "ACTIVA"}, True),
        ({"is_deleted": False, "source_status": "ANULADA"}, True),
    ],
)
def test_receipt_restore_reports_cancelled_attention_without_restoring_it(
    monkeypatch, linked_attention, expected_deleted
):
    receipt = {
        "id": 8,
        "numero": 1008,
        "nombre": "Paciente",
        "fecha": "2026-08-27",
        "estado_facturacion": CALCULOS_QT.BILLING_INVOICED,
        "admission_atencion_id": 15,
        "admission_nss_snapshot": "",
        "admission_cedula_snapshot": "001",
        "admission_source_instance_id": "PRIMARY",
        "admission_global_attention_id": "11111111-1111-4111-8111-111111111111",
    }

    class Cursor:
        def __init__(self, row=None):
            self.row = row

        def fetchone(self):
            return self.row

    class Connection:
        def __init__(self):
            self.statements = []

        def execute(self, query, params=()):
            self.statements.append((" ".join(str(query).split()), tuple(params)))
            if "FROM recibos WHERE id=" in query:
                return Cursor(receipt)
            if "FROM admission_attention_projection" in query:
                return Cursor(linked_attention)
            return Cursor()

    connection = Connection()

    @contextmanager
    def connect():
        yield connection

    monkeypatch.setattr(CALCULOS_QT, "db_connect", connect)
    monkeypatch.setattr(
        CALCULOS_QT,
        "_require_receipt_permission",
        lambda *_args: ({"username": "admin"}, "admin"),
    )
    monkeypatch.setattr(
        CALCULOS_QT, "_guard_active_receipt_duplicate", lambda *_a, **_k: None
    )
    monkeypatch.setattr(CALCULOS_QT, "now_str", lambda: "2026-08-27 12:00:00")

    result = CALCULOS_QT.restore_recibo(8, {"role": "Administrador"})

    assert result["original_attention_deleted"] is expected_deleted
    assert any(
        "UPDATE recibos SET is_deleted=0" in sql for sql, _ in connection.statements
    )
    assert not any(
        "UPDATE admission_attention_projection" in sql
        for sql, _params in connection.statements
    )
