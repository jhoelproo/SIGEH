"""Two SQLite replicas simulate stations; no hospital database is opened."""

import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

from patient_directory import PatientDirectoryService
from tests.test_patient_directory_and_reconnect import _create_v15_database


def test_master_update_converges_on_two_physical_sqlite_files_after_refresh(tmp_path):
    original = {
        "global_patient_id": "11111111-1111-4111-8111-111111111111",
        "nombre": "SYNTHETIC ORIGINAL",
        "cedula": "00112345678",
        "nss": "123456",
        "server_revision": 1,
    }
    updated = original | {"nombre": "SYNTHETIC CORRECTED", "server_revision": 2}
    paths = [tmp_path / "station-a.db", tmp_path / "station-b.db"]
    replicas = []
    for path in paths:
        _create_v15_database(path)
        service = PatientDirectoryService(path, lambda: None)
        service.local.hydrate(original)
        replicas.append(service)
    central = SimpleNamespace(
        update_patient=Mock(return_value=updated),
        find_by_cedula=Mock(return_value=updated),
    )
    for service in replicas:
        service.central = central
    replicas[0].update_patient(
        original["global_patient_id"],
        {"patient_name": updated["nombre"]},
        expected_revision=1,
        actor_user="SYNTHETIC",
        actor_role="auxiliar",
    )
    assert (
        replicas[1].find_by_cedula(original["cedula"])["nombre"] == original["nombre"]
    )
    replicas[1].verify_with_cloud(cedula=original["cedula"])
    for path in paths:
        with sqlite3.connect(path) as connection:
            rows = connection.execute(
                "SELECT nombre,global_patient_id,server_revision FROM pacientes"
            ).fetchall()
        assert rows == [(updated["nombre"], original["global_patient_id"], 2)]
        reopened = PatientDirectoryService(path, lambda: None, is_online=lambda: False)
        assert (
            reopened.find_by_cedula(original["cedula"])["nombre"] == updated["nombre"]
        )
    central.update_patient.assert_called_once()
