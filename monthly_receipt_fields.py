"""ARS-specific identifiers and service dates used in monthly corrections."""

from monthly_candidate_search import normalized_name

BASE_DOCUMENT_TYPES = ("NSS", "CÉDULA")
ALTERNATE_DOCUMENT_TYPES = ("NO. PÓLIZA", "NO. AFILIADO", "NO. CARNET")
INSURER_DOCUMENT_TYPES = {
    "RENACER": "NO. PÓLIZA",
    "HUMANO": "NO. AFILIADO",
    "PRIMERA": "NO. AFILIADO",
    "SEMMA": "NO. CARNET",
}
INSURANCE_SCHEMA_SQL = """ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS insurance_document_type TEXT,
    ADD COLUMN IF NOT EXISTS insurance_document_number TEXT;"""


def ensure_receipt_insurance_schema(connection):
    connection.execute(INSURANCE_SCHEMA_SQL)


def document_type_key(value):
    return normalized_name(value).replace(".", "").replace(" ", "")


def list_document_options(ars):
    key = normalized_name(ars)
    alternate = next(
        (
            value
            for insurer, value in INSURER_DOCUMENT_TYPES.items()
            if insurer.casefold() in key.split()
        ),
        None,
    )
    return [*BASE_DOCUMENT_TYPES, *([alternate] if alternate else [])]


def normalize_document_type(value):
    key = document_type_key(value)
    for kind in (*BASE_DOCUMENT_TYPES, *ALTERNATE_DOCUMENT_TYPES):
        if document_type_key(kind) == key:
            return kind
    raise ValueError("Selecciona un tipo de identificación válido para la ARS.")


def receipt_document_changes(kind, number, ars):
    kind = normalize_document_type(kind)
    if kind not in list_document_options(ars):
        raise ValueError(
            "El tipo de identificación no corresponde a la ARS del recibo."
        )
    changes = {
        "insurance_document_type": kind,
        "insurance_document_number": number,
    }
    field = {
        "NSS": "admission_nss_snapshot",
        "CÉDULA": "admission_cedula_snapshot",
    }.get(kind)
    if field:
        changes[field] = number
    return changes


def optional_service_date(value):
    if value is None:
        return None
    from receipt_edit_integrity import receipt_service_date

    return receipt_service_date(value)


def monthly_metadata_changes(
    receipt,
    *,
    document_type,
    document_number,
    authorization,
    specialty,
    patient_name=None,
    service_date=None,
):
    changes = {
        "numero_autorizacion": authorization,
        "specialty_snapshot": specialty,
        **receipt_document_changes(document_type, document_number, receipt["ars"]),
    }
    if service_date is not None:
        changes["fecha"] = optional_service_date(service_date)
    if patient_name is not None:
        from receipt_patient_correction import normalized_patient_name

        changes["nombre"] = normalized_patient_name(patient_name)
    return changes


def receipt_service_date_snapshot(receipt):
    return str(receipt.get("service_date_snapshot") or receipt.get("fecha") or "")


def require_expected_date(receipt, expected):
    if expected is not None and optional_service_date(expected) != receipt["fecha"]:
        raise ValueError("La fecha cambió en otra pantalla. Actualiza el listado.")


def displayed_list_document(entry):
    kind = str(entry.get("document_type_snapshot") or "NSS")
    fields = {
        "NSS": "nss_snapshot",
        "CÉDULA": "cedula_snapshot",
        "CEDULA": "cedula_snapshot",
    }
    field = fields.get(kind)
    if field:
        return entry.get(field) or entry.get("document_number_snapshot")
    return entry.get("document_number_snapshot")
