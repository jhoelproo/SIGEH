"""Regression for a final price entered into the medication catalog."""

import pytest

import CALCULOS_QT as app


@pytest.mark.parametrize("entered", [6, 6.0, "6.00", "5.99", "0.10"])
def test_effective_catalog_price_survives_base_conversion(entered):
    stored_base = app.medication_base_price_from_effective(entered, percent=35)
    reopened = app.calculate_medication_price(stored_base, percent=35)
    assert reopened == float(entered)


@pytest.mark.parametrize("entered,quantity,expected", [(6, 2, 12), (5.99, 3, 17.97)])
def test_catalog_price_roundtrip_preserves_line_total(entered, quantity, expected):
    stored_base = app.medication_base_price_from_effective(entered, percent=35)
    reopened = app.calculate_medication_price(stored_base, percent=35)
    assert round(reopened * quantity, 2) == expected
