from decimal import Decimal

import pytest

from self_pay_billing import (
    coverage_code,
    coverage_label,
    is_self_pay,
    tariff_ars,
    payment_summary,
)
import self_pay_billing as billing
from types import SimpleNamespace


@pytest.mark.parametrize(
    "label,code",
    [
        ("Asegurado", "ASEGURADO"),
        ("No asegurado", "NO_ASEGURADO"),
        ("Extranjero", "EXTRANJERO"),
    ],
)
def test_explicit_coverage_and_shared_tariff(label, code):
    assert coverage_code(label) == code
    assert coverage_label(code) == label
    assert tariff_ars(code, "APS") == (
        "APS" if code == "ASEGURADO" else "SENASA CONTRIBUTIVO"
    )
    assert is_self_pay(code) == (code != "ASEGURADO")


@pytest.mark.parametrize("value", ["", "Otra", None, "ASEGURADO"])
def test_invalid_ui_coverage_is_rejected(value):
    with pytest.raises(ValueError):
        coverage_code(value)


def test_empty_summary_keeps_foreign_and_uninsured_separate():
    result = payment_summary([])
    assert [row["coverage"] for row in result] == ["EXTRANJERO", "NO_ASEGURADO"]
    assert all(row["count"] == 0 and row["tariff_total"] == "0.00" for row in result)


def test_summary_does_not_mutate_source_or_round_away_cents():
    rows = [
        {"coverage": "EXTRANJERO", "payment_status": "PAGADO", "total": Decimal("5.99")}
    ]
    result = payment_summary(rows)
    assert result[0]["count"] == 1
    assert result[0]["tariff_total"] == "5.99"
    assert rows[0]["total"] == Decimal("5.99")


def test_invalid_coverage_label_and_tariff_are_rejected():
    for operation in (
        billing.coverage_label,
        lambda value: billing.tariff_ars(value, "APS"),
    ):
        with pytest.raises(ValueError):
            operation("UNKNOWN")


@pytest.mark.parametrize(
    "form,expected",
    [
        (SimpleNamespace(), "ASEGURADO"),
        (SimpleNamespace(coverage_combo=object()), "ASEGURADO"),
        (
            SimpleNamespace(
                coverage_combo=SimpleNamespace(currentText=lambda: "Extranjero")
            ),
            "EXTRANJERO",
        ),
        (
            SimpleNamespace(coverage_combo=SimpleNamespace(currentText=lambda: "")),
            "ASEGURADO",
        ),
    ],
)
def test_form_coverage_handles_legacy_forms(form, expected):
    assert billing.form_coverage(form) == expected


@pytest.mark.parametrize(
    "code,status,reason,valid",
    [
        ("EXTRANJERO", "PAGADO", "", True),
        ("EXTRANJERO", "EXONERADO", "Motivo válido", False),
        ("NO_ASEGURADO", "PAGADO", "Se descarta", True),
        ("NO_ASEGURADO", "EXONERADO", "1234567", False),
        ("NO_ASEGURADO", "EXONERADO", " 12345678 ", True),
        ("NO_ASEGURADO", "EXONERADO", "123456789", True),
        ("ASEGURADO", "PAGADO", "", False),
        ("NO_ASEGURADO", "", "", False),
    ],
)
def test_payment_business_rules_and_reason_boundaries(code, status, reason, valid):
    if valid:
        assert billing.validate_payment(code, status, reason) == (
            reason.strip() if status == "EXONERADO" else ""
        )
    else:
        with pytest.raises(ValueError):
            billing.validate_payment(code, status, reason)


def test_mixed_summary_separates_collection_and_exemptions():
    rows = [
        {
            "coverage": "EXTRANJERO",
            "payment_status": "PAGADO",
            "total": "10.25",
            "count": 2,
        },
        {"coverage": "NO_ASEGURADO", "payment_status": "PAGADO", "total": "20.10"},
        {"coverage": "NO_ASEGURADO", "payment_status": "EXONERADO", "total": "30.99"},
        {"coverage": "ASEGURADO", "payment_status": "PAGADO", "total": "900"},
    ]
    foreign, uninsured = billing.payment_summary(rows)
    assert foreign["count"] == 2 and foreign["collected"] == "10.25"
    assert uninsured["count"] == 2 and uninsured["tariff_total"] == "51.09"
    assert uninsured["collected"] == "20.10" and uninsured["exempt_amount"] == "30.99"


class Connection:
    def __init__(self, room=None, catalog=(), previous=(), rows=()):
        self.room, self.catalog, self.previous, self.rows = (
            room,
            catalog,
            previous,
            rows,
        )
        self.calls = []

    def executescript(self, sql):
        self.calls.append((sql, ()))

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        selected = (
            self.previous
            if "precio_unit" in sql
            else self.catalog
            if "ars_items" in sql
            else self.rows
        )
        return SimpleNamespace(fetchone=lambda: self.room, fetchall=lambda: selected)


def test_schema_and_payment_write_use_bound_parameters():
    con = Connection()
    billing.install_schema(con)
    assert "ENABLE ROW LEVEL SECURITY" in con.calls[0][0]
    billing.save_payment(
        con, 7, "NO_ASEGURADO", "EXONERADO", "Motivo válido", "usuario"
    )
    assert con.calls[-1][1] == (
        7,
        "NO_ASEGURADO",
        "EXONERADO",
        "Motivo válido",
        "usuario",
        "EXONERADO",
    )


