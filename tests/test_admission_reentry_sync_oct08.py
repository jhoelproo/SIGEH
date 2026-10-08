from contextlib import closing
import json
import sqlite3
from types import SimpleNamespace

from admission_hybrid import AdmissionSyncService, OfflineAdmissionStore
from admission_sheet_state import confirmed_turn_config
from admission_v15_adapter import load_v15_application_module
from tests.test_admission_sheet_state import state
from tests.test_admission_sync_architecture import _MemoryCloud, _session


def station(path, monkeypatch):
    v15 = load_v15_application_module()
    monkeypatch.setattr(v15, "BACKUPS_DIR", str(path.parent / "backups"))
    manager = v15.DatabaseManager(
        str(path),
        session_context=SimpleNamespace(
            audit_actor="TEST", role="auxiliar", session_id="test"
        ),
    )
    config = confirmed_turn_config(
        state(turn_started_at="2026-10-04T08:00:00", turn_code="8AM_8AM")
    )
    manager.obtener_contexto_turno(config)
    store = OfflineAdmissionStore(path)
    store.configure_runtime_context(_session(), device_id=path.stem)
    return manager, store, config


def create_visits(manager, config):
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
    original = manager.guardar_atencion(patient, "PEDIATRIA", turno_cfg=config)
    patient.update(Fecha="06/10/2026", Hora="10:00 PM")
    fresh = manager.guardar_atencion(patient, "PEDIATRIA", turno_cfg=config)
    reentry = manager.guardar_atencion(
        {
            **patient,
            "EsReingreso": True,
            "AtencionOrigenId": fresh,
            "MotivoReingreso": "Nuevo episodio clínico",
            "AutorizadoPor": "ADMIN",
        },
        "PEDIATRIA",
        turno_cfg=config,
    )
    return original, fresh, reentry


def test_distinct_visit_dates_and_reentry_sync_to_second_pc_after_restart(
    tmp_path, monkeypatch
):
    manager, store, config = station(tmp_path / "PC1.db", monkeypatch)
    visits = create_visits(manager, config)
    cloud = _MemoryCloud()
    service = AdmissionSyncService(store, cloud)
    service.push_outbox()
    service.push_outbox()
    attention_events = [
        event for event in cloud.events if event["entity_type"] == "attention"
    ]
    assert len(attention_events) == 3
    payload = attention_events[-1]["payload_json"]
    assert payload["is_reentry"] == 1
    assert (
        payload["reentry_origin_global_attention_id"]
        == manager.obtener_atencion_por_id(visits[1])["global_attention_id"]
    )
    remote, replica, _config = station(tmp_path / "PC2.db", monkeypatch)
    remote_service = AdmissionSyncService(replica, cloud)
    remote_service.synchronize_once()
    remote_service.synchronize_once()
    reopened = OfflineAdmissionStore(tmp_path / "PC2.db")
    reopened.initialize()
    with closing(sqlite3.connect(remote.db_name)) as connection:
        connection.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in connection.execute("SELECT * FROM atenciones ORDER BY id")
        ]
    assert len(rows) == 3
    assert rows[0]["dia_operativo_id"] != rows[1]["dia_operativo_id"]
    assert rows[2]["es_reingreso"] == 1
    assert rows[2]["atencion_origen_id"] == rows[1]["id"]
    assert rows[2]["motivo_reingreso"] == "Nuevo episodio clínico"
    assert rows[2]["autorizado_por"] == "ADMIN"
    assert reopened.pending_count() == 0


def test_reentry_waits_for_origin_before_advancing_cursor(tmp_path, monkeypatch):
    manager, store, config = station(tmp_path / "origin.db", monkeypatch)
    create_visits(manager, config)
    cloud = _MemoryCloud()
    AdmissionSyncService(store, cloud).push_outbox()
    events = [event for event in cloud.events if event["entity_type"] == "attention"]
    _remote, replica, _config = station(tmp_path / "replica.db", monkeypatch)
    assert replica.apply_remote_events([events[-1]]) == 0
    assert replica.last_cloud_cursor() == 0
    assert not replica.already_applied(events[-1]["event_uuid"])
    assert replica.apply_remote_events(events) == 3
    assert replica.last_cloud_cursor() == events[-1]["sequence"]
    assert replica.apply_remote_events(events) == 0
    with replica.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 3
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sync_conflicts WHERE resolved_at IS NULL"
            ).fetchone()[0]
            == 0
        )


