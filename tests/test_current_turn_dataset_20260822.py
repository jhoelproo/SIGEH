from __future__ import annotations

import importlib
import sqlite3
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from admission_hybrid import OperationalSessionService
from admission_v15_adapter import DEFAULT_V15_ROOT, _HybridDatabaseProxy, _load_v15_modules


SOURCE = "source-current"
CURRENT_TURN = 901
PREVIOUS_TURN = 900


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _CloudConnection:
    def __init__(self, rows):
        self.rows = rows
        self.params = ()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, params=()):
        self.params = tuple(params)
        return _Cursor(self.rows)


class _NoFallbackDatabase:
    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """CREATE TABLE atenciones(
                   id INTEGER PRIMARY KEY,global_attention_id TEXT,
                   origin_device_id TEXT,device_local_sequence INTEGER,
                   created_at_effective_utc TEXT
               )"""
        )
        self.connection.execute(
            "CREATE TABLE sync_outbox(entity_type TEXT,entity_uuid TEXT,sync_status TEXT)"
        )
        self.connection.executemany(
            """INSERT INTO atenciones(
                   id,global_attention_id,origin_device_id,
                   device_local_sequence,created_at_effective_utc
               ) VALUES(?,?,?,?,?)""",
            [
                (
                    int(row["id"]),
                    str(row["global_attention_id"]),
                    str(row.get("origin_device_id") or ""),
                    int(row.get("device_local_sequence") or 0),
                    str(row.get("created_at_effective_utc") or ""),
                )
                for row in self.rows
            ],
        )

    def _connect(self):
        return self.connection

    def obtener_atenciones_para_rango_real(self, **_kwargs):
        return list(self.rows)


def _row(attention_id, attention_type="EMERGENCIA", specialty="GENERAL"):
    return {
        "id": attention_id,
        "attention_id": attention_id,
        "global_attention_id": f"00000000-0000-4000-8000-{attention_id:012d}",
        "tipo_atencion": attention_type,
        "hoja_normalizada": specialty,
        "ars_display": "HUMANO",
        "created_at_effective_utc": "2026-08-22T14:02:00+00:00",
        "origin_device_id": "PC-A",
        "device_local_sequence": attention_id,
    }


def _proxy(rows):
    connection = _CloudConnection(rows)
    runtime = SimpleNamespace(
        offline=False,
        logger=None,
        host=SimpleNamespace(connection_factory=lambda: connection),
        operational_session=SimpleNamespace(
            turn_id=CURRENT_TURN,
            operational_source_id=SOURCE,
        ),
    )
    return _HybridDatabaseProxy(_NoFallbackDatabase(rows), runtime), connection


def _v15_module():
    _load_v15_modules(Path(DEFAULT_V15_ROOT))
    return importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")


def _turn_config():
    return {
        "fecha_base": date(2026, 8, 22),
        "turno_codigo": "8AM_8AM",
        "representante": "AUX TEST",
    }


def test_current_dataset_uses_only_central_source_and_turn_id():
    proxy, connection = _proxy([])

    assert proxy.build_current_admission_list_dataset() == []
    assert connection.params == (SOURCE, CURRENT_TURN, 500, 0)


def test_new_empty_turn_excludes_historical_rows_without_date_fallback():
    proxy, _connection = _proxy([])

    assert proxy.build_turn_dataset(
        turn_id=CURRENT_TURN, operational_source_id=SOURCE
    ) == []


def test_general_urgency_and_consultation_keep_existing_summary_rules():
    proxy, _connection = _proxy([
        _row(1),
        _row(2, "URGENCIA"),
        _row(3, "CONSULTA"),
    ])

    summary = proxy.refresh_turn_summary()

    assert summary["total"] == 1
    assert summary["GENERAL"] == 1
    assert summary["URGENCIAS"] == 1
    assert summary["CONSULTAS"] == 1


def test_previous_turn_identity_cannot_enter_current_dataset():
    proxy, connection = _proxy([])

    assert proxy.build_turn_dataset(
        turn_id=PREVIOUS_TURN, operational_source_id=SOURCE
    ) == []
    assert connection.params == (SOURCE, PREVIOUS_TURN, 500, 0)


def test_excel_and_turn_report_use_the_same_explicit_central_dataset():
    v15 = _v15_module()
    records = [_row(1), _row(2, "URGENCIA")]

    class Database:
        def __init__(self):
            self.requests = []

        def get_operational_station_snapshot(self):
            return {"turn_id": CURRENT_TURN, "operational_source_id": SOURCE}

        def build_turn_dataset(self, *, turn_id, operational_source_id):
            self.requests.append((turn_id, operational_source_id))
            return records

    database = Database()
    workbook, count = v15._construir_workbook_turno(database, _turn_config())
    try:
        assert count == 2
    finally:
        workbook.close()
    summary = v15.construir_resumen_turno(database, _turn_config())

    assert database.requests == [(CURRENT_TURN, SOURCE), (CURRENT_TURN, SOURCE)]
    assert summary["total_general"] == 1
    assert summary["cantidad_urgencias"] == 1


