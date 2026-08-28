from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from admission_bridge import AdmissionAttention
from admission_v15_adapter import (
    DEFAULT_V15_ROOT,
    AdmissionV15EventAdapter,
    AdmissionV15EventBus,
    AdmissionV15Factory,
    EmbeddedMainAppGateway,
    _load_v15_modules,
)
from ADMISION_PYSIDE6_V15.qt_compat import tb, ttk


def _attention(*, name="PACIENTE CONTROLADO", status="ACTIVA", sheet=False):
    return AdmissionAttention(
        attention_id=41,
        patient_id=7,
        name=name,
        service_date="2026-08-08",
        service_time="08:10",
        nss="001-002",
        nss_clean="001002",
        cedula="001-0000001-1",
        cedula_clean="00100000011",
        ars="HUMANO",
        attention_type="EMERGENCIA",
        source_updated_at="2026-08-08 08:10:00",
        uninsured=False,
        turn_id=12,
        source_instance_id="source-controlled",
        source_schema_version=7,
        coverage_status="ASEGURADO_VERIFICADO",
        canonical_ars="HUMANO",
        billing_readiness="LISTO_PARA_FACTURAR",
        source_status=status,
        processing_turn_id=12,
        has_detail_sheet=sheet,
    )


@pytest.mark.parametrize(
    "module_name",
    (
        "admission_source.facturacion_tabs",
        "ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6",
    ),
)
def test_rectification_rejects_an_ambiguous_short_reason(module_name):
    module = __import__(module_name, fromlist=["DatabaseManager"])

    with pytest.raises(ValueError, match="al menos 5 caracteres"):
        module.DatabaseManager.actualizar_atencion_especifica(
            None,
            1,
            {},
            motivo="no",
        )


class _Repository:
    def __init__(self):
        self.current = _attention()
        self.reads = []

    def get_attention_by_identity(self, source_instance_id, attention_id):
        self.reads.append((source_instance_id, attention_id))
        if (source_instance_id, attention_id) != ("source-controlled", 41):
            return None
        return self.current


def test_v15_events_requery_source_and_upsert_by_composite_identity():
    bus = AdmissionV15EventBus()
    repository = _Repository()
    central_projection = {}
    refreshes = []

    def sync(attentions):
        for attention in attentions:
            key = (attention.source_instance_id, attention.attention_id)
            central_projection[key] = attention.snapshot()
        return len(attentions)

    adapter = AdmissionV15EventAdapter(
        bus,
        repository=repository,
        projection_sync=sync,
    )
    adapter.projection_changed.connect(refreshes.append)
    key = ("source-controlled", 41)

    created = {
        "source_instance_id": key[0],
        "attention_id": key[1],
        "event_uuid": "created-1",
    }
    bus.attention_created.emit(created)
    bus.attention_created.emit(created)
    assert central_projection[key]["name"] == "PACIENTE CONTROLADO"
    assert len(repository.reads) == 1
    assert len(refreshes) == 1

    repository.current = replace(repository.current, name="PACIENTE EDITADO")
    bus.attention_updated.emit({**created, "event_uuid": "updated-1"})
    assert central_projection[key]["name"] == "PACIENTE EDITADO"
    assert len(central_projection) == 1

    repository.current = replace(repository.current, source_status="ANULADA")
    bus.attention_cancelled.emit({**created, "event_uuid": "cancelled-1"})
    assert central_projection[key]["source_status"] == "ANULADA"

    repository.current = replace(repository.current, has_detail_sheet=True)
    bus.detail_sheet_generated.emit({**created, "event_uuid": ""})
    assert central_projection[key]["has_detail_sheet"] is True
    assert [item["event_type"] for item in refreshes] == [
        "attention_created",
        "attention_updated",
        "attention_cancelled",
        "detail_sheet_generated",
    ]


def test_shift_change_emits_reference_without_patient_payload_or_restart():
    bus = AdmissionV15EventBus()
    repository = _Repository()
    shifts = []
    adapter = AdmissionV15EventAdapter(
        bus,
        repository=repository,
        projection_sync=lambda _rows: 0,
    )
    adapter.shift_changed.connect(shifts.append)

    bus.shift_changed.emit(
        {
            "source_instance_id": "source-controlled",
            "turn_id": 13,
            "operational_day_id": 4,
            "shift_type": "8AM_8AM",
            "representative": "OPERADOR CONTROLADO",
        }
    )
    bus.shift_changed.emit(
        {
            "source_instance_id": "source-controlled",
            "turn_id": 14,
            "operational_day_id": 5,
            "shift_type": "8AM_8PM",
            "representative": "OPERADOR CONTROLADO",
        }
    )

    assert [shift.turn_id for shift in shifts] == [13, 14]
    assert repository.reads == []
    assert adapter.event_bus is bus


