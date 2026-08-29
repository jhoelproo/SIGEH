import sqlite3
import logging
import sys
from datetime import date, datetime
from types import ModuleType
from types import SimpleNamespace

import pytest

from admission_v15_adapter import (
    TurnDatasetResult,
    _HybridAdmissionRuntime,
    _HybridDatabaseProxy,
    _V15BackgroundRefreshCoordinator,
    _bind_hybrid_shutdown,
)


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _CloudConnection:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, _sql, _params=()):
        return _Cursor(
            [
                {
                    "id": 200,
                    "fecha": "2026-08-12",
                    "hora": "12:00:00",
                    "nombre": "CENTRAL",
                    "hoja": "GENERAL",
                    "ars": "HUMANO",
                    "nss": "",
                    "cedula": "",
                    "edad_num": "",
                    "unidad": "",
                    "tipo_atencion": "EMERGENCIA",
                    "global_attention_id": "cloud-uuid",
                    "created_at_effective_utc": "2026-08-12T16:00:00+00:00",
                    "origin_device_id": "PC2",
                    "device_local_sequence": 2,
                    "sync_state": "SYNCHRONIZED",
                }
            ]
        )


class _CapturingCloudConnection(_CloudConnection):
    def __init__(self):
        self.query = ""
        self.params = ()

    def execute(self, query, params=()):
        self.query = str(query)
        self.params = tuple(params)
        return super().execute(query, params)


class _LocalDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute(
            """CREATE TABLE atenciones(
                   id INTEGER PRIMARY KEY,global_attention_id TEXT,
                   origin_device_id TEXT,device_local_sequence INTEGER,
                   created_at_effective_utc TEXT,
                   operational_turn_id INTEGER,operational_source_id TEXT
               )"""
        )
        self.connection.execute(
            "CREATE TABLE sync_outbox(entity_type TEXT,entity_uuid TEXT,sync_status TEXT)"
        )
        self.connection.executemany(
            """INSERT INTO atenciones(
                   id,global_attention_id,origin_device_id,
                   device_local_sequence,created_at_effective_utc,
                   operational_turn_id,operational_source_id
               ) VALUES(?,?,?,?,?,?,?)""",
            (
                (100, "pending-uuid", "PC1", 1, "2026-08-12T16:01:00+00:00", 316, "44444444-4444-4444-8444-444444444444"),
                (99, "old-local-uuid", "PC1", 2, "2026-03-01T12:00:00+00:00", 315, "44444444-4444-4444-8444-444444444444"),
            ),
        )
        self.connection.execute(
            "INSERT INTO sync_outbox VALUES('attention','pending-uuid','PENDING')"
        )

    def _connect(self):
        return self.connection

    def listar_atenciones(self, filtro_texto=None, limite=200, offset=0):
        return [
            {
                "id": 100,
                "fecha": "2026-08-12",
                "hora": "12:01:00",
                "nombre": "LOCAL PENDING",
                "hoja": "GENERAL",
                "ars": "HUMANO",
                "nss": "",
                "cedula": "",
                "edad_num": 1,
                "unidad": "Años",
                "tipo_atencion": "EMERGENCIA",
            },
            {
                "id": 99,
                "fecha": "2026-03-01",
                "hora": "08:00:00",
                "nombre": "OLD LOCAL",
                "hoja": "GENERAL",
                "ars": "HUMANO",
                "nss": "",
                "cedula": "",
                "edad_num": 1,
                "unidad": "Años",
                "tipo_atencion": "EMERGENCIA",
            },
        ]

    def obtener_atenciones_para_rango_real(self, **kwargs):
        requested_turn = kwargs.get("operational_turn_id")
        requested_source = kwargs.get("operational_source_id")
        rows = []
        for row in self.listar_atenciones():
            stored = self.connection.execute(
                "SELECT operational_turn_id,operational_source_id FROM atenciones WHERE id=?",
                (row["id"],),
            ).fetchone()
            if requested_turn is not None and stored[0] != requested_turn:
                continue
            if requested_source and stored[1] != requested_source:
                continue
            rows.append(row)
        return rows

    def listar_atenciones_filtradas(self, **_kwargs):
        return []


