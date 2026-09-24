"""Decimal money arithmetic; floats are confined to legacy UI boundaries."""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal("0.01")
BASE_PRECISION = Decimal("0.000000000001")


def decimal_amount(value):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Importe inválido.") from error
    if not amount.is_finite() or amount < 0:
        raise ValueError("El importe debe ser finito y no negativo.")
    return amount


def money(value):
    return decimal_amount(value).quantize(CENT, rounding=ROUND_HALF_UP)


def markup_factor(percent):
    value = decimal_amount(percent)
    if value > 100:
        raise ValueError("El recargo debe estar entre 0 y 100.")
    return 1 + value / 100


def effective_medication_price(base_price, percent):
    return money(decimal_amount(base_price) * markup_factor(percent))


def medication_base_price(effective_price, percent):
    # A base cost is an intermediate value, not a final currency amount.
    return (money(effective_price) / markup_factor(percent)).quantize(
        BASE_PRECISION, rounding=ROUND_HALF_UP
    )


def line_total(unit_price, quantity):
    units = decimal_amount(quantity)
    if units != units.to_integral_value():
        raise ValueError("La cantidad debe ser un número entero.")
    return money(money(unit_price) * units)


def sum_money(values):
    return sum((money(value) for value in values), Decimal("0.00"))


def install_money_storage(connection):
    """Preserve stored values while avoiding REAL precision loss on future writes."""
    columns = (
        ("universal_items", "precio", "NUMERIC(24,12)"),
        ("ars_items", "precio", "NUMERIC(18,2)"),
        ("recibos", "sala", "NUMERIC(18,2)"),
        ("recibos", "total", "NUMERIC(18,2)"),
        ("recibo_items", "precio_unit", "NUMERIC(18,2)"),
        ("recibo_items", "total", "NUMERIC(18,2)"),
    )
    for table, column, datatype in columns:
        connection.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} TYPE {datatype} "
            f"USING {column}::TEXT::{datatype}"
        )