def test_legacy_alias_cannot_merge_different_clinical_days_with_stale_central_turn(
    tmp_path, monkeypatch
):
    manager, store, config = station(tmp_path / "origin.db", monkeypatch)
    create_visits(manager, config)
    cloud = _MemoryCloud()
    AdmissionSyncService(store, cloud).push_outbox()
    events = [event for event in cloud.events if event["entity_type"] == "attention"][
        :2
    ]
    events[-1]["payload_json"].update(origin_device_id="CENTRAL-LEGACY")
    events[-1]["origin_device_id"] = "CENTRAL-LEGACY"
    _remote, replica, _config = station(tmp_path / "replica.db", monkeypatch)
    assert replica.apply_remote_events(events) == 2
    with replica.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM sync_attention_aliases"
            ).fetchone()[0]
            == 0
        )


def test_existing_database_reinstalls_create_and_update_reentry_capture(
    tmp_path, monkeypatch
):
    manager, store, config = station(tmp_path / "existing.db", monkeypatch)
    visits = create_visits(manager, config)
    with store.connection() as connection:
        connection.executescript("""
            DROP TRIGGER trg_admission_sync_attention_update;
            CREATE TRIGGER trg_admission_sync_attention_update AFTER UPDATE OF nombre ON atenciones
            BEGIN SELECT 1; END;
            DROP TRIGGER trg_admission_sync_attention_create;
            CREATE TRIGGER trg_admission_sync_attention_create AFTER INSERT ON atenciones
            BEGIN SELECT 1; END;
        """)
    upgraded = OfflineAdmissionStore(tmp_path / "existing.db")
    upgraded.initialize()
    with upgraded.connection() as connection:
        connection.execute("DELETE FROM sync_outbox")
        connection.execute(
            "UPDATE atenciones SET motivo_reingreso=? WHERE id=?",
            ("Reingreso corregido", visits[-1]),
        )
        event = json.loads(
            connection.execute(
                "SELECT payload_json FROM sync_outbox WHERE operation='UPDATE'"
            ).fetchone()[0]
        )
        assert event["is_reentry"] == 1
        assert event["reentry_reason"] == "Reingreso corregido"
        assert (
            event["reentry_origin_global_attention_id"]
            == manager.obtener_atencion_por_id(visits[1])["global_attention_id"]
        )
    fresh = manager.obtener_atencion_por_id(visits[-1])
    values = {
        "Nombre": "PACIENTE",
        "NSS": "190065476",
        "Sexo": "Masculino",
        "Edad_num": 10,
        "Unidad": "Años",
        "Cédula": "00100000001",
        "Teléfono": "8095550101",
        "Aseguradora (ARS)": "HUMANO",
        "Fecha": "06/10/2026",
        "Hora": "11:00 PM",
        "EsReingreso": True,
        "AtencionOrigenId": visits[1],
        "MotivoReingreso": "Otro episodio clínico",
        "AutorizadoPor": "ADMIN",
    }
    newest = manager.guardar_atencion(values, "PEDIATRIA", turno_cfg=config)
    with upgraded.connection() as connection:
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM sync_outbox WHERE operation='CREATE' AND entity_uuid=?",
                (manager.obtener_atencion_por_id(newest)["global_attention_id"],),
            ).fetchone()[0]
        )
        assert (
            payload["reentry_origin_global_attention_id"]
            == event["reentry_origin_global_attention_id"]
        )
        assert payload["is_reentry"] == 1
        assert fresh["id"] != newest


def test_detail_sheet_and_missing_outbox_recovery_preserve_reentry(
    tmp_path, monkeypatch
):
    manager, store, config = station(tmp_path / "origin.db", monkeypatch)
    visits = create_visits(manager, config)
    cloud = _MemoryCloud()
    AdmissionSyncService(store, cloud).push_outbox()
    assert store.queue_detail_sheet_generated(visits[-1])
    with store.connection() as connection:
        detail = json.loads(
            connection.execute(
                "SELECT payload_json FROM sync_outbox WHERE sync_status='PENDING'"
            ).fetchone()[0]
        )
        assert detail["is_reentry"] == 1
        connection.execute("DELETE FROM sync_outbox")
    global_id = manager.obtener_atencion_por_id(visits[-1])["global_attention_id"]
    assert store.queue_missing_attention_events(global_attention_id=global_id) == 1
    assert store.queue_missing_attention_events(global_attention_id=global_id) == 0
    with store.connection() as connection:
        recovered = json.loads(
            connection.execute("SELECT payload_json FROM sync_outbox").fetchone()[0]
        )
    for field in (
        "is_reentry",
        "reentry_origin_global_attention_id",
        "reentry_reason",
        "reentry_authorized_by",
    ):
        assert recovered[field] == detail[field]