def test_online_v15_history_reads_postgresql_and_keeps_only_local_pending_rows():
    database = _LocalDatabase()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    rows = proxy.listar_atenciones(limite=200, offset=0)

    assert {row["id"] for row in rows} == {100, 200}
    assert {row["nombre"] for row in rows} == {"CENTRAL", "LOCAL PENDING"}


def test_excel_dataset_reads_the_same_central_current_turn_view():
    database = _LocalDatabase()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    rows = proxy.build_current_admission_list_dataset(turn_id=999)

    assert [row["id"] for row in rows] == [200, 100]
    assert {row["global_attention_id"] for row in rows} == {
        "cloud-uuid",
        "pending-uuid",
    }


def test_turn_summary_uses_the_same_central_current_turn_dataset_as_history():
    database = _LocalDatabase()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    summary = proxy.refresh_turn_summary()

    assert summary["total"] == 2
    assert summary["GENERAL"] == 2
    assert summary["URGENCIAS"] == 0
    assert summary["CONSULTAS"] == 0


def test_summary_result_reapplies_the_sidebar_when_its_fingerprint_is_unchanged():
    applied = []
    summary = {
        "total": 1,
        "sin_seguro": 0,
        "GENERAL": 1,
        "PEDIATRIA": 0,
        "GINECOLOGIA": 0,
        "URGENCIAS": 0,
        "CONSULTAS": 0,
    }
    controller = _V15BackgroundRefreshCoordinator.__new__(
        _V15BackgroundRefreshCoordinator
    )
    controller._summary_busy = True
    controller._summary_pending = False
    controller._summary_reason = "history_dataset_changed"
    controller._summary_started_at = 0.0
    controller._summary_fingerprint = tuple(
        (key, int(summary[key]))
        for key in (
            "total", "sin_seguro", "GENERAL", "PEDIATRIA",
            "GINECOLOGIA", "URGENCIAS", "CONSULTAS",
        )
    )
    controller.admission = SimpleNamespace(
        db=SimpleNamespace(_runtime=None),
        _actualizar_resumen_turno_panel=lambda *, forzar: applied.append(forzar),
    )

    controller._summary_ready(summary)

    assert applied == [True]


def test_summary_result_delivers_the_current_turn_dataset_to_the_sidebar():
    delivered = []
    summary = {
        "total": 1,
        "sin_seguro": 0,
        "GENERAL": 1,
        "PEDIATRIA": 0,
        "GINECOLOGIA": 0,
        "URGENCIAS": 0,
        "CONSULTAS": 0,
    }
    controller = _V15BackgroundRefreshCoordinator.__new__(
        _V15BackgroundRefreshCoordinator
    )
    controller._summary_busy = True
    controller._summary_pending = False
    controller._summary_reason = "history_dataset_changed"
    controller._summary_started_at = 0.0
    controller._summary_fingerprint = ()
    controller.admission = SimpleNamespace(
        db=SimpleNamespace(_runtime=None),
        apply_turn_summary=lambda value, *, reason: delivered.append(
            (dict(value), reason)
        ),
    )

    controller._summary_ready(summary)

    assert delivered == [(summary, "history_dataset_changed")]


def test_offline_excel_dataset_uses_local_turn_and_deterministic_order():
    database = _LocalDatabase()
    runtime = SimpleNamespace(
        offline=True,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    rows = proxy.build_current_admission_list_dataset()

    assert [row["id"] for row in rows] == [100]


def test_history_queries_postgresql_even_when_the_replica_is_available():
    database = _LocalDatabase()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
        logger=logging.getLogger("test.history"),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    rows = proxy.listar_atenciones(limite=200, offset=0)

    assert [row["nombre"] for row in rows] == ["LOCAL PENDING", "CENTRAL"]


def test_this_turn_history_uses_central_source_and_turn_from_local_replica():
    database = _LocalDatabase()
    connection = _CapturingCloudConnection()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: connection),
        operational_session=SimpleNamespace(
            operational_session_id="central-session",
            generation=42,
            turn_id=316,
            operational_source_id="central-source",
        ),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    proxy.listar_atenciones_filtradas(
        modo="Este turno",
        turno_id=999,
        limite=20,
        offset=0,
    )

    assert "admission_attention_projection" in connection.query
    assert "p.operational_source_id::TEXT=%s" in connection.query
    assert "p.turn_id=%s" in connection.query
    assert connection.params[:2] == ("central-source", 316)


