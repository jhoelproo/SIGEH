"""Receipt-edit context derived from a centrally matched receipt only."""


def apply_owned_receipt_context(row: dict, receipt_id: int | None) -> dict:
    result = dict(row)
    own = receipt_id is not None and row.get("linked_receipt_id") == receipt_id
    result["editing_own_receipt"] = own
    if not own:
        return result
    result["explicitly_inherited"] = bool(row.get("explicitly_inherited")) or (
        row.get("receipt_inheritance_state") == "HEREDADA_PROCESADA"
        and row.get("receipt_origin_turn") == row.get("turn_id")
    )
    # Only this receipt's link is exempt from duplication, never its clinical,
    # deletion, operational-scope or foreign-claim validation.
    result["linked_receipt_id"] = None
    result["linked_billing_status"] = None
    result["linked_document_status"] = None
    return result


class AdmissionDataChanged(ValueError):
    def __init__(self, fields):
        self.fields = tuple(fields)
        super().__init__(
            "Hay diferencias con los datos actuales de Admisión: "
            + ", ".join(fields)
            + ". Revise y vuelva a validar la atención. El recibo no se guardó."
        )


def validate_admission_snapshot(snapshot: dict, projection: dict) -> None:
    fields = (
        (("service_date", "fecha"), "service_date", "fecha"),
        (("name", "nombre"), "patient_name", "nombre"),
        (("canonical_ars", "ars"), "canonical_ars", "ARS"),
        (("nss_clean",), "nss_snapshot", "NSS"),
        (("cedula_clean",), "cedula_snapshot", "cédula"),
        (("attention_type", "service_type"), "service_type", "tipo de atención"),
    )
    differences = []
    for aliases, column, label in fields:
        present = [key for key in aliases if key in snapshot]
        if not present:
            continue
        old = next((snapshot[key] for key in present if snapshot[key]), "")
        if normalize_snapshot_value(old) != normalize_snapshot_value(
            projection.get(column)
        ):
            differences.append(label)
    if differences:
        raise AdmissionDataChanged(differences)


def normalize_snapshot_value(value) -> str:
    return " ".join(str(value or "").strip().upper().split())


def can_refresh_patient_in_draft(previous: dict, verified: dict) -> bool:
    """Allow explicit revalidation of one central attention without repricing."""
    from uuid import UUID

    try:
        previous_id = UUID(str(previous.get("global_attention_id") or ""))
        verified_id = UUID(str(verified.get("global_attention_id") or ""))
    except (ValueError, AttributeError):
        return False
    if previous_id != verified_id or verified.get("uninsured"):
        return False
    if verified.get("billing_readiness") != "LISTA":
        return False
    if verified.get("source_status") != "ACTIVA":
        return False
    fields = (
        "service_date",
        "canonical_ars",
        "attention_type",
        "coverage_status",
        "source_instance_id",
        "attention_id",
    )
    return all(
        bool(previous.get(field))
        and normalize_snapshot_value(previous[field])
        == normalize_snapshot_value(verified.get(field))
        for field in fields
    )
