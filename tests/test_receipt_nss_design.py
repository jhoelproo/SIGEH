"""Receipt identity is visible, escaped, and independent of authorization readiness."""

import pytest
from pdf_engine.renderer import ReceiptPDFRenderer
import CALCULOS_QT as app
from tests.test_receipt_history_loading import _Connection, _Result


@pytest.mark.parametrize(
    "values,expected",
    [
        ({"nss": "001234567"}, "001234567"),
        ({"admission_nss_snapshot": "002345678"}, "002345678"),
        ({}, "NO REGISTRADO"),
        ({"nss": "000000000000000000000000"}, "000000000000000000000000"),
    ],
)
def test_receipt_shows_nss_without_losing_leading_zeroes(values, expected):
    html = ReceiptPDFRenderer().render_html({"nombre": "PACIENTE SINTÉTICO", **values})
    assert "<b>NSS:</b>" in html
    assert expected in html
    assert "patient-identifier" in html


def test_identity_and_patient_name_cannot_inject_html():
    html = ReceiptPDFRenderer().render_html(
        {"nombre": "<script>alert(1)</script>", "nss": "<img src=x onerror=alert(2)>"}
    )
    assert "<script>alert(1)</script>" not in html
    assert "<img src=x" not in html
    assert "&lt;" in html


@pytest.mark.parametrize(
    "sort,clause",
    [
        ("recent", "r.id DESC"),
        ("service_recent", "r.fecha DESC"),
        ("service_oldest", "r.fecha ASC"),
        ("total", "r.total DESC"),
        ("total;DROP TABLE recibos;--", "r.id DESC"),
    ],
)
def test_history_sort_is_whitelisted_and_uses_lightweight_identity(sort, clause):
    con = _Connection([_Result(many=[])])
    assert app.list_receipts_history_rows(sort_order=sort, _connection=con) == []
    sql, params = con.calls[0]
    assert clause in sql
    assert "DROP" not in sql
    assert "admission_nss_snapshot" in sql
    assert "SELECT *" not in sql
    assert params[-2:] == [100, 0] or params[-2:] == (100, 0)


def test_history_numeric_search_includes_nss_and_cedula_using_parameters():
    sql, params = app._receipt_history_filter_sql(patient_or_receipt="001234")
    assert "r.admission_nss_snapshot LIKE %s" in sql
    assert "r.admission_cedula_snapshot LIKE %s" in sql
    assert "001234" not in sql
    assert "%001234%" in params
    ready, ready_params = app._receipt_history_filter_sql(flow_filter="ready")
    assert app.DOCUMENT_READY in ready_params
    assert "PENDIENTE" in ready
