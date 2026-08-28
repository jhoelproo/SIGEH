import sqlite3
import uuid
from collections import defaultdict
from contextlib import closing
from pathlib import Path

import pytest

import admission_database_import as database_import
import patient_seed_tool
from patient_seed_tool import seed_admission_database_to_cloud, source_identity


def _legacy_database(path: Path) -> None:
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY,nombre TEXT,cedula TEXT,nss TEXT,created_at TEXT
            );
            CREATE TABLE turnos(
              id INTEGER PRIMARY KEY,fecha_inicio TEXT,representante TEXT,created_at TEXT
            );
            CREATE TABLE atenciones(
              id INTEGER PRIMARY KEY,paciente_id INTEGER,turno_id INTEGER,nombre TEXT,
              fecha TEXT,hora TEXT,hoja TEXT,tipo_atencion TEXT,estado TEXT,created_at TEXT
            );
            INSERT INTO pacientes VALUES(1,'UNO','001','101','2026-01-01 08:00:00');
            INSERT INTO turnos VALUES(1,'2026-01-01 08:00:00','AUX','2026-01-01');
            INSERT INTO atenciones VALUES(
              1,1,1,'UNO','2026-01-01','09:00','GENERAL','EMERGENCIA','ACTIVA',
              '2026-01-01 09:00:00'
            );
            """
        )


def test_legacy_source_identity_does_not_change_when_database_grows(tmp_path):
    path = tmp_path / "pacientes.db"
    _legacy_database(path)
    with closing(sqlite3.connect(path)) as connection:
        before = source_identity(connection)
        connection.executemany(
            "INSERT INTO pacientes VALUES(?,?,?,?,?)",
            [
                (index, f"P{index}", str(index), str(index), "2026-02-01")
                for index in range(2, 502)
            ],
        )
        connection.commit()
        after = source_identity(connection)
    assert before == after


def test_explicit_source_identity_and_modern_ids_are_preserved(tmp_path):
    path = tmp_path / "modern.db"
    _legacy_database(path)
    patient_id = str(uuid.uuid4())
    attention_id = str(uuid.uuid4())
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE app_metadata(clave TEXT PRIMARY KEY,valor TEXT);
            ALTER TABLE pacientes ADD COLUMN global_patient_id TEXT;
            ALTER TABLE atenciones ADD COLUMN global_attention_id TEXT;
            """
        )
        connection.execute(
            "INSERT INTO app_metadata VALUES('integration.source_instance_id','SOURCE-REAL')"
        )
        connection.execute(
            "UPDATE pacientes SET global_patient_id=? WHERE id=1", (patient_id,)
        )
        connection.execute(
            "UPDATE atenciones SET global_attention_id=? WHERE id=1", (attention_id,)
        )
        connection.commit()
        assert source_identity(connection) == "SOURCE-REAL"
    _sha, source_id, patient_count, payloads = (
        database_import.AdmissionDatabaseImporter._payloads(path)
    )
    assert source_id == "SOURCE-REAL"
    assert patient_count == 1
    assert payloads[0]["global_attention_id"] == attention_id
    assert payloads[0]["specialty"] == "GENERAL"


def test_source_change_requires_reanalysis_before_any_cloud_access(tmp_path):
    path = tmp_path / "changed.db"
    _legacy_database(path)

    def forbidden_connection():
        raise AssertionError("PostgreSQL must not be opened for a stale preview")

    importer = database_import.AdmissionDatabaseImporter(forbidden_connection)
    with pytest.raises(ValueError, match="cambió después del análisis"):
        importer.apply(
            str(uuid.uuid4()),
            current_user={"username": "admin", "role": "Administrador"},
            device_id="PC-1",
            sqlite_path=path,
            expected_source_sha256="0" * 64,
        )


def test_sqlite_inspection_reports_progress_and_uses_historical_context(tmp_path):
    path = tmp_path / "legacy.db"
    _legacy_database(path)
    progress = []
    _sha, source_id, _patients, payloads = (
        database_import.AdmissionDatabaseImporter._payloads(
            path,
            progress=lambda phase, current, total: progress.append(
                (phase, current, total)
            ),
        )
    )
    context = database_import._historical_context(payloads[0], source_id)
    assert progress
    assert context["admission_username"] == "IMPORTACION HISTORICA"
    assert uuid.UUID(context["operational_session_id"])
    assert uuid.UUID(context["operational_source_id"])
    assert context["origin_device_id"].startswith("IMPORT:")


