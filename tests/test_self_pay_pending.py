from decimal import Decimal
import pytest
from self_pay_billing import (
    payment_states,
    validate_payment,
    payment_summary,
    receipt_filters,
)
from pdf_engine import ReceiptPDFRenderer
from self_pay_history_dialog import export_summary
from openpyxl import load_workbook
from tests.test_billing_close_model import snapshot, receipt


@pytest.mark.parametrize("code", ["EXTRANJERO", "NO_ASEGURADO"])
def test_pending_is_valid_without_reason_and_without_revenue(code):
    assert "PENDIENTE_PAGO" in payment_states(code)
    assert validate_payment(code, "PENDIENTE_PAGO", "No debe conservarse") == ""
    row = next(
        row
        for row in payment_summary(
            [
                dict(
                    coverage=code,
                    payment_status="PENDIENTE_PAGO",
                    total=Decimal("5.99"),
                )
            ]
        )
        if row["coverage"] == code
    )
    assert row["pending_count"] == 1 and row["pending_amount"] == "5.99"
    assert row["collected"] == "0.00" and row["exempt_amount"] == "0.00"
    assert row["tariff_total"] == "5.99"
    sql, params = receipt_filters(
        start="2026-10-01", end="2026-10-03", status="PENDIENTE_PAGO"
    )
    assert "PENDIENTE_PAGO" not in sql and params[-1] == "PENDIENTE_PAGO"


def test_pending_pdf_is_debt_and_never_payment_confirmation():
    html = ReceiptPDFRenderer().render_html(
        dict(
            numero=1,
            paciente="DEMO",
            ars="EXTRANJERO",
            sala=460,
            total_general=460,
            payment_status="PENDIENTE_PAGO",
        )
    )
    assert "PENDIENTE DE PAGO" in html and "No acredita pago" in html
    assert "Importe pendiente" in html and "RD$ 0.00" in html
    assert "RECIBO DE COBRO · PAGADO" not in html


def test_pending_excel_separates_debt_from_revenue(tmp_path):
    path = tmp_path / "pending.xlsx"
    export_summary(
        path,
        payment_summary(
            [dict(coverage="EXTRANJERO", payment_status="PENDIENTE_PAGO", total="5.99")]
        ),
        dict(start="2026-10-01", end="2026-10-03"),
    )
    rows = list(load_workbook(path).active.values)
    assert rows[1][-2:] == ("Pendientes de pago", "Importe pendiente")
    assert rows[2][3] == 0 and rows[2][-2:] == (1, 5.99)


def test_pending_close_is_separate_from_insurance_and_cash():
    data = snapshot(
        receipts=[
            receipt(
                1,
                total="5.99",
                self_pay_coverage="EXTRANJERO",
                payment_status="PENDIENTE_PAGO",
            )
        ]
    )
    assert data["receipt_count"] == 0 and data["amount"] == "0.00"
    assert data["self_pay"][0]["pending_amount"] == "5.99"
    assert data["self_pay"][0]["collected"] == "0.00"


@pytest.mark.parametrize(
    "paid_at,amount",
    [
        ("2026-09-25T07:59:59-04:00", "0.00"),
        ("2026-09-25T08:00:00-04:00", "5.99"),
        ("2026-09-26T08:00:00-04:00", "0.00"),
    ],
)
def test_receipt_saved_previously_is_collected_in_payment_turn_only(paid_at, amount):
    data = snapshot(
        receipts=[
            receipt(
                1,
                created_at="2026-09-24T10:00:00-04:00",
                paid_at=paid_at,
                total="5.99",
                self_pay_coverage="EXTRANJERO",
                payment_status="PAGADO",
            )
        ]
    )
    assert data.get("self_pay", [{"collected": "0.00"}])[0]["collected"] == amount