def test_online_history_survives_local_hydration_failure():
    class _BrokenStore:
        def hydrate_remote_events(self, _events):
            raise sqlite3.OperationalError("disk unavailable")

    database = _LocalDatabase()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
        store=_BrokenStore(),
        logger=logging.getLogger("test.history-hydration-failure"),
    )
    proxy = _HybridDatabaseProxy(database, runtime)

    rows = proxy.listar_atenciones(limite=200, offset=0)

    assert {row["nombre"] for row in rows} == {"CENTRAL", "LOCAL PENDING"}


def test_online_history_survives_local_pending_read_failure():
    class _BrokenLocalDatabase(_LocalDatabase):
        def listar_atenciones(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("local replica unavailable")

    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
        logger=logging.getLogger("test.history-local-failure"),
    )
    proxy = _HybridDatabaseProxy(_BrokenLocalDatabase(), runtime)

    rows = proxy.listar_atenciones(limite=200, offset=0)

    assert [row["nombre"] for row in rows] == ["CENTRAL"]


def test_temporary_central_failure_switches_to_offline_local_history():
    marked_offline = []

    def unavailable():
        raise ConnectionError("central unavailable")

    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=unavailable),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
        logger=logging.getLogger("test.history-offline-fallback"),
        _temporary=lambda _error: True,
        connection_supervisor=SimpleNamespace(
            mark_offline=lambda error: marked_offline.append(error)
        ),
        status_message="",
    )
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)

    rows = proxy.listar_atenciones(limite=200, offset=0)

    assert runtime.offline is True
    assert runtime.status_message.startswith("Sin conexión")
    assert len(marked_offline) == 1
    assert {row["nombre"] for row in rows} == {"LOCAL PENDING", "OLD LOCAL"}


def test_offline_history_never_opens_the_central_connection():
    runtime = SimpleNamespace(
        offline=True,
        host=SimpleNamespace(
            connection_factory=lambda: (_ for _ in ()).throw(
                AssertionError("central must not be opened")
            )
        ),
        operational_session=SimpleNamespace(turn_id=316),
    )
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)

    rows = proxy.listar_atenciones(limite=200, offset=0)

    assert [row["id"] for row in rows] == [100, 99]


def test_projection_readthrough_has_an_explicit_offline_replica_path():
    runtime = SimpleNamespace(offline=True)
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)

    rows = proxy._legacy_projection_readthrough(
        "listar_atenciones", (), {"limite": 200, "offset": 0}
    )

    assert [row["id"] for row in rows] == [100, 99]


def test_background_lookup_executes_cedula_and_nss_queries():
    values = []

    class Coordinator:
        @staticmethod
        def submit_background(operation, _consume):
            values.append(operation())

    controller = _V15BackgroundRefreshCoordinator.__new__(
        _V15BackgroundRefreshCoordinator
    )
    controller._lookup_generation = 0
    controller.coordinator = Coordinator()
    controller.admission = SimpleNamespace(
        entry_cedula=SimpleNamespace(get=lambda: "001-002"),
        entry_nss=SimpleNamespace(get=lambda: " nss-9 "),
        db=SimpleNamespace(
            search_patient_directory=lambda **query: {"query": query}
        ),
    )

    controller.request_lookup("cedula")
    controller.request_lookup("nss")

    assert values == [
        {"query": {"cedula": "001002"}},
        {"query": {"nss": "NSS-9"}},
    ]


