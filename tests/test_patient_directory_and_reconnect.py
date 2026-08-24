from __future__ import annotations

import sqlite3

from admission_hybrid import ConnectionSupervisor, same_user
from patient_directory import PatientDirectoryService, normalize_patient_document


def _create_v15_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              nombre TEXT NOT NULL,
              cedula TEXT, cedula_clean TEXT,
              nss TEXT, nss_clean TEXT,
              telefono TEXT, direccion TEXT, nacionalidad TEXT, ars TEXT,
              estado TEXT DEFAULT 'ACTIVO', provisional INTEGER DEFAULT 0,
              requiere_revision INTEGER DEFAULT 0,
              created_at TEXT, updated_at TEXT
            );
            CREATE TABLE paciente_identificadores(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paciente_id INTEGER NOT NULL,
              tipo TEXT NOT NULL,
              valor_normalizado TEXT NOT NULL,
              activo INTEGER NOT NULL DEFAULT 1,
              conflicto INTEGER NOT NULL DEFAULT 0,
              UNIQUE(paciente_id,tipo,valor_normalizado)
            );
            """
        )


class _CentralDirectory:
    def __init__(self):
        self.cedula_calls = 0

    def find_by_cedula(self, value):
        self.cedula_calls += 1
        assert normalize_patient_document(value) == "00112345678"
        return {
            "global_patient_id": "e16d8440-9e39-4ab4-ab06-b1bfbed860d4",
            "nombre": "PACIENTE CENTRAL",
            "cedula": "001-1234567-8",
            "nss": "NSS-100",
            "server_revision": 3,
        }


def test_document_normalization_ignores_visual_format():
    assert normalize_patient_document("001-1234567-8") == "00112345678"
    assert normalize_patient_document(" 001 1234567 8 ") == "00112345678"


def test_cloud_miss_hydrates_local_and_next_lookup_is_local(tmp_path):
    database = tmp_path / "pc2.db"
    _create_v15_database(database)
    service = PatientDirectoryService(database, lambda: None)
    central = _CentralDirectory()
    service.central = central

    first = service.find_by_cedula("001-1234567-8")
    second = service.find_by_cedula("00112345678")

    assert first["nombre"] == "PACIENTE CENTRAL"
    assert second["global_patient_id"] == first["global_patient_id"]
    assert central.cedula_calls == 1


def test_connection_supervisor_resets_pool_before_probe():
    calls = []
    supervisor = ConnectionSupervisor(
        lambda: calls.append("probe") or {"generation": 8},
        reset_pool=lambda: calls.append("reset"),
        log=calls.append,
    )
    supervisor.mark_offline(ConnectionError("network"))

    snapshot = supervisor.recover()

    assert snapshot == {"generation": 8}
    assert calls.index("reset") < calls.index("probe")
    assert supervisor.state == "ONLINE"


def test_same_admin_uses_canonical_identity_not_display_name():
    central = {"active_user_id": "1", "active_username": "admin"}
    local = {
        "user_id": "1",
        "username": "admin",
        "full_name": "Administrador del sistema",
    }
    assert same_user(central, local)
