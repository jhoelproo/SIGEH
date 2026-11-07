from dataclasses import replace
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_report_tools import (
    comparison_rows,
    followup_rows,
    load_billing_followup,
    normalize_view,
)
from admission_statistical_reports import build_admission_report_dataset
from tests.test_admission_statistical_reports_v106 import _filters, _row


def test_adapter_report_queries_use_the_existing_connection_and_event_bus(monkeypatch):
    from admission_v15_adapter import _HybridDatabaseProxy

    connection = object()
    factory = Mock(side_effect=lambda: nullcontext(connection))
    requested = Mock()
    runtime = SimpleNamespace(
        offline=False,
        host=SimpleNamespace(
            connection_factory=factory,
            event_bus=SimpleNamespace(billing_requested=requested),
        ),
    )
    proxy = _HybridDatabaseProxy(object(), runtime)
    adjacent, followup = Mock(return_value={"turn_id": 2}), Mock(return_value=[])
    monkeypatch.setattr("admission_turn_history.adjacent_turn", adjacent)
    monkeypatch.setattr("admission_report_tools.load_billing_followup", followup)
    assert proxy.adjacent_report_turn({"turn_id": 3}, "previous") == {"turn_id": 2}
    assert proxy.report_billing_followup([]) == []
    adjacent.assert_called_once_with(connection, {"turn_id": 3}, "previous")
    followup.assert_called_once_with(connection, [])
    proxy.open_report_receipt("9")
    requested.emit.assert_called_once_with({"receipt_id": 9})
    runtime.offline = True
    with pytest.raises(RuntimeError, match="Conecte"):
        proxy.adjacent_report_turn({}, "next")
    with pytest.raises(RuntimeError, match="Conecte"):
        proxy.report_billing_followup([])
    assert factory.call_count == 2


def test_comparison_applies_equivalent_filters_and_zero_baselines():
    current = build_admission_report_dataset([_row(1), _row(2, ars="APS")], _filters())
    other = build_admission_report_dataset([_row(3)], replace(_filters(), turn_id=316))
    rows = comparison_rows(current, other)
    assert ("Total pacientes", 2, 1, 1, "100.00%") in rows
    assert ("ARS · APS", 1, 0, 1, "Sin base") in rows
    assert ("ARS · HUMANO", 1, 1, 0, "0.00%") in rows
    assert comparison_rows(other, current)[0][3:] == (-1, "-50.00%")


def test_views_store_filter_choices_without_fixed_shift_or_patient_data():
    view = normalize_view(
        {
            "name": "  Pediatría  ",
            "period": "Rango",
            "specialty": "PEDIATRIA",
            "coverage": "TODAS",
            "ars_mode": "INCLUIR",
            "selected_ars": ["APS", "APS", "HUMANO"],
            "turn_id": 123,
            "patient": "sensitive",
        }
    )
    assert view["name"] == "Pediatría"
    assert view["selected_ars"] == ["APS", "HUMANO"]
    assert "turn_id" not in view and "patient" not in view


@pytest.mark.parametrize(
    "values",
    [
        {},
        {"name": " "},
        {"name": "x", "period": "bad"},
        {"name": "x", "ars_mode": "INCLUIR", "selected_ars": []},
        {"name": "x", "coverage": "bad"},
        {"name": "x", "specialty": "bad"},
    ],
)
def test_invalid_views_fail_explicitly(values):
    with pytest.raises(ValueError):
        normalize_view(values)


def test_followup_uses_receipt_states_and_keeps_one_patient_per_attention():
    records = [_row(1), _row(2), _row(3)]
    evidence = [
        dict(
            global_attention_id=records[0]["global_attention_id"],
            readiness="LISTA",
            receipt_id=1,
            numero=10,
            estado_facturacion="PENDIENTE",
            dx="CEFALEA",
            numero_autorizacion="0012",
        ),
        dict(
            global_attention_id=records[0]["global_attention_id"],
            readiness="LISTA",
            receipt_id=2,
            numero=11,
            estado_facturacion="FACTURADO",
            dx="CEFALEA",
            numero_autorizacion="0012",
        ),
        dict(
            global_attention_id=records[1]["global_attention_id"],
            readiness="INCOMPLETA",
            readiness_reasons='["Revisar cobertura"]',
            receipt_id=None,
        ),
    ]
    rows = followup_rows(records, evidence)
    assert len(rows) == 3
    assert rows[0]["billing_state"] == "Facturado" and len(rows[0]["receipts"]) == 2
    assert rows[1]["billing_state"] == "Sin recibo"
    assert rows[1]["issues"] == ("Revisar cobertura",)
    assert rows[2]["billing_state"] == "Sin confirmar"


