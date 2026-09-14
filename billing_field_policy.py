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
    *,
    admin: bool,
    auxiliary: bool,
    validated: bool,
    read_only: bool,
    editing: bool = False,
) -> dict:
    can_identify = not validated and not auxiliary
    identity_editable = can_identify or (admin and editing)
    insurance_editable = can_identify and not editing
    fields = {
        "name_edit": identity_editable,
        "date_edit": identity_editable,
        "dx_edit": validated or not auxiliary,
        "ars_combo": insurance_editable,
        "coverage_combo": insurance_editable,
        "sala_spin": admin,
    }
    return dict.fromkeys(fields, False) if read_only else fields


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
