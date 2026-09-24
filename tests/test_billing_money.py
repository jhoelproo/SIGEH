from decimal import Decimal

import pytest

from billing_money import (
    decimal_amount,
    effective_medication_price,
    line_total,
    markup_factor,
    medication_base_price,
    money,
    sum_money,
)


@pytest.mark.parametrize("invalid", [None, "", "wrong", "NaN", "Infinity", -1])
def test_reject_invalid_amount(invalid):
    with pytest.raises(ValueError):
        decimal_amount(invalid)


@pytest.mark.parametrize(
    "value,expected", [("0.005", "0.01"), (0, "0.00"), ("5.99", "5.99")]
)
def test_round_half_up(value, expected):
    assert money(value) == Decimal(expected)


@pytest.mark.parametrize("percent", [0, 35, 100])
@pytest.mark.parametrize("price", ["0.10", "5.99", "6.00", "999999.99"])
def test_price_survives_inverse_markup(price, percent):
    assert effective_medication_price(
        medication_base_price(price, percent), percent
    ) == Decimal(price)


def test_markup_limit_and_fractional_quantity():
    with pytest.raises(ValueError):
        markup_factor("100.01")
    with pytest.raises(ValueError):
        line_total(6, "1.5")
    assert line_total(6, 0) == Decimal("0.00")
    assert line_total("5.99", 3) == Decimal("17.97")
    assert sum_money([]) == Decimal("0.00")
    assert sum_money(["0.10"] * 10) == Decimal("1.00")