def test_initial_baseline_context_forces_one_canonical_central_turn():
    baseline = {
        "operational_session_id": "55555555-5555-4555-8555-555555555555",
        "operational_source_id": "44444444-4444-4444-8444-444444444444",
        "turn_id": 316,
        "generation": 42,
        "active_username": "aux.inicial",
    }
    first = database_import._historical_context(
        {"turn_id": 1}, "PRIMARY-SOURCE", baseline_context=baseline
    )
    second = database_import._historical_context(
        {"turn_id": 99}, "PRIMARY-SOURCE", baseline_context=baseline
    )

    assert first == second
    assert first["turn_id"] == 316
    assert first["operational_source_id"] == baseline["operational_source_id"]
    assert first["operational_session_id"] == baseline["operational_session_id"]
    assert first["reconciliation_status"] == "INITIAL_BASELINE"


@pytest.mark.parametrize("rows", [[], [{"turn_id": 1}, {"turn_id": 2}]])
def test_seed_requires_exactly_one_active_central_turn(rows):
    class Connection:
        def execute(self, _query):
            return _Rows(rows)

    with pytest.raises(ValueError, match="exactamente un turno central activo"):
        database_import.AdmissionDatabaseImporter._active_baseline_context(
            Connection()
        )


def test_verified_backup_is_recoverable_and_does_not_replace_source(tmp_path):
    path = tmp_path / "primary.sqlite3"
    _legacy_database(path)
    source_sha256 = database_import._stream_sha256(path)

    backup_path, backup_sha256 = database_import._verified_sqlite_backup(
        path, source_sha256
    )
    second_backup_path, second_backup_sha256 = (
        database_import._verified_sqlite_backup(path, source_sha256)
    )

    assert Path(backup_path).parent == tmp_path / "BACKUPS"
    assert Path(backup_path).is_file()
    assert backup_sha256 == database_import._stream_sha256(Path(backup_path))
    assert second_backup_path != backup_path
    assert second_backup_sha256 == database_import._stream_sha256(
        Path(second_backup_path)
    )
    with closing(sqlite3.connect(path)) as source:
        assert source.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1
    with closing(sqlite3.connect(backup_path)) as backup:
        assert backup.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1


def test_verified_backup_removes_partial_file_when_integrity_check_fails(
    monkeypatch, tmp_path
):
    path = tmp_path / "invalid.sqlite3"
    path.touch()

    class Connection:
        def __init__(self, *, quick_check="ok"):
            self.quick_check = quick_check

        def backup(self, _target):
            return None

        def commit(self):
            return None

        def execute(self, _query):
            return _Rows([(self.quick_check,)])

        def close(self):
            return None

    connections = iter((Connection(), Connection(quick_check="corrupt")))
    monkeypatch.setattr(database_import.sqlite3, "connect", lambda *_a, **_k: next(connections))

    with pytest.raises(ValueError, match="quick_check"):
        database_import._verified_sqlite_backup(path, "a" * 64)

    assert not list((tmp_path / "BACKUPS").glob("*.partial"))


def test_verified_backup_rejects_an_empty_verification_digest(monkeypatch, tmp_path):
    path = tmp_path / "primary.sqlite3"
    _legacy_database(path)
    monkeypatch.setattr(database_import, "_stream_sha256", lambda *_a, **_k: "")

    with pytest.raises(ValueError, match="No se pudo verificar"):
        database_import._verified_sqlite_backup(path, "a" * 64)

    assert not list((tmp_path / "BACKUPS").glob("*.sqlite3"))