def test_v15_real_persistence_emits_post_commit_events_into_controlled_projection(
    tmp_path, monkeypatch
):
    if not Path(DEFAULT_V15_ROOT).is_dir():
        pytest.skip("La fuente V15 externa certificada no está disponible en este equipo.")
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    modules = _load_v15_modules(Path(DEFAULT_V15_ROOT))
    application_module = __import__(
        "ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6",
        fromlist=["DatabaseManager"],
    )
    now = datetime.now().replace(microsecond=0)
    monkeypatch.setattr(
        application_module,
        "turno_config_es_vigente",
        lambda _config, momento=None: True,
    )
    monkeypatch.setattr(
        application_module,
        "obtener_rango_turno_efectivo",
        lambda _config: (now, now + timedelta(days=1)),
    )

    bus = AdmissionV15EventBus()
    session = SimpleNamespace(
        audit_actor="operador.controlado",
        role="auxiliar",
        session_id="session-controlled",
    )
    manager = modules.database_class(session_context=session, event_bus=bus)
    repository = __import__("admission_bridge").AdmissionReadOnlyRepository(
        manager.db_name
    )
    central_projection = {}
    refreshed = []

    def sync(attentions):
        for attention in attentions:
            central_projection[
                (attention.source_instance_id, attention.attention_id)
            ] = attention.snapshot()
        return len(attentions)

    adapter = AdmissionV15EventAdapter(
        bus,
        repository=repository,
        projection_sync=sync,
    )
    adapter.projection_changed.connect(refreshed.append)
    shifts = []
    adapter.shift_changed.connect(shifts.append)

    shift = {
        "fecha_base": date.today(),
        "turno_codigo": "8AM_8AM",
        "representante": "OPERADOR CONTROLADO",
        "inicio_real_dt": now,
    }
    data = {
        "Nombre": "PACIENTE CONTRATO V15",
        "Sexo": "Femenino",
        "Edad_num": 30,
        "Unidad": "Años",
        "Cédula": "00100000011",
        "Teléfono": "8095550101",
        "Dirección": "DATO CONTROLADO",
        "Nacionalidad": "DOMINICANA",
        "Aseguradora (ARS)": "HUMANO",
        "NSS": "001002003",
        "Fecha": date.today().strftime("%d/%m/%Y"),
        "Hora": now.strftime("%H:%M"),
        "TipoAtencion": "EMERGENCIA",
    }

    attention_id = manager.guardar_atencion(data, "GENERAL", turno_cfg=shift)
    source_id = next(iter(central_projection))[0]
    key = (source_id, attention_id)
    assert central_projection[key]["name"] == "PACIENTE CONTRATO V15"

    edited = dict(data)
    edited["Nombre"] = "PACIENTE CONTRATO EDITADO"
    edited["Hoja"] = "GENERAL"
    manager.actualizar_atencion_especifica(
        attention_id,
        edited,
        usuario="operador.controlado",
    )
    assert central_projection[key]["name"] == "PACIENTE CONTRATO EDITADO"

    manager.notify_detail_sheet_generated(attention_id)
    assert central_projection[key]["has_detail_sheet"] is True

    turn_id = manager.obtener_o_crear_turno(shift)
    manager.notify_shift_changed(turn_id)
    assert shifts[-1].turn_id == turn_id

    assert manager.borrar_atencion(
        attention_id,
        motivo="Anulación de prueba controlada",
        usuario="operador.controlado",
    )
    assert central_projection[key]["source_status"] == "ANULADA"
    assert len(central_projection) == 1
    assert [event["event_type"] for event in refreshed] == [
        "attention_created",
        "attention_updated",
        "detail_sheet_generated",
        "attention_cancelled",
    ]


