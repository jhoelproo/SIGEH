"""Field ownership rules shared by receipt UI and persistence."""

from decimal import Decimal, InvalidOperation

MAX_ROOM_PRICE = Decimal("1000000")


def room_price(value) -> Decimal:
    try:
        price = Decimal(str(value)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise ValueError("El precio de sala debe ser un importe válido.") from exc
    if not price.is_finite() or not Decimal(0) <= price <= MAX_ROOM_PRICE:
        raise ValueError("El precio de sala está fuera del rango permitido.")
    return price


def editable_billing_fields(
    *, admin: bool, auxiliary: bool, validated: bool, read_only: bool
) -> dict:
    editable = not read_only
    can_identify = editable and not validated and not auxiliary
    return {
        "name_edit": can_identify,
        "date_edit": can_identify,
        "dx_edit": editable and (validated or not auxiliary),
        "ars_combo": can_identify,
        "coverage_combo": can_identify,
        "sala_spin": editable and admin,
    }


def require_room_price(*, admin: bool, supplied, catalog, existing=None) -> None:
    price = room_price(supplied)
    if admin:
        return
    expected = room_price(existing if existing is not None else catalog)
    if price != expected:
        raise PermissionError(
            "Sólo ADMIN puede modificar el precio de sala. "
            "Use la tarifa de la ARS o conserve el importe del recibo existente."
        )


def require_validated_header_edit(
    *, auxiliary: bool, validated: bool, supplied: dict, previous: dict
) -> None:
    if not auxiliary or validated:
        return
    protected = ("nombre", "dx", "fecha", "ars", "sala")
    if any(supplied[key] != previous.get(key) for key in protected):
        raise PermissionError(
            "Debe validar al paciente en Admisión antes de modificar sus datos de facturación."
        )
