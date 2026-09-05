"""Optional PostgreSQL UUIDs and privacy-safe receipt persistence diagnostics."""

from contextlib import contextmanager
from uuid import UUID


class InvalidOptionalUUID(ValueError):
    """A supplied relationship identifier is nonempty but malformed."""


def normalize_optional_uuid(value: str | UUID | None) -> str | UUID | None:
    if value is None or isinstance(value, UUID):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            return str(UUID(value))
        except ValueError:
            pass
    raise InvalidOptionalUUID("El identificador de Admisión no es un UUID válido.")


def receipt_admission_uuid(value, log):
    """Validate before SQL; log types/absence, never the supplied identifier."""
    metadata = (
        "operation=SAVE_RECEIPT column_name=admission_global_attention_id "
        f"postgres_type=uuid python_type={type(value).__name__} "
        f"is_none={value is None} "
        f"is_blank_string={isinstance(value, str) and not value.strip()}"
    )
    try:
        normalized = normalize_optional_uuid(value)
    except InvalidOptionalUUID:
        log(f"RECEIPT_UUID_VALIDATION {metadata} uuid_validation_status=INVALID")
        raise
    log(f"RECEIPT_UUID_VALIDATION {metadata} uuid_validation_status=VALID")
    # psycopg2 accepts canonical text without globally changing UUID adapters.
    return None if normalized is None else str(normalized)


@contextmanager
def receipt_persistence_diagnostics(log, *, bypass, global_id, source_id):
    try:
        yield
    except Exception as error:
        log(
            "RECEIPT_PERSISTENCE_FAILED operation=SAVE_RECEIPT "
            f"bypass={bool(bypass)} has_global_attention_id={global_id is not None} "
            f"has_source_instance_id={bool(source_id)} uuid_validation_status=VALID "
            f"exception_type={type(error).__name__}"
        )
        raise


def receipt_save_error_message(error):
    if isinstance(error, (InvalidOptionalUUID, PermissionError)):
        return str(error)
    return (
        "No se pudo guardar el recibo. Revise el registro técnico "
        "o contacte al administrador antes de reintentar."
    )
