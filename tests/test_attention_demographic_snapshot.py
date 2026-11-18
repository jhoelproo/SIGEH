from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import Connection, server as server


@pytest.mark.parametrize("age,unit", [(32, "Años"), (0, "Meses"), (7, "Días")])
def test_document_uses_persistent_snapshot_after_event_compaction(
    tmp_path, monkeypatch, age, unit
):
    identity = "22222222-2222-4222-8222-222222222222"
    snapshot = {
        "global_attention_id": identity,
        "name": "PACIENTE DE PRUEBA",
        "age": age,
        "age_unit": unit,
        "phone": "8090000000",
        "address": "DIRECCION SINTETICA",
        "nationality": "DOMINICANA",
        "sex": "F",
        "pregnant": True,
    }
    connection = Mock()
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    connection.execute.return_value.fetchone.return_value = {
        "global_attention_id": identity,
        "source_instance_id": "source",
        "attention_id": 1,
        "latest_payload_json": snapshot,
        "document_payload": None,
    }
    render = Mock(return_value=str(tmp_path / "sheet.pdf"))
    monkeypatch.setattr(
        app,
        "load_v15_application_module",
        lambda: SimpleNamespace(
            RUTA_HOJAS={"GENERAL": "template"}, crear_pdf_temporal=render
        ),
    )
    app.AdmissionDocumentResolver(lambda: connection).build_document(
        global_attention_id=identity
    )
    data = render.call_args.args[1]
    assert data["Edad_num"] == age
    assert data["Unidad"] == unit
    assert data["Teléfono"] == snapshot["phone"]
    assert data["Dirección"] == snapshot["address"]
    assert data["Nacionalidad"] == snapshot["nationality"]
    assert connection.execute.call_count == 1
    assert data["Embarazada"] is True


def test_open_history_recovers_only_missing_fields_for_the_same_attention(
    server, tmp_path, monkeypatch
):
    from json import dumps

    selected = "22222222-2222-4222-8222-222222222222"
    other = "33333333-3333-4333-8333-333333333333"
    complete = {
        "global_attention_id": selected,
        "name": "CONTROL",
        "age": 0,
        "age_unit": "Meses",
        "sex": "Femenino",
        "phone": "8090000000",
        "address": "CALLE DE PRUEBA",
        "nationality": "DOMINICANA",
    }
    latest = {"global_attention_id": selected, "name": "CONTROL", "phone": ""}
    with Connection(server) as con:
        con.execute("""CREATE TEMP TABLE admission_attention_projection(
            global_attention_id UUID, source_instance_id TEXT,
            attention_id INT, latest_payload_json JSONB, specialty TEXT,
            is_deleted BOOLEAN DEFAULT FALSE)""")
        con.execute("""CREATE TEMP TABLE admission_sync_events(
            entity_type TEXT, entity_uuid UUID, sequence BIGINT,
            cloud_event_seq BIGINT, resulting_version INT, payload_json JSONB,
            event_uuid UUID, operation TEXT)""")
        con.execute(
            """INSERT INTO admission_attention_projection VALUES
            (%s,'ORIGIN',1,%s,'PEDIATRIA',FALSE)""",
            (selected, dumps(latest)),
        )
        for sequence, identity, event in (
            (1, selected, complete),
            (2, selected, {"address": "CALLE CORREGIDA"}),
            (3, selected, latest),
            (4, other, {"address": "OTRA FICHA", "age": 99}),
        ):
            con.execute(
                """INSERT INTO admission_sync_events
                (entity_type,entity_uuid,sequence,cloud_event_seq,resulting_version,payload_json)
                VALUES ('attention',%s,%s,%s,%s,%s)""",
                (identity, sequence, sequence, sequence, dumps(event)),
            )

        from admission_hybrid import AdmissionCloudRepository
        from contextlib import nullcontext

        def factory():
            return nullcontext(con)

        central = AdmissionCloudRepository(factory).get_attention_by_global_id(selected)
        snapshot = central["event"]["payload_json"]
        assert snapshot["address"] == "CALLE CORREGIDA"
        assert snapshot["age"] == 0

        render = Mock(return_value=str(tmp_path / "hoja.pdf"))
        monkeypatch.setattr(
            app,
            "load_v15_application_module",
            lambda: SimpleNamespace(
                RUTA_HOJAS={"PEDIATRIA": "template"},
                crear_pdf_temporal=render,
            ),
        )
        app.AdmissionDocumentResolver(factory).build_document(
            global_attention_id=selected
        )
        sheet, data = render.call_args.args
        assert sheet == "PEDIATRIA"
        assert (data["Edad_num"], data["Unidad"]) == (0, "Meses")
        assert data["Dirección"] == "CALLE CORREGIDA"
        assert data["Nacionalidad"] == "DOMINICANA"
        assert data["Teléfono"] == ""


