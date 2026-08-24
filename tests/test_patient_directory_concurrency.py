from __future__ import annotations

import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

from patient_directory import LocalPatientDirectory


def _create_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              nombre TEXT NOT NULL,
              cedula TEXT, cedula_clean TEXT, nss TEXT, nss_clean TEXT,
              telefono TEXT, direccion TEXT, nacionalidad TEXT, ars TEXT,
              estado TEXT DEFAULT 'ACTIVO', provisional INTEGER DEFAULT 0,
              requiere_revision INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE paciente_identificadores(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              paciente_id INTEGER NOT NULL, tipo TEXT NOT NULL,
              valor_normalizado TEXT NOT NULL, activo INTEGER DEFAULT 1,
              conflicto INTEGER DEFAULT 0,
              UNIQUE(paciente_id,tipo,valor_normalizado)
            );
            """
        )


def test_concurrent_patient_hydration_is_serialized_without_database_locked(tmp_path):
    database = tmp_path / "secondary.db"
    _create_database(database)
    directory = LocalPatientDirectory(database)

    def hydrate(index):
        return directory.hydrate(
            {
                "global_patient_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"patient-{index}")),
                "nombre": f"PACIENTE {index}",
                "cedula": f"9000000{index:04d}",
                "nss": f"8000000{index:04d}",
                "server_revision": 1,
            }
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        rows = list(executor.map(hydrate, range(100)))

    with sqlite3.connect(database) as connection:
        count = connection.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0]
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert len(rows) == count == 100
    assert str(journal_mode).upper() == "WAL"
    assert int(busy_timeout) == 5000
