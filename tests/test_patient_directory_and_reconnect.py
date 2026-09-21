from __future__ import annotations

import sqlite3
from unittest.mock import Mock

import pytest

from admission_hybrid import ConnectionSupervisor, same_user
from patient_directory import (
    LocalPatientDirectory,
    PatientDirectoryService,
    normalize_patient_document,
)


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


def test_patient_directory_cursor_is_monotonic_for_hydrate_and_direct_updates(tmp_path):
    database = tmp_path / "patient-cursor.db"
    _create_v15_database(database)
    local = LocalPatientDirectory(database)

    local.hydrate_many([], final_sequence=25)
    local.hydrate_many([], final_sequence=5)
    local.set_patient_cursor(40)
    local.set_patient_cursor(10)

    assert local.patient_cursor() == 40


def test_patient_batch_failure_rolls_back_rows_and_cursor(tmp_path):
    database = tmp_path / "rollback.db"
    _create_v15_database(database)
    local = LocalPatientDirectory(database)
    local.set_patient_cursor(25000)
    with pytest.raises(ValueError, match="global_patient_id"):
        local.hydrate_many([
            {"global_patient_id": "e16d8440-9e39-4ab4-ab06-b1bfbed860d4", "nombre": "SYNTHETIC"},
            {"nombre": "INVALID"},
        ], final_sequence=25002)
    assert LocalPatientDirectory(database).patient_cursor() == 25000
    assert local.cached_patient_ids() == set()


def test_cache_only_new_replica_does_not_download_directory(tmp_path):
    database = tmp_path / "new.db"
    _create_v15_database(database)
    service = PatientDirectoryService(database, lambda: None, cache_only=True)
    service.central = Mock()
    service.central.event_window.return_value = {"latest_sequence": 25000}
    assert service.pull_incremental() == 0
    assert service.local.patient_cursor() == 25000
    service.central.snapshot_page.assert_not_called()
    service.central.events_after.assert_not_called()
    service.central.snapshots_for_ids.assert_not_called()


def test_cache_only_reconnect_fetches_only_changed_cached_patients(tmp_path):
    database = tmp_path / "incremental.db"
    _create_v15_database(database)
    patient_id = "e16d8440-9e39-4ab4-ab06-b1bfbed860d4"
    unknown = "f16d8440-9e39-4ab4-ab06-b1bfbed860d4"
    service = PatientDirectoryService(database, lambda: None, cache_only=True)
    service.local.hydrate_many([{"global_patient_id": patient_id, "nombre": "OLD", "server_revision": 1}], final_sequence=25000)
    service.central = Mock()
    service.central.event_window.return_value = {"latest_sequence": 25002}
    service.central.event_headers_after.return_value = [
        {"sequence": 25001, "global_patient_id": unknown},
        {"sequence": 25002, "global_patient_id": patient_id},
    ]
    service.central.snapshots_for_ids.return_value = []
    with pytest.raises(RuntimeError, match="identidades"):
        service.pull_incremental()
    assert service.local.patient_cursor() == 25000
    service.central.snapshots_for_ids.return_value = [{"global_patient_id": patient_id, "nombre": "UPDATED", "server_revision": 2}]
    assert service.pull_incremental() == 1
    service.central.event_headers_after.assert_called_with(25000, limit=500)
    service.central.snapshots_for_ids.assert_called_with([patient_id])
    assert service.local.patient_cursor() == 25002
    service.central.event_headers_after.reset_mock()
    assert service.pull_incremental() == 0
    service.central.event_headers_after.assert_not_called()


def test_patient_incremental_idle_uses_head_probe_without_event_payload_query(tmp_path):
    database = tmp_path / "patient-idle.db"
    _create_v15_database(database)
    service = PatientDirectoryService(database, lambda: None)
    service.local.set_patient_cursor(15)

    class _IdleCentral:
        event_calls = 0

        def event_window(self):
            return {"minimum_available_sequence": 0, "latest_sequence": 15}

        def events_after(self, *_args, **_kwargs):
            self.event_calls += 1
            return []

    service.central = _IdleCentral()

    assert service.pull_incremental() == 0
    assert service.central.event_calls == 0