def test_initial_baseline_identity_rejects_fingerprint_or_source_changes():
    database_import._require_compatible_initial_baseline(
        existing_fingerprint="hash-a",
        existing_source_id="PRIMARY-A",
        source_sha256="hash-a",
        source_id="PRIMARY-A",
    )
    with pytest.raises(ValueError, match="otra fuente"):
        database_import._require_compatible_initial_baseline(
            existing_fingerprint="hash-a",
            existing_source_id="PRIMARY-A",
            source_sha256="hash-b",
            source_id="PRIMARY-A",
        )
    with pytest.raises(ValueError, match="otra fuente"):
        database_import._require_compatible_initial_baseline(
            existing_fingerprint="hash-a",
            existing_source_id="PRIMARY-A",
            source_sha256="hash-a",
            source_id="SECONDARY-B",
        )


@pytest.mark.parametrize(
    "invalid_context",
    [
        {"turn_id": 0, "generation": 1, "active_username": "aux"},
        {"turn_id": 1, "generation": 0, "active_username": "aux"},
        {"turn_id": 1, "generation": 1, "active_username": ""},
    ],
)
def test_initial_baseline_rejects_incomplete_operational_context(invalid_context):
    invalid_context.update(
        operational_session_id="55555555-5555-4555-8555-555555555555",
        operational_source_id="44444444-4444-4444-8444-444444444444",
    )

    with pytest.raises(ValueError, match="contexto operacional válido"):
        database_import._historical_context(
            {}, "CENTRAL_BASELINE", baseline_context=invalid_context
        )


def test_active_baseline_context_accepts_positional_database_rows():
    row = (
        "55555555-5555-4555-8555-555555555555",
        "44444444-4444-4444-8444-444444444444",
        316,
        42,
        "aux.inicial",
        "PC-PRIMARY",
    )

    context = database_import.AdmissionDatabaseImporter._active_baseline_context(
        type("Connection", (), {"execute": lambda _self, _query: _Rows([row])})()
    )

    assert context["turn_id"] == 316
    assert context["active_username"] == "aux.inicial"


def test_seed_classification_never_overwrites_a_different_central_record():
    classification, _local_revision, _cloud_revision = (
        database_import._classify_import_row(
            {"version": 4, "is_deleted": False},
            {"server_revision": 4, "is_deleted": False},
            lambda _payload, _cloud: False,
            allow_updates=False,
        )
    )

    assert classification == "CONFLICT"


def test_importer_uses_streaming_hash_and_batched_staging_source_contract():
    source = Path(database_import.__file__).read_text(encoding="utf-8")
    assert ".read_bytes()" not in source
    assert "fetchmany(READ_CHUNK_SIZE)" in source
    assert "execute_values(cursor, query, rows" in source


class _Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _SeedCloud:
    def __init__(self):
        self.patients = {}
        self.mapping = {}
        self.registry_writes = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, params=None):
        compact = " ".join(query.split())
        if compact.startswith("SELECT global_patient_id::TEXT"):
            return _Rows(
                [
                    (
                        global_id,
                        row["cedula_normalized"],
                        row["nss_normalized"],
                        False,
                    )
                    for global_id, row in self.patients.items()
                ]
            )
        if compact.startswith("SELECT legacy_id,global_uuid::TEXT"):
            source_id = str((params or ("",))[0])
            return _Rows(
                [
                    (legacy_id, global_id)
                    for (mapped_source, legacy_id), global_id in self.mapping.items()
                    if mapped_source == source_id
                ]
            )
        if compact.startswith("INSERT INTO admission_patient_seed_registry"):
            self.registry_writes += 1
        return _Rows()