def test_central_state_creates_v15_turn_mirror_without_primary_transition(monkeypatch):
    module_name = "tests.fake_v15_turn_mirror"
    module = ModuleType(module_name)
    saved = []
    module.cargar_turno_config = lambda permitir_vencido=False: {}
    module.guardar_turno_config = lambda *args, **kwargs: saved.append((args, kwargs)) or True
    module.turno_config_es_vigente = lambda _config, momento=None: False
    monkeypatch.setitem(sys.modules, module_name, module)

    class _MirrorDatabase:
        __module__ = module_name

        def __init__(self):
            self.calls = []

        def obtener_o_crear_turno(self, config, *, administrative_override=False):
            self.calls.append((dict(config), administrative_override))
            return 27

    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime._bound_database = _MirrorDatabase()
    runtime.logger = logging.getLogger("test.history")
    runtime._mirror_v15_turn_config(
        SimpleNamespace(
            active_user_display_name="Auxiliar Prueba",
            active_username="aux.test",
            turn_code="8AM_8AM",
            turn_started_at="2026-08-22T08:10:00+00:00",
            turn_id=350,
            generation=80,
        )
    )
    runtime._mirror_v15_turn_config(
        SimpleNamespace(
            active_user_display_name="Auxiliar Prueba",
            active_username="aux.test",
            turn_code="8AM_8AM",
            turn_started_at="2026-08-22T08:10:00+00:00",
            turn_id=350,
            generation=80,
        )
    )

    assert saved
    assert runtime._bound_database.calls == [
        (
            {
                "representante": "Auxiliar Prueba",
                "turno_codigo": "8AM_8AM",
                "fecha_base": date(2026, 8, 22),
                "inicio_real_dt": datetime(2026, 8, 22, 4, 10),
                "administrative_override": True,
                "override_reason": "Espejo operativo central",
            },
            True,
        )
    ]


def test_primary_backfills_blank_central_turn_code_before_mirroring(monkeypatch):
    module_name = "tests.fake_v15_legacy_turn_code"
    module = ModuleType(module_name)
    module.cargar_turno_config = lambda permitir_vencido=False: {
        "turno_codigo": "8AM_8PM"
    }
    module.guardar_turno_config = lambda *_args, **_kwargs: True
    module.turno_config_es_vigente = lambda _config, momento=None: True
    monkeypatch.setitem(sys.modules, module_name, module)

    class _MirrorDatabase:
        __module__ = module_name

        def __init__(self):
            self.calls = []

        def obtener_o_crear_turno(self, config, *, administrative_override=False):
            self.calls.append((dict(config), administrative_override))
            return 28

    class _SessionService:
        def __init__(self):
            self.calls = []

        def backfill_missing_turn_code(self, **kwargs):
            self.calls.append(kwargs)
            return SimpleNamespace(turn_code="8AM_8PM")

    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime._bound_database = _MirrorDatabase()
    runtime.logger = logging.getLogger("test.history")
    runtime.host = SimpleNamespace(
        device_id="PC-PRIMARY",
        session_id="LOGIN-PRIMARY",
        user={"username": "admin"},
    )
    runtime.session_service = _SessionService()
    runtime._mirror_v15_turn_config(
        SimpleNamespace(
            operational_session_id="session-central",
            primary_device_id="PC-PRIMARY",
            active_user_display_name="Auxiliar Prueba",
            active_username="aux.test",
            turn_code="",
            turn_started_at="2026-08-22T08:00:00+00:00",
            turn_id=350,
            generation=80,
        )
    )

    assert runtime.session_service.calls == [
        {
            "operational_session_id": "session-central",
            "primary_device_id": "PC-PRIMARY",
            "primary_login_session_id": "LOGIN-PRIMARY",
            "expected_generation": 80,
            "turn_code": "8AM_8PM",
            "changed_by": "admin",
        }
    ]
    assert runtime._bound_database.calls[0][0]["turno_codigo"] == "8AM_8PM"


def test_v15_turn_display_config_uses_the_adopted_central_snapshot():
    from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App

    app = object.__new__(App)
    app.current_shift_context = {
        "turn_code": "8PM_8AM",
        "turn_started_at": "2026-08-22T20:00:00+00:00",
        "representative_display_name": "AUX TEST",
    }

    config = app._turno_config_desde_snapshot_operacional()

    assert config is not None
    assert config["turno_codigo"] == "8PM_8AM"
    assert config["fecha_base"] == date(2026, 8, 22)
    assert config["inicio_real_dt"].tzinfo is None