def test_turn_dataset_requires_both_central_identity_parts():
    proxy, _connection = _proxy([])

    assert proxy.build_turn_dataset(turn_id=CURRENT_TURN, operational_source_id="") == []
    assert proxy.build_turn_dataset(turn_id=None, operational_source_id=SOURCE) == []


def test_next_central_turn_id_is_allocated_from_central_records_only():
    class Connection:
        def execute(self, _sql):
            return SimpleNamespace(fetchone=lambda: {"next_turn_id": 932})

    assert OperationalSessionService._allocate_next_central_turn_id(Connection()) == 932


def test_ambiguous_current_turn_repair_changes_only_operational_identity():
    before = {
        "operational_session_id": "session-1",
        "status": "ACTIVE",
        "primary_device_id": "PC-A",
        "turn_id": 10,
        "operational_source_id": SOURCE,
        "turn_started_at": "2026-08-22T14:01:38+00:00",
        "generation": 12,
        "active_username": "aux-test",
    }
    after = {**before, "turn_id": 932}

    class Result:
        def __init__(self, value=None):
            self.value = value

        def fetchone(self):
            return self.value

    class Connection:
        def __init__(self):
            self.statements = []
            self.session_reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, _params=()):
            self.statements.append(str(sql))
            if "SELECT * FROM admission_operational_sessions" in sql:
                self.session_reads += 1
                return Result(before if self.session_reads == 1 else after)
            if "SELECT COUNT(*) AS count" in sql:
                return Result({"count": 41})
            return Result()

    connection = Connection()
    service = OperationalSessionService(lambda: connection)
    service._row_to_session = lambda row: SimpleNamespace(**row) if row else None
    service._allocate_next_central_turn_id = lambda _con: 932

    repaired = service.repair_ambiguous_current_turn_identity(
        operational_session_id="session-1",
        primary_device_id="PC-A",
    )

    assert repaired.turn_id == 932
    statements = "\n".join(connection.statements)
    assert "UPDATE admission_operational_sessions" in statements
    assert "UPDATE admission_operational_turn_intervals" in statements
    assert "UPDATE admission_attention_projection" not in statements


def test_local_replica_filters_operational_source_and_turn_not_legacy_turn_id(tmp_path):
    v15 = _v15_module()
    database_path = tmp_path / "pacientes.db"
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE dias_operativos(id INTEGER PRIMARY KEY,fecha_base TEXT);
            CREATE TABLE turnos(
                id INTEGER PRIMARY KEY,dia_operativo_id INTEGER,representante TEXT,
                tipo_turno TEXT
            );
            CREATE TABLE atenciones(
                id INTEGER PRIMARY KEY,paciente_id INTEGER,dia_operativo_id INTEGER,
                turno_id INTEGER,operational_turn_id INTEGER,
                operational_source_id TEXT,fecha TEXT,hora TEXT,created_at TEXT,
                nombre TEXT,hoja TEXT,ars TEXT,nss TEXT,cedula TEXT,edad_num INTEGER,
                unidad TEXT,tipo_atencion TEXT,estado TEXT
            );
            INSERT INTO dias_operativos VALUES(1,'2026-08-22');
            INSERT INTO turnos VALUES(1,1,'AUX TEST','8AM_8AM');
            INSERT INTO atenciones VALUES(
                1,1,1,1,901,'source-current','2026-08-22','10:00',
                '2026-08-22 10:00:00','ACTUAL','GENERAL','HUMANO','', '', 30,
                'AÑOS','EMERGENCIA','ACTIVA'
            );
            INSERT INTO atenciones VALUES(
                2,2,1,1,900,'source-current','2025-09-24','10:00',
                '2026-08-22 10:01:00','HISTORICO','GENERAL','HUMANO','', '', 30,
                'AÑOS','EMERGENCIA','ACTIVA'
            );
            """
        )
    manager = v15.DatabaseManager.__new__(v15.DatabaseManager)
    manager.db_name = str(database_path)

    rows = manager.obtener_atenciones_para_rango_real(
        operational_turn_id=CURRENT_TURN,
        operational_source_id=SOURCE,
    )

    assert [row["id"] for row in rows] == [1]


def test_same_turn_metadata_never_compares_the_replica_local_turn_id():
    session = SimpleNamespace(
        turn_id=42,
        turn_code="8AM_8AM",
        turn_started_at="2026-08-22T14:00:00+00:00",
    )

    assert _HybridAdmissionRuntimeMatcher.matches(session) is True


class _HybridAdmissionRuntimeMatcher:
    @staticmethod
    def matches(session):
        from admission_v15_adapter import _HybridAdmissionRuntime

        return _HybridAdmissionRuntime._matches_current_turn_metadata(
            session,
            {"turno_codigo": "8AM_8AM", "fecha_base": "22/08/2026"},
        )