def test_followup_empty_does_not_access_database_or_invent_status():
    connection = Mock()
    assert load_billing_followup(connection, []) == []
    connection.execute.assert_not_called()
    assert followup_rows([], []) == []


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("PENDIENTE", "Recibo pendiente"),
        ("SIN_CLASIFICAR", "Recibo pendiente"),
        ("NO_FACTURADO", "No facturado"),
        ("FACTURADO", "Facturado"),
    ],
)
def test_followup_receipt_statuses_and_missing_fields_are_independent(status, expected):
    record = _row(1)
    evidence = dict(
        record,
        readiness="LISTA",
        receipt_id=1,
        numero=5,
        estado_facturacion=status,
        dx="",
        numero_autorizacion="",
    )
    row = followup_rows([record], [evidence])[0]
    assert row["billing_state"] == expected
    assert len(row["issues"]) == 2


@pytest.mark.parametrize("reasons", ["not-json", '{"bad":"data"}', [], None])
def test_malformed_readiness_keeps_safe_review_message(reasons):
    record = _row(1)
    evidence = dict(record, readiness="REVISION", readiness_reasons=reasons)
    assert followup_rows([record], [evidence])[0]["issues"] == (
        "Revisar los datos de la atención.",
    )


def test_uninsured_receipt_does_not_require_authorization_and_deleted_is_reported():
    record = _row(1, ars="SIN SEGURO")
    evidence = dict(
        record,
        readiness="LISTA",
        receipt_id=1,
        numero=5,
        estado_facturacion="PENDIENTE",
        dx="CEFALEA",
        tipo_cobertura="NO_ASEGURADO",
    )
    assert followup_rows([record], [evidence])[0]["issues"] == ()
    evidence["is_deleted"] = True
    assert "anulada" in followup_rows([record], [evidence])[0]["issues"][0]


def test_followup_sql_uses_parameters_and_deduplicates_requested_ids():
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = []
    record = _row(1)
    assert load_billing_followup(connection, [record, record]) == []
    sql, params = connection.execute.call_args.args
    sql = sql.as_string(None)
    assert "jsonb_to_recordset(%s::jsonb)" in sql
    assert record["global_attention_id"] not in sql
    assert params[0].count(record["global_attention_id"]) == 1


def test_invalid_followup_identity_fails_before_query():
    connection = Mock()
    with pytest.raises(ValueError):
        load_billing_followup(connection, [dict(_row(1), global_attention_id="bad")])
    connection.execute.assert_not_called()


@pytest.mark.parametrize(
    "values",
    [
        None,
        [],
        {"name": "x", "selected_ars": "APS"},
        {"name": "x", "ars_mode": []},
        {"name": "x" * 81},
    ],
)
def test_malformed_view_values_are_rejected(values):
    with pytest.raises(ValueError):
        normalize_view(values)


def test_legacy_identity_requires_source_and_positive_id():
    connection = Mock()
    assert (
        load_billing_followup(connection, [{"attention_id": 0}, {"attention_id": 1}])
        == []
    )
    connection.execute.assert_not_called()


@pytest.mark.parametrize("authorization", ["１２３", "a", "1" * 41])
def test_followup_reuses_numeric_authorization_rules(authorization):
    record = _row(1)
    row = followup_rows(
        [record],
        [
            dict(
                record,
                receipt_id=1,
                numero=5,
                dx="DX",
                readiness="LISTA",
                numero_autorizacion=authorization,
            )
        ],
    )[0]
    assert row["issues"] == ("Recibo 5 sin autorización numérica.",)
