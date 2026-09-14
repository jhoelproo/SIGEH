"""Receipt service dates and insurance invariants for historical edits."""

from datetime import datetime


def receipt_validation_snapshot(snapshot: dict, *, editable_header: bool) -> dict:
    result = dict(snapshot)
    if editable_header:
        for field in ("name", "nombre", "service_date", "fecha"):
            result.pop(field, None)
    return result


def receipt_service_date(value) -> str:
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return (
                datetime.strptime(str(value or "").strip(), pattern).date().isoformat()
            )
        except ValueError:
            continue
    raise ValueError(
        "El recibo tiene una fecha de servicio inválida. Revísela antes de editar."
    )


def require_same_insurance(ars: str, coverage: str, previous: dict) -> None:
    previous_ars = str(previous.get("ars") or "").strip()
    previous_coverage = str(
        previous.get("tipo_cobertura")
        or ("ASEGURADO" if previous_ars else "NO_ASEGURADO")
    )
    if (
        ars.strip().casefold() != previous_ars.casefold()
        or coverage != previous_coverage
    ):
        raise PermissionError(
            "No se permite cambiar el seguro ni la cobertura al editar un recibo."
        )