def test_local_turn_materialization_never_invokes_a_central_transition():
    class _TurnDatabase:
        def obtener_o_crear_turno(self, _config, **_kwargs):
            return 77

    class _Runtime:
        class StationRole:
            PRIMARY = "PRIMARY"
            SECONDARY = "SECONDARY"

        def __init__(self):
            self.role = self.StationRole.PRIMARY
            self.logger = logging.getLogger("test.turn-path")
            self.turn_guard_calls = 0
            self.transition_guard_calls = 0
            self.changed = []

        def require_write(self):
            self.turn_guard_calls += 1

    runtime = _Runtime()
    proxy = _HybridDatabaseProxy(_TurnDatabase(), runtime)

    assert proxy.obtener_o_crear_turno({"turno_codigo": "8AM_8PM"}) == 77
    assert runtime.turn_guard_calls == 1
    assert runtime.transition_guard_calls == 0
    assert runtime.changed == []


def test_adopted_central_snapshot_updates_turn_and_representative_before_mirror():
    from admission_hybrid import ConnectivityState, OperationalState, StationRole

    runtime = object.__new__(_HybridAdmissionRuntime)
    runtime.host = SimpleNamespace(current_shift={})
    runtime.attachment = None
    runtime._operational_state = None
    runtime.offline = True
    runtime.status_message = ""
    runtime.logger = logging.getLogger("test.central-adoption")
    state = OperationalState(
        operational_session_id="central-session",
        generation=12,
        active_user_id="42",
        active_username="aux-test",
        active_user_display_name="AUX TEST",
        turn_id=77,
        turn_code="8PM_8AM",
        primary_device_id="PC1",
        primary_login_session_id="login-pc1",
        local_device_id="PC2",
        local_login_session_id="login-pc2",
        device_role=StationRole.SECONDARY,
        device_attached=True,
        user_matches_operational=True,
        write_allowed=True,
        connection_state=ConnectivityState.CONNECTED,
        operational_source_id="central-source",
        operational_revision=6,
        turn_started_at="2026-08-22T20:00:00+00:00",
        turn_ends_at="2026-08-23T08:00:00+00:00",
    )

    runtime.adopt_central_operational_state(state)

    assert runtime.host.current_shift == {
        "turn_id": 77,
        "turn_code": "8PM_8AM",
        "owner_user_id": "42",
        "owner_username": "aux-test",
        "representative_display_name": "AUX TEST",
        "generation": 12,
        "operational_revision": 6,
        "operational_session_id": "central-session",
        "operational_source_id": "central-source",
        "turn_started_at": "2026-08-22T20:00:00+00:00",
        "turn_ends_at": "2026-08-23T08:00:00+00:00",
    }
    assert runtime.offline is False


def test_turn_summary_uses_canonical_dataset_and_excludes_urgency_and_consultation():
    rows = [
        {"tipo_atencion": "EMERGENCIA", "hoja_normalizada": "GENERAL", "ars_display": "SIN SEGURO"},
        {"tipo_atencion": "EMERGENCIA", "hoja_normalizada": "PEDIATRIA", "ars_display": "HUMANO"},
        {"tipo_atencion": "EMERGENCIA", "hoja_normalizada": "GINECOLOGIA", "ars_display": ""},
        {"tipo_atencion": "URGENCIA", "hoja_normalizada": "GENERAL", "ars_display": "HUMANO"},
        {"tipo_atencion": "CONSULTA", "hoja_normalizada": "GENERAL", "ars_display": "HUMANO"},
    ]
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="central-source",
            generation=12,
            operational_revision=6,
        ),
        logger=logging.getLogger("test.turn-summary"),
    )
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)
    object.__setattr__(
        proxy,
        "load_turn_dataset_result",
        lambda identity: TurnDatasetResult(
            "VALID", identity[0], identity[1], identity[2], identity[3],
            tuple(rows), "CENTRAL", "", "2026-08-22T00:00:00+00:00",
            central_count=len(rows),
        ),
    )

    summary = proxy.refresh_turn_summary()

    assert summary["total"] == 3
    assert summary["GENERAL"] == 1
    assert summary["PEDIATRIA"] == 1
    assert summary["GINECOLOGIA"] == 1
    assert summary["URGENCIAS"] == 1
    assert summary["CONSULTAS"] == 1
    assert summary["sin_seguro"] == 2


