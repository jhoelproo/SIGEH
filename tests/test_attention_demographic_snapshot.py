from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app


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
    assert data["Embarazada"] is True