@pytest.mark.parametrize("age, expected", [(0, "0M"), (32, "32M"), (None, "")])
def test_history_pdf_preserves_age_contact_and_nationality(age, expected, monkeypatch):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    monkeypatch.setattr(admision, "app_setting", lambda _key, default: default)
    app_window = Mock(spec=admision.App)
    app_window.db = Mock(spec=admision.DatabaseManager)
    app_window.db.obtener_atencion_por_id.return_value = {
        "hoja": "GENERAL",
        "edad_num": age,
        "unidad": "Meses",
        "telefono": "8090000000",
        "direccion": "CALLE DE PRUEBA",
        "nacionalidad": "DOMINICANA",
        "fecha": "18/11/2026",
        "hora": "12:00 PM",
        "nombre": "PACIENTE DE PRUEBA",
    }
    app_window._snapshot_a_datos.side_effect = lambda row: (
        admision.App._snapshot_a_datos(app_window, row)
    )
    path = admision.App._crear_hoja_temporal_atencion(app_window, 41)
    assert path is not None
    from pathlib import Path

    pdf = Path(path)
    try:
        text = admision.PdfReader(pdf).pages[0].extract_text()
        for value in ("809-000-0000", "CALLE DE PRUEBA", "DOMINICANA"):
            assert value in text
        if expected:
            assert expected in text
        else:
            assert "0M" not in text
    finally:
        pdf.unlink()


@pytest.mark.parametrize(
    "state", ["complete", "restored", "offline", "mismatch", "missing"]
)
def test_admission_history_restores_only_the_selected_attention(state):
    from admission_v15_adapter import _HybridDatabaseProxy

    original = {"id": 7, "global_attention_id": "same", "edad_num": None}
    complete = dict(original, edad_num=0)
    proxy = Mock()
    proxy.obtener_atencion_por_id.return_value = {
        "complete": complete,
        "missing": None,
    }.get(state, original)
    service = proxy._runtime.get_attention_by_global_id
    if state == "offline":
        service.side_effect = OSError
    else:
        service.return_value = (
            dict(complete, id=99) if state == "mismatch" else complete
        )

    assert _HybridDatabaseProxy.obtener_atencion_documental(proxy, 7) == {
        "complete": complete,
        "restored": complete,
        "missing": None,
    }.get(state, original)
    if state in ("complete", "missing"):
        service.assert_not_called()
    else:
        service.assert_called_once_with("same", force_central=True)


@pytest.mark.parametrize(
    "previous", [None, {"age": 27, "phone": "8090000000", "address": "ANTERIOR"}]
)
def test_central_projection_preserves_demographics_on_legacy_sheet_event(previous):
    from json import loads

    from admission_hybrid import AdmissionCloudRepository, SyncEvent

    identity = "22222222-2222-4222-8222-222222222222"
    source = "11111111-1111-4111-8111-111111111111"
    data = {
        "attention_id": 1,
        "patient_id": 2,
        "name": "PRUEBA",
        "detail_sheet": "GENERAL",
        "address": "",
        "source_instance_id": source,
        "service_date": "2026-11-18",
    }
    event = SyncEvent(
        "legacy-event",
        "attention",
        identity,
        "DETAIL_SHEET_GENERATED",
        data,
        "test",
        1,
        "station",
        "2026-11-18T13:00:00Z",
    )
    con = Mock()
    results = [
        Mock(
            **{
                "fetchone.return_value": (False, 1, 5, 5, previous)
                if previous
                else None
            }
        ),
        Mock(**{"fetchone.return_value": (1,)}),
        Mock(),
    ]
    con.execute.side_effect = results

    AdmissionCloudRepository._materialize_attention(con, event, 2)

    persisted = loads(con.execute.call_args_list[1].args[1][-2])
    assert persisted["address"] == ""
    if previous:
        assert persisted["age"] == 27
        assert persisted["phone"] == "8090000000"
    else:
        assert "age" not in persisted
