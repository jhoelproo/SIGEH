from __future__ import annotations

from dataclasses import asdict, dataclass


BILLING_PENDING = "PENDIENTE"
BILLING_INVOICED = "FACTURADO"
BILLING_NOT_INVOICED = "NO_FACTURADO"
BILLING_UNCLASSIFIED = "SIN_CLASIFICAR"
BILLING_ALL = "TODOS"

ANALYSIS_CONFIRMED = "confirmed_billing"
ANALYSIS_PRODUCTION = "generated_production"
ANALYSIS_PENDING = "pending_validation"
ANALYSIS_NOT_INVOICED = "not_invoiced"
ANALYSIS_HISTORICAL = "unclassified_history"

ANALYSIS_OPTIONS = {
    ANALYSIS_CONFIRMED: {
        "label": "Facturación confirmada",
        "statuses": (BILLING_INVOICED,),
        "date_basis": "status_changed",
        "total_label": "TOTAL FACTURADO",
        "receipt_label": "RECIBOS FACTURADOS",
    },
    ANALYSIS_PRODUCTION: {
        "label": "Producción generada",
        "statuses": (),
        "date_basis": "generated",
        "total_label": "TOTAL GENERADO",
        "receipt_label": "RECIBOS GENERADOS",
    },
    ANALYSIS_PENDING: {
        "label": "Pendientes de validación",
        "statuses": (BILLING_PENDING,),
        "date_basis": "generated",
        "total_label": "MONTO PENDIENTE",
        "receipt_label": "RECIBOS PENDIENTES",
    },
    ANALYSIS_NOT_INVOICED: {
        "label": "No facturados",
        "statuses": (BILLING_NOT_INVOICED,),
        "date_basis": "status_changed",
        "total_label": "MONTO NO FACTURADO",
        "receipt_label": "RECIBOS NO FACTURADOS",
    },
    ANALYSIS_HISTORICAL: {
        "label": "Históricos sin clasificar",
        "statuses": (BILLING_UNCLASSIFIED,),
        "date_basis": "generated",
        "total_label": "TOTAL HISTÓRICO SIN CLASIFICAR",
        "receipt_label": "RECIBOS SIN CLASIFICAR",
    },
}

DATE_EXPRESSIONS = {
    "generated": "NULLIF({alias}.created_at, '')::timestamp::date",
    "service": "NULLIF({alias}.fecha, '')::date",
    "status_changed": "NULLIF({alias}.estado_facturacion_at, '')::timestamp::date",
}
DATE_BASIS_LABELS = {
    "generated": "Fecha de generación",
    "service": "Fecha del servicio",
    "status_changed": "Fecha de validación o cambio de estado",
}


def medication_ars_sql_exclusion(alias: str) -> str:
    """Excluye SENASA SUBSIDIADO de los reportes operativos de medicamentos."""
    normalized = (
        "UPPER(REGEXP_REPLACE(COALESCE("
        f"{alias}.ars,''), '[^A-Za-z0-9]+', '', 'g'))"
    )
    # No usar un '%' literal aquí: psycopg2 interpreta cualquier porcentaje
    # dentro de una consulta parametrizada como parte de pyformat.
    return f"{normalized} !~ '^SENASASUB'"


def normalize_analysis_type(value: str) -> str:
    return value if value in ANALYSIS_OPTIONS else ANALYSIS_CONFIRMED


def analysis_definition(value: str) -> dict:
    key = normalize_analysis_type(value)
    return {"key": key, **ANALYSIS_OPTIONS[key]}


def date_expression(alias: str, date_basis: str) -> str:
    key = date_basis if date_basis in DATE_EXPRESSIONS else "generated"
    return DATE_EXPRESSIONS[key].format(alias=alias)


def receipt_scope(
    alias: str,
    start_date: str,
    end_date: str,
    analysis_type: str,
    service_type: str = "EMERGENCIA",
):
    definition = analysis_definition(analysis_type)
    expression = date_expression(alias, definition["date_basis"])
    clauses = [
        f"{expression} BETWEEN %s::date AND %s::date",
        f"{alias}.is_deleted=0",
        f"COALESCE({alias}.service_type, 'EMERGENCIA')=%s",
        medication_ars_sql_exclusion(alias),
    ]
    params: list = [start_date, end_date, service_type]
    statuses = list(definition["statuses"])
    if statuses:
        clauses.append(f"{alias}.estado_facturacion = ANY(%s)")
        params.append(statuses)
    return clauses, params, expression, definition


@dataclass(frozen=True)
class ReportQuery:
    start_date: str
    end_date: str
    analysis_type: str = ANALYSIS_CONFIRMED
    ars_filter: object = None
    user_filter: object = None
    coverage: str = "Todas"
    category: str = "Todas las categorías"
    trend_granularity: str = "day"

    def definition(self) -> dict:
        return analysis_definition(self.analysis_type)

    def to_dict(self) -> dict:
        result = asdict(self)
        result.update(self.definition())
        result["statuses"] = list(result.get("statuses", []))
        result["date_basis_label"] = DATE_BASIS_LABELS[result["date_basis"]]
        return result