def test_patient_seed_merges_growth_and_preserves_modern_patient_id(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "modern-seed.db"
    _legacy_database(path)
    global_patient_id = str(uuid.uuid4())
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(
            """
            CREATE TABLE app_metadata(clave TEXT PRIMARY KEY,valor TEXT);
            ALTER TABLE pacientes ADD COLUMN global_patient_id TEXT;
            """
        )
        connection.execute(
            "INSERT INTO app_metadata VALUES('integration.source_instance_id','SOURCE-SEED')"
        )
        connection.execute(
            "UPDATE pacientes SET global_patient_id=? WHERE id=1",
            (global_patient_id,),
        )
        connection.commit()

    cloud = _SeedCloud()
    monkeypatch.setattr(
        patient_seed_tool.CentralPatientDirectoryRepository,
        "ensure_schema",
        lambda _self: None,
    )

    def capture_execute_values(_connection, query, rows):
        if "legacy_entity_uuid_map" in query:
            for _entity, source_id, legacy_id, global_id in rows:
                cloud.mapping[(str(source_id), int(legacy_id))] = str(global_id)
        elif "admission_patient_directory(" in query:
            for row in rows:
                cloud.patients[str(row[0])] = {
                    "cedula_normalized": str(row[3]),
                    "nss_normalized": str(row[5]),
                }

    monkeypatch.setattr(patient_seed_tool, "_execute_values", capture_execute_values)
    progress = []
    first = seed_admission_database_to_cloud(
        path,
        lambda: cloud,
        progress=lambda current, total: progress.append((current, total)),
    )
    assert first["inserted"] == 1
    assert global_patient_id in cloud.patients

    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO pacientes(id,nombre,cedula,nss,created_at) "
            "VALUES(2,'DOS','002','102','2026-02-01')"
        )
        connection.commit()
    second = seed_admission_database_to_cloud(path, lambda: cloud)
    assert second["source_instance_id"] == first["source_instance_id"]
    assert second["inserted"] == 1
    assert second["already_present"] == 1
    assert len(cloud.patients) == 2
    assert progress[-1] == (1, 1)
    assert cloud.registry_writes == 2


@pytest.mark.parametrize(
    ("cloud", "deleted", "matches", "expected"),
    [
        ({"is_deleted": True, "server_revision": 2}, False, False, "SKIP_TOMBSTONED"),
        ({"server_revision": 1}, False, True, "EXISTING"),
        ({"server_revision": 5}, False, False, "SKIPPED_CLOUD_NEWER"),
        ({"server_revision": 1}, False, False, "UPDATE"),
        (None, True, False, "SKIP_ORPHAN_TOMBSTONE"),
        (None, False, False, "INSERT"),
    ],
)
def test_import_classification_edges(cloud, deleted, matches, expected):
    result = database_import._classify_import_row(
        {"version": 1, "is_deleted": deleted},
        cloud,
        lambda _payload, _cloud: matches,
    )
    assert result[0] == expected


def test_import_validation_edges_and_timestamp_fallbacks(tmp_path):
    assert database_import._effective_timestamp(
        "2026-01-01", "08:00:00", "2026-01-01T09:00:00Z"
    ).startswith("2026-01-01T09:00:00")
    assert database_import._effective_timestamp(
        "01/02/2026", "08:30", "invalid"
    ).startswith("2026-02-01T08:30:00")
    assert database_import._uuid("invalid", "fallback") == database_import._uuid(
        None, "fallback"
    )
    database_import._insert_staging_batch(object(), [])

    missing_attentions = tmp_path / "missing-atenciones.db"
    with closing(sqlite3.connect(missing_attentions)) as connection:
        connection.execute("CREATE TABLE pacientes(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="atenciones"):
        database_import.AdmissionDatabaseImporter._payloads(missing_attentions)

    missing_patients = tmp_path / "missing-pacientes.db"
    with closing(sqlite3.connect(missing_patients)) as connection:
        connection.execute("CREATE TABLE atenciones(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="pacientes"):
        database_import.AdmissionDatabaseImporter._payloads(missing_patients)


def test_patient_seed_helper_edges(tmp_path):
    with pytest.raises(FileNotFoundError):
        seed_admission_database_to_cloud(tmp_path / "missing.db", lambda: None)
    assert patient_seed_tool._document_index(
        [("deleted", "1", "2", True), ("active", "", "22", False)]
    ) == {("NSS", "22"): {"active"}}
    payloads, conflicts, inserted, already, conflict_count = (
        patient_seed_tool._prepare_patient_batch(
            [
                {"id": 1, "nombre": "A", "cedula": "001", "nss": ""},
                {"id": 2, "nombre": "B", "cedula": "002", "nss": ""},
            ],
            "SOURCE",
            {1: "mapped"},
            {"mapped"},
            defaultdict(
                set,
                {("CEDULA", "002"): {"match-a", "match-b"}},
            ),
        )
    )
    assert already == 1
    assert inserted == 1
    assert conflict_count == 1
    assert len(conflicts) == 1
    assert len(payloads) == 1
