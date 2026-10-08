from contextlib import closing
from datetime import date, datetime
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_sheet_state import confirmed_turn_config
from admission_v15_adapter import _HybridDatabaseProxy, load_v15_application_module
from tests.test_admission_sheet_state import state
from tests.test_admission_v15_unified_history import (
    _CapturingCloudConnection,
    _LocalDatabase,
)


@pytest.fixture
def v15():
    return load_v15_application_module()


def test_duplicate_lookup_uses_confirmed_snapshot_not_old_local_json(v15, monkeypatch):
    current = confirmed_turn_config(state(turn_started_at="2026-10-06T08:00:00"))
    old = {"fecha_base": date(2026, 10, 4), "turno_codigo": "8AM_8AM"}
    lookup = Mock(return_value=None)
    window = SimpleNamespace(
        _generation_turn_config=lambda: current,
        db=SimpleNamespace(
            obtener_contexto_turno=lambda config: {
                "turno_id": 2,
                "dia_operativo_id": 2,
            },
            buscar_atencion_en_turno=lookup,
        ),
    )
    monkeypatch.setattr(v15, "cargar_turno_config", lambda: old)
    v15.App._buscar_duplicado_turno_actual(
        window,
        {
            "NSS": "190065476",
            "Nombre": "PACIENTE",
            "Fecha": "06/10/2026",
            "Hora": "10:00 PM",
        },
    )
    assert lookup.call_args.args[2:] == (
        datetime(2026, 10, 6, 8),
        datetime(2026, 10, 7, 8),
    )


def _lookup_database(v15, path):
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript("""
            CREATE TABLE pacientes(id INTEGER PRIMARY KEY,nombre TEXT,telefono_clean TEXT,
              telefono TEXT,estado TEXT,updated_at TEXT,created_at TEXT);
            CREATE TABLE paciente_identificadores(paciente_id INTEGER,tipo TEXT,
              valor_normalizado TEXT,activo INTEGER,conflicto INTEGER);
            CREATE TABLE turnos(id INTEGER PRIMARY KEY,dia_operativo_id INTEGER);
            CREATE TABLE atenciones(id INTEGER PRIMARY KEY,paciente_id INTEGER,
              dia_operativo_id INTEGER,turno_id INTEGER,estado TEXT,nombre TEXT,
              telefono_clean TEXT,fecha TEXT,hora TEXT,created_at TEXT,
              hoja TEXT,nss TEXT,cedula TEXT,ars TEXT,edad_num INTEGER,
              unidad TEXT,tipo_atencion TEXT);
            INSERT INTO pacientes VALUES(1,'PACIENTE','','','ACTIVO','','');
            INSERT INTO paciente_identificadores VALUES(1,'NSS','190065476',1,0);
            INSERT INTO turnos VALUES(1,1);
            INSERT INTO atenciones(id,paciente_id,dia_operativo_id,turno_id,estado,nombre,
              fecha,hora,created_at) VALUES(342,1,1,1,'ACTIVA','PACIENTE',
              '04/10/2026','10:27 PM','2026-10-04 22:27:00');
        """)
    database = v15.DatabaseManager.__new__(v15.DatabaseManager)
    database._connect = lambda **_kwargs: sqlite3.connect(path)
    return database


def test_old_attention_cannot_be_duplicate_even_with_stale_day_and_turn(v15, tmp_path):
    database = _lookup_database(v15, tmp_path / "lookup.db")
    assert (
        database.buscar_atencion_en_turno(
            "190065476",
            "",
            datetime(2026, 10, 6, 8),
            datetime(2026, 10, 7, 8),
            turno_id=1,
            dia_operativo_id=1,
            nombre="PACIENTE",
        )
        is None
    )


@pytest.mark.parametrize("value", ["07/10/2026", "07-10-2026", "2026-10-07"])
def test_date_filter_uses_service_date_even_when_day_and_turn_are_stale(
    v15, tmp_path, value
):
    database = _lookup_database(v15, tmp_path / "history.db")
    with closing(database._connect()) as connection:
        connection.execute("UPDATE atenciones SET fecha='07/10/2026'")
        connection.execute("""INSERT INTO atenciones(id,paciente_id,dia_operativo_id,turno_id,
            estado,nombre,fecha,hora) VALUES(343,2,1,1,'ACTIVA','OTRO','06/10/2026','12:00')""")
        connection.commit()
    assert [
        row["id"]
        for row in database.listar_atenciones_filtradas(
            modo="Por fecha", fecha_txt=value
        )
    ] == [342]


