"""Validate receipt dates without changing the existing date-only grace window."""

from datetime import date

RECEIPT_DATE_GRACE_DAYS = 2


def validate_receipt_date(service_date: str, *, allow_older: bool, today: date) -> int:
    selected = date.fromisoformat(service_date)
    days_past = (today - selected).days
    if days_past < 0:
        raise ValueError("No puedes crear recibos con fechas futuras.")
    backdated = days_past > RECEIPT_DATE_GRACE_DAYS
    if backdated and not allow_older:
        raise PermissionError(
            "Necesitas el permiso «Facturar atenciones de más de 48 horas» "
            "para facturar esta fecha. Solicita la revisión de un administrador."
        )
    return int(backdated)