@pytest.mark.parametrize(
    "limit,offset,expected",
    [(0, -1, (1, 0)), (1, 0, (1, 0)), (200, 100, (200, 100)), (201, 200, (200, 200))],
)
def test_history_pagination_and_sql_parameters(limit, offset, expected):
    con = Connection(rows=[{"id": 1}])
    result = billing.list_receipts(
        con,
        start="2026-10-01",
        end="2026-10-03",
        code="EXTRANJERO",
        status="PAGADO",
        search="'; DROP TABLE recibos;--",
        limit=limit,
        offset=offset,
    )
    sql, params = con.calls[-1]
    assert "DROP TABLE" not in sql and "'; DROP TABLE" in params[4]
    assert params[-2:] == expected
    assert result == [{"id": 1}]


@pytest.mark.parametrize("filters", [{"code": "UNKNOWN"}, {"status": "FACTURADO"}])
def test_invalid_history_filters_fail_before_query(filters):
    with pytest.raises(ValueError):
        billing.receipt_filters(start="2026-10-01", end="2026-10-03", **filters)


def test_empty_history_and_summary_and_aggregate():
    con = Connection()
    filters = {"start": "2026-10-01", "end": "2026-10-03"}
    assert billing.list_receipts(con, **filters) == []
    assert all(row["count"] == 0 for row in billing.load_summary(con, **filters))
    con.rows = [
        {
            "coverage": "EXTRANJERO",
            "payment_status": "PAGADO",
            "count": 12,
            "total": "120.00",
        }
    ]
    assert billing.load_summary(con, **filters)[0]["paid_count"] == 12


@pytest.mark.parametrize(
    "room,catalog,items,receipt_id,previous,valid",
    [
        (None, [], [], None, [], False),
        ({"sala_emergencia": 460, "consulta_price": 300}, [], [], None, [], True),
        ({"sala_emergencia": 461, "consulta_price": 300}, [], [], None, [], False),
        (
            {"sala_emergencia": 460, "consulta_price": 300},
            [],
            [("Laboratorios", "L", 100, 1, 100, "SENASA CONTRIBUTIVO")],
            None,
            [],
            False,
        ),
        (
            {"sala_emergencia": 460, "consulta_price": 300},
            [{"categoria": "Laboratorios", "nombre": "L", "precio": 100}],
            [("Laboratorios", "L", 100, 1, 100, "SENASA CONTRIBUTIVO")],
            None,
            [],
            True,
        ),
        (
            {"sala_emergencia": 460, "consulta_price": 300},
            [],
            [("Laboratorios", "L", 90, 1, 90, "SENASA CONTRIBUTIVO")],
            1,
            [{"categoria": "Laboratorios", "nombre": "L", "precio_unit": 90}],
            True,
        ),
        (
            {"sala_emergencia": 460, "consulta_price": 300},
            [],
            [("Medicamentos", "M", 10, 1, 10, "")],
            None,
            [],
            True,
        ),
    ],
)
def test_shared_tariffs_and_existing_item_prices(
    room, catalog, items, receipt_id, previous, valid
):
    con = Connection(room, catalog, previous)
    if valid:
        billing.validate_tariffs(
            con, room=460, items=items, service_type="EMERGENCIA", receipt_id=receipt_id
        )
    else:
        with pytest.raises(ValueError):
            billing.validate_tariffs(
                con,
                room=460,
                items=items,
                service_type="EMERGENCIA",
                receipt_id=receipt_id,
            )


def test_consultation_tariff_is_shared():
    billing.validate_tariffs(
        Connection({"sala_emergencia": 460, "consulta_price": 300}),
        room=300,
        items=[],
        service_type="CONSULTA",
    )


@pytest.mark.parametrize(
    "price,quantity,subtotal,total,valid",
    [
        ("5.99", 1, "5.99", "465.99", True),
        (0, 1, 0, 460, True),
        (1, 0, 0, 460, False),
        (1, -1, -1, 459, False),
        (-1, 1, -1, 459, False),
        (1, 1, 2, 462, False),
        (1, 1, 1, 999, False),
    ],
)
def test_amount_boundaries(price, quantity, subtotal, total, valid):
    items = [("Materiales", "M", price, quantity, subtotal, "")]
    if valid:
        billing.validate_amounts(460, items, total)
    else:
        with pytest.raises(ValueError):
            billing.validate_amounts(460, items, total)


def test_zero_total_is_not_a_payable_receipt():
    with pytest.raises(ValueError):
        billing.validate_amounts(0, [], 0)


@pytest.mark.parametrize(
    "previous,room,expected",
    [(None, 460, False), ({"sala": 460}, 460, True), ({"sala": 460}, 461, False)],
)
def test_edit_keeps_own_original_room_tariff(previous, room, expected):
    assert billing._retains_room_price(Connection(previous), 1, room) == expected


def test_auxiliary_cannot_change_unverified_insured_header():
    from billing_field_policy import require_validated_header_edit

    values = dict(nombre="A", dx="DX", fecha="2026-10-03", ars="ARS", sala=460)
    with pytest.raises(PermissionError):
        require_validated_header_edit(
            auxiliary=True,
            validated=False,
            supplied={**values, "nombre": "B"},
            previous=values,
        )