@pytest.mark.parametrize("value", ["07/10/2026", "07-10-2026", "2026-10-07"])
def test_central_date_filter_accepts_calendar_and_iso_dates(value):
    cloud = _CapturingCloudConnection()
    database = _LocalDatabase()
    database.connection.execute("DELETE FROM sync_outbox")
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: cloud),
        operational_session=None,
    )
    _HybridDatabaseProxy(database, runtime).listar_atenciones_filtradas(
        modo="Por fecha", fecha_txt=value
    )
    assert "2026-10-07" in str(cloud.params)
    assert "p.turn_id=%s" not in cloud.query


def test_current_turn_cloud_query_and_result_are_newest_first():
    cloud = _CapturingCloudConnection()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=lambda: cloud),
        operational_session=SimpleNamespace(
            turn_id=316, operational_source_id="central-source"
        ),
    )
    proxy = _HybridDatabaseProxy(_LocalDatabase(), runtime)
    rows = proxy.listar_atenciones_filtradas(modo="Este turno")
    assert "COALESCE(p.device_local_sequence,0) DESC" in cloud.query
    assert [row["id"] for row in rows] == [200]


@pytest.mark.parametrize(
    "action", ["reimprimir", "abrir", "editar", "cancelar", "reingreso"]
)
def test_duplicate_actions_dispatch_and_reentry_keeps_original(
    v15, monkeypatch, action
):
    window = SimpleNamespace(
        root=object(),
        app_settings={"print_copies_hoja": 2},
        _dialogo_atencion_existente=lambda _row: action,
        _abrir_editor_atencion=Mock(),
        _abrir_hoja_temporal_atencion=Mock(),
        _imprimir_hoja_temporal_atencion=Mock(return_value=True),
        _solicitar_autorizacion_admin=Mock(return_value="ADMIN"),
        set_status=Mock(),
    )
    monkeypatch.setattr(
        v15.simpledialog, "askstring", lambda *_a, **_k: "Reingreso por nuevo episodio"
    )
    result = v15.App._atender_duplicado_turno(window, {"id": 342})
    if action == "reingreso":
        assert result["EsReingreso"] and result["AtencionOrigenId"] == 342
        assert result["AutorizadoPor"] == "ADMIN"
    else:
        assert result is None
    assert window._imprimir_hoja_temporal_atencion.call_count == (
        action == "reimprimir"
    )
    assert window._abrir_hoja_temporal_atencion.call_count == (action == "abrir")
    assert window._abrir_editor_atencion.call_count == (action == "editar")


def test_stale_confirmed_turn_saves_new_day_and_valid_reentry_without_overwriting(
    v15, tmp_path, monkeypatch
):
    monkeypatch.setattr(v15, "BACKUPS_DIR", str(tmp_path / "backups"))
    database_path = tmp_path / "clinical.db"
    context = SimpleNamespace(audit_actor="TEST", role="auxiliar", session_id="test")
    database = v15.DatabaseManager(str(database_path), session_context=context)
    config = confirmed_turn_config(
        state(turn_started_at="2026-10-04T08:00:00", turn_code="8AM_8AM")
    )
    patient = {
        "Nombre": "PACIENTE",
        "NSS": "190065476",
        "Sexo": "Masculino",
        "Edad_num": 10,
        "Unidad": "Años",
        "Cédula": "00100000001",
        "Teléfono": "8095550101",
        "Aseguradora (ARS)": "HUMANO",
        "Fecha": "04/10/2026",
        "Hora": "10:27 PM",
    }
    original = database.guardar_atencion(patient, "PEDIATRIA", turno_cfg=config)
    patient.update(Fecha="06/10/2026", Hora="10:00 PM")
    fresh = database.guardar_atencion(patient, "PEDIATRIA", turno_cfg=config)
    reopened = v15.DatabaseManager(str(database_path), session_context=context)
    first_row = reopened.obtener_atencion_por_id(original)
    second_row = reopened.obtener_atencion_por_id(fresh)
    assert first_row["fecha"] == "04/10/2026"
    assert first_row["turno_id"] == second_row["turno_id"]
    assert first_row["dia_operativo_id"] != second_row["dia_operativo_id"]
    with pytest.raises(sqlite3.IntegrityError, match="día operativo"):
        reopened.guardar_atencion(patient, "PEDIATRIA", turno_cfg=config)
    reentry_data = {
        **patient,
        "EsReingreso": True,
        "AtencionOrigenId": fresh,
        "MotivoReingreso": "Nuevo episodio clínico",
        "AutorizadoPor": "ADMIN",
    }
    reentry_id = reopened.guardar_atencion(reentry_data, "PEDIATRIA", turno_cfg=config)
    assert reopened.obtener_atencion_por_id(reentry_id)["atencion_origen_id"] == fresh
    with pytest.raises(ValueError, match="no está activa en este día"):
        reopened.guardar_atencion(
            {**reentry_data, "AtencionOrigenId": original},
            "PEDIATRIA",
            turno_cfg=config,
        )
    with closing(reopened._connect()) as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 3
        assert (
            connection.execute("SELECT COUNT(*) FROM dias_operativos").fetchone()[0]
            == 2
        )