def test_embedded_v15_receives_the_host_event_bus_without_second_session(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    qt_app = QApplication.instance() or QApplication([])
    host = SimpleNamespace(
        connection_factory=lambda: None,
        user={
            "id": 1,
            "username": "controlado",
            "full_name": "Usuario Controlado",
            "role": "auxiliar",
        },
        session_id="session-controlled",
        device_id="device-controlled",
        device_name="equipo-controlado",
        current_shift={},
        configuration={},
        logger=None,
        event_bus=AdmissionV15EventBus(),
    )
    factory = AdmissionV15Factory(
        host,
        session_checker=lambda: True,
        users_provider=lambda: [
            {
                "is_active": 1,
                "username": "controlado",
                "full_name": "Usuario Controlado",
                "role": "auxiliar",
            }
        ],
        credential_verifier=lambda _username, _password: None,
    )

    widget = factory.create_widget()
    try:
        assert factory.context.event_bus is host.event_bus
        if hasattr(widget, "admission"):
            assert widget.admission.db.event_bus is host.event_bus
        else:
            assert widget.controller.service.context.event_bus is host.event_bus
        assert widget.context.session_id == host.session_id
        assert widget.context.device_id == host.device_id
        assert QApplication.instance() is qt_app
    finally:
        if hasattr(widget, "shutdown"):
            widget.shutdown()
            widget.shutdown()
        else:
            widget.close()
        widget.deleteLater()
        qt_app.processEvents()


def test_main_shell_uses_v15_without_legacy_admission_imports():
    source = (Path(__file__).parents[1] / "CALCULOS_QT.py").read_text(
        encoding="utf-8"
    )
    assert "from integrated_admission import" not in source
    assert "from admission_pyside6 import" not in source
    assert "self._source_locator" not in source
    assert "AdmissionV15EventBus(self)" in source


def test_representatives_are_cached_and_do_not_depend_on_login_sessions():
    rows = [
        {
            "id": 1,
            "username": "admin",
            "full_name": "Administrador",
            "role": "administrador",
            "is_active": 1,
        },
        {
            "id": 2,
            "username": "aux",
            "full_name": "Auxiliar",
            "role": "auxiliar",
            "is_active": 1,
        },
        {
            "id": 3,
            "username": "audit",
            "full_name": "Auditor Activo",
            "role": "facturador de auditoria",
            "is_active": 1,
        },
        {
            "id": 4,
            "username": "inactive",
            "full_name": "Inactivo",
            "role": "auxiliar",
            "is_active": 0,
        },
    ]
    gateway = EmbeddedMainAppGateway(
        current_user=rows[0],
        session_checker=lambda: False,
        users_provider=lambda: rows,
        credential_verifier=lambda _username, _password: None,
        audit_callback=None,
        gateway_error_class=RuntimeError,
        representative_class=lambda username, full_name, role: SimpleNamespace(
            username=username, full_name=full_name, role=role
        ),
    )

    representatives = gateway.list_representatives()

    assert [item.username for item in representatives] == ["admin", "audit", "aux"]
    assert [item.username for item in gateway.cached_representatives()] == [
        "admin", "audit", "aux",
    ]


def test_administrative_turn_override_allows_pdf_attention_persistence(tmp_path, monkeypatch):
    """A manually corrected canonical shift remains usable outside its nominal time."""
    if not Path(DEFAULT_V15_ROOT).is_dir():
        pytest.skip("La fuente V15 externa certificada no está disponible en este equipo.")
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    modules = _load_v15_modules(Path(DEFAULT_V15_ROOT))
    application_module = __import__(
        "ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6",
        fromlist=["DatabaseManager"],
    )
    monkeypatch.setattr(application_module, "TURNOS_CFG", tmp_path / "turno_actual.json")
    manager = modules.database_class(
        session_context=SimpleNamespace(
            audit_actor="administrador.controlado",
            role="administrador",
            session_id="session-admin-override",
        )
    )
    stale_shift = {
        "fecha_base": date.today() - timedelta(days=2),
        "turno_codigo": "8AM_8AM",
        "representante": "AUX TEST",
        "inicio_real_dt": (datetime.now() - timedelta(days=2)).replace(microsecond=0),
        "administrative_override": True,
        "override_reason": "Corrección administrativa de prueba",
    }
    assert application_module.guardar_turno_config(
        stale_shift["representante"],
        stale_shift["turno_codigo"],
        stale_shift["fecha_base"],
        inicio_real=stale_shift["inicio_real_dt"],
        administrative_override=True,
        override_reason=stale_shift["override_reason"],
    )
    persisted_shift = application_module.cargar_turno_config()
    assert persisted_shift and persisted_shift["administrative_override"] is True
    data = {
        "Nombre": "PACIENTE TURNO ADMINISTRATIVO",
        "Sexo": "Femenino",
        "Edad_num": 30,
        "Unidad": "Años",
        "Cédula": "00100000012",
        "Teléfono": "8095550102",
        "Dirección": "DATO CONTROLADO",
        "Nacionalidad": "DOMINICANA",
        "Aseguradora (ARS)": "HUMANO",
        "NSS": "001002004",
        "Fecha": date.today().strftime("%d/%m/%Y"),
        "Hora": "07:05",
        "TipoAtencion": "EMERGENCIA",
    }

    attention_id = manager.guardar_atencion(data, "GENERAL", turno_cfg=persisted_shift)

    assert attention_id > 0

    uncorrected_stale_shift = dict(persisted_shift, administrative_override=False)
    with pytest.raises(application_module.TurnoNoVigenteError):
        manager.guardar_atencion(data, "GENERAL", turno_cfg=uncorrected_stale_shift)


def test_qt_notebook_emits_the_v15_tab_changed_contract():
    qt_app = QApplication.instance() or QApplication([])
    notebook = ttk.Notebook()
    first = tb.Frame(notebook)
    second = tb.Frame(notebook)
    changes = []
    notebook.bind("<<NotebookTabChanged>>", lambda _event: changes.append(True))
    notebook.add(first, text="Primera")
    notebook.add(second, text="Representante del turno")

    notebook.select(second)

    assert notebook.tab(notebook.select(), "text") == "Representante del turno"
    assert changes
    notebook.deleteLater()
    qt_app.processEvents()
