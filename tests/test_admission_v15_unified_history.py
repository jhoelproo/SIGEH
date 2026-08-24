import sqlite3
import logging
import sys
from datetime import date, datetime
from types import ModuleType
from types import SimpleNamespace

from admission_v15_adapter import (
    _HybridAdmissionRuntime,
    _HybridDatabaseProxy,
    _V15BackgroundRefreshCoordinator,
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


def test_online_v15_history_reads_the_local_synchronized_replica():
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

    assert {row["id"] for row in rows} == {99, 100}


def test_excel_dataset_reads_the_same_local_current_turn_view():
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

    assert [row["id"] for row in rows] == [100]
    assert {row["global_attention_id"] for row in rows} == {"pending-uuid"}


def test_turn_summary_uses_the_same_local_current_turn_dataset_as_history():
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

    assert summary["total"] == 1
    assert summary["GENERAL"] == 1
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


def test_history_does_not_query_postgresql_when_the_replica_is_available():
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

    assert [row["nombre"] for row in rows] == ["OLD LOCAL", "LOCAL PENDING"]


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

    assert connection.query == ""


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


def test_primary_representative_turn_path_does_not_use_user_transition_guard():
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

        def require_primary_turn_change(self):
            self.turn_guard_calls += 1

        def require_primary_transition(self):
            self.transition_guard_calls += 1
            raise AssertionError("normal turn must not use user-transition guard")

        def change_primary_turn(self, turn_id, *, shift_metadata):
            self.changed.append((turn_id, dict(shift_metadata)))
            return SimpleNamespace(committed=True)

    runtime = _Runtime()
    proxy = _HybridDatabaseProxy(_TurnDatabase(), runtime)

    assert proxy.obtener_o_crear_turno({"turno_codigo": "8AM_8PM"}) == 77
    assert runtime.turn_guard_calls == 1
    assert runtime.transition_guard_calls == 0
    assert runtime.changed == [(77, {"turno_codigo": "8AM_8PM"})]


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
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: _CloudConnection()),
        operational_session=SimpleNamespace(turn_id=316),
        logger=logging.getLogger("test.turn-summary"),
    )
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)
    object.__setattr__(
        proxy,
        "build_current_admission_list_dataset",
        lambda: [
            {"tipo_atencion": "EMERGENCIA", "hoja_normalizada": "GENERAL", "ars_display": "SIN SEGURO"},
            {"tipo_atencion": "EMERGENCIA", "hoja_normalizada": "PEDIATRIA", "ars_display": "HUMANO"},
            {"tipo_atencion": "EMERGENCIA", "hoja_normalizada": "GINECOLOGIA", "ars_display": ""},
            {"tipo_atencion": "URGENCIA", "hoja_normalizada": "GENERAL", "ars_display": "HUMANO"},
            {"tipo_atencion": "CONSULTA", "hoja_normalizada": "GENERAL", "ars_display": "HUMANO"},
        ],
    )

    summary = proxy.refresh_turn_summary()

    assert summary["total"] == 3
    assert summary["GENERAL"] == 1
    assert summary["PEDIATRIA"] == 1
    assert summary["GINECOLOGIA"] == 1
    assert summary["URGENCIAS"] == 1
    assert summary["CONSULTAS"] == 1
    assert summary["sin_seguro"] == 2