@pytest.mark.parametrize(
    "action,actor,reason",
    [
        ("reingreso", None, "motivo válido"),
        ("reingreso", "ADMIN", None),
        ("reingreso", "ADMIN", "corto"),
    ],
)
def test_reentry_cancel_and_short_reason_never_create_data(
    v15, monkeypatch, action, actor, reason
):
    window = SimpleNamespace(
        root=object(),
        _dialogo_atencion_existente=lambda _row: action,
        _solicitar_autorizacion_admin=lambda *_a, **_k: actor,
    )
    warning = Mock()
    monkeypatch.setattr(v15.simpledialog, "askstring", lambda *_a, **_k: reason)
    monkeypatch.setattr(v15.messagebox, "showwarning", warning)
    assert v15.App._atender_duplicado_turno(window, {"id": 342}) is None
    assert warning.call_count == bool(actor)


def test_print_failure_reports_error_and_does_not_modify_attention(v15):
    status = Mock()
    window = SimpleNamespace(
        root=object(),
        app_settings={"print_copies_hoja": 0},
        _dialogo_atencion_existente=lambda _row: "reimprimir",
        _imprimir_hoja_temporal_atencion=Mock(return_value=False),
        set_status=status,
    )
    assert v15.App._atender_duplicado_turno(window, {"id": 342}) is None
    assert status.call_args.args[-1] == "error"


@pytest.mark.parametrize(
    "label,action",
    [
        ("Reimprimir", "reimprimir"),
        ("Abrir hoja", "abrir"),
        ("Registrar reingreso", "reingreso"),
        ("Cancelar", "cancelar"),
    ],
)
def test_existing_attention_dialog_buttons_return_selected_action(v15, label, action):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPushButton

    root = v15.tb.Window()
    window = SimpleNamespace(root=root)

    def choose():
        for top in QApplication.topLevelWidgets():
            if top.isVisible() and top.windowTitle() == "Atención ya registrada":
                for button in top.findChildren(QPushButton):
                    if button.text() == label:
                        button.click()
                        return

    QTimer.singleShot(30, choose)
    watchdog = QTimer(root)
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(
        lambda: [
            top.reject()
            for top in QApplication.topLevelWidgets()
            if top.windowTitle() == "Atención ya registrada" and top.isVisible()
        ]
    )
    watchdog.start(2000)
    result = v15.App._dialogo_atencion_existente(
        window,
        {"id": 342, "nombre": "PACIENTE", "fecha": "06/10/2026", "hora": "10:00 PM"},
    )
    watchdog.stop()
    assert result == action
    root.destroy()


@pytest.mark.parametrize(
    "clock,expected",
    [
        ("07:59", False),
        ("08:00", True),
        ("07:59 PM", True),
        ("08:00 PM", False),
        ("invalid", False),
    ],
)
def test_duplicate_database_query_obeys_actual_service_interval(
    v15, tmp_path, clock, expected
):
    database = _lookup_database(v15, tmp_path / "interval.db")
    with closing(database._connect()) as connection:
        connection.execute("UPDATE atenciones SET fecha='06/10/2026',hora=?", (clock,))
        connection.commit()
    result = database.buscar_atencion_en_turno(
        "190065476",
        "",
        datetime(2026, 10, 6, 8),
        datetime(2026, 10, 6, 20),
        turno_id=1,
        dia_operativo_id=1,
        nombre="PACIENTE",
    )
    assert bool(result) is expected


def test_stale_central_and_local_identity_use_new_registration_day(v15, monkeypatch):
    old = confirmed_turn_config(
        state(turn_started_at="2026-10-04T08:00:00", turn_code="8AM_8AM")
    )
    lookup = Mock(return_value=None)
    window = SimpleNamespace(
        _generation_turn_config=lambda: old,
        db=SimpleNamespace(
            obtener_contexto_turno=lambda _config: {
                "turno_id": 1,
                "dia_operativo_id": 1,
            },
            buscar_atencion_en_turno=lookup,
        ),
    )
    monkeypatch.setattr(v15, "cargar_turno_config", lambda: old)
    v15.App._buscar_duplicado_turno_actual(
        window, {"NSS": "190065476", "Fecha": "06/10/2026", "Hora": "10:00 PM"}
    )
    assert lookup.call_args.args[2:] == (
        datetime(2026, 10, 6, 8),
        datetime(2026, 10, 7, 8),
    )


@pytest.mark.parametrize("value", ["", "invalid", "31/02/2026"])
def test_invalid_calendar_filter_never_displays_all_records(v15, tmp_path, value):
    database = _lookup_database(v15, tmp_path / "invalid.db")
    with pytest.raises(ValueError, match="fecha válida"):
        database.listar_atenciones_filtradas(modo="Por fecha", fecha_txt=value)