def test_central_turn_filters_cover_legacy_identity_and_explicit_turn_paths():
    proxy = _HybridDatabaseProxy(
        _LocalDatabase(),
        SimpleNamespace(
            operational_session=SimpleNamespace(
                operational_source_id="",
                operational_session_id="session-legacy",
                generation=4,
                turn_id=316,
            )
        ),
    )
    where = []
    params = []
    proxy._append_central_turn_filters({}, "Turno actual", where, params)
    assert where == ["p.operational_session_id=%s", "p.generation=%s"]
    assert params == ["session-legacy", 4]

    proxy._runtime.operational_session = None
    where = []
    params = []
    proxy._append_central_turn_filters({}, "Este turno", where, params)
    assert where == []
    assert params == []

    proxy._runtime.operational_session = SimpleNamespace(
        operational_source_id="",
        operational_session_id="",
        generation=0,
        turn_id=None,
    )
    where = []
    params = []
    proxy._append_central_turn_filters(
        {"turno_id": 88, "operational_source_id": "requested-source"},
        "Este turno",
        where,
        params,
    )
    assert where == ["p.operational_source_id::TEXT=%s", "p.turn_id=%s"]
    assert params == ["requested-source", 88]

    where = []
    params = []
    proxy._append_central_turn_filters(
        {"turno_id": 89}, "Por turno", where, params
    )
    assert where == ["p.turn_id=%s"]
    assert params == [89]


def test_online_history_keeps_central_rows_when_hydration_fails_without_logger():
    class BrokenStore:
        @staticmethod
        def hydrate_remote_events(_events):
            raise sqlite3.OperationalError("replica unavailable")

    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
        store=BrokenStore(),
    )
    rows = _HybridDatabaseProxy(_LocalDatabase(), runtime).listar_atenciones()
    assert {row["nombre"] for row in rows} == {"CENTRAL", "LOCAL PENDING"}


def test_online_history_ignores_local_pending_failure_without_logger():
    class BrokenLocalDatabase(_LocalDatabase):
        def listar_atenciones(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("replica unavailable")

    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
    )
    rows = _HybridDatabaseProxy(BrokenLocalDatabase(), runtime).listar_atenciones()
    assert [row["nombre"] for row in rows] == ["CENTRAL"]


def test_non_temporary_central_history_error_is_not_hidden():
    def fail_connection():
        raise RuntimeError("invalid central query")

    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=fail_connection),
        operational_session=SimpleNamespace(turn_id=316),
        _temporary=lambda _error: False,
    )
    with pytest.raises(RuntimeError, match="invalid central query"):
        _HybridDatabaseProxy(_LocalDatabase(), runtime).listar_atenciones()


def test_temporary_central_failure_falls_back_without_optional_services():
    def fail_connection():
        raise ConnectionError("central unavailable")

    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=fail_connection),
        operational_session=SimpleNamespace(turn_id=316),
        _temporary=lambda _error: True,
        status_message="",
    )
    rows = _HybridDatabaseProxy(_LocalDatabase(), runtime).listar_atenciones()
    assert runtime.offline is True
    assert {row["nombre"] for row in rows} == {"LOCAL PENDING", "OLD LOCAL"}


def test_hybrid_shutdown_without_optional_refreshers_is_idempotent():
    events = []
    widget = SimpleNamespace(shutdown=lambda: events.append("widget"))
    hybrid = SimpleNamespace(shutdown=lambda: events.append("hybrid"))
    coordinator = SimpleNamespace(stop=lambda: events.append("coordinator"))

    _bind_hybrid_shutdown(widget, hybrid, coordinator, None, None)
    widget.shutdown()
    widget.shutdown()

    assert events == ["coordinator", "hybrid", "widget"]
