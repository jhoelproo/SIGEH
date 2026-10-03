"""Rules for administrative linking, independent of external services."""

from types import SimpleNamespace
import pytest

from receipt_attention_link import (
    link_receipt_to_inherited_attention,
    validate_matching_patient,
    validate_unlinked_receipt,
)
from tests.test_billing_close_model import admission, receipt, snapshot


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"is_deleted": True},
        {"estado_documento": "ANULADO"},
        {"estado_facturacion": "CANCELADO"},
        {"admission_atencion_id": 1},
        {"admission_global_attention_id": "uuid"},
        {"admission_paciente_id": 1},
    ],
)
def test_invalid_or_linked_receipts_rejected(value):
    with pytest.raises(ValueError):
        validate_unlinked_receipt(value)


def test_saved_preliminary_receipt_is_eligible():
    validate_unlinked_receipt({"id": 1, "estado_documento": "PRELIMINAR"})


@pytest.mark.parametrize(
    "name,ars", [(" Ana  Perez ", " futuro "), ("ANA PEREZ", "FUTURO")]
)
def test_patient_matching_normalizes_spaces_and_case(name, ars):
    validate_matching_patient(
        {"nombre": name, "ars": ars},
        {"patient_name": "ANA PEREZ", "canonical_ars": "FUTURO"},
    )


@pytest.mark.parametrize(
    "change",
    [
        {"nombre": "OTRA PERSONA"},
        {"nombre": ""},
        {"ars": "OTRA ARS"},
        {"ars": ""},
        {"tipo_cobertura": "NO_ASEGURADO"},
        {"admission_nss_snapshot": "999"},
        {"admission_cedula_snapshot": "999"},
    ],
)
def test_mismatched_identity_or_insurance_rejected(change):
    with pytest.raises(ValueError):
        validate_matching_patient(
            {"nombre": "ANA", "ars": "FUTURO", **change},
            {
                "patient_name": "ANA",
                "canonical_ars": "FUTURO",
                "nss_snapshot": "123",
                "cedula_snapshot": "123",
            },
        )


@pytest.mark.parametrize("ars", ["", "SIN SEGURO"])
def test_matching_document_punctuation_and_uninsured(ars):
    validate_matching_patient(
        {
            "nombre": "ANA",
            "ars": ars,
            "tipo_cobertura": "NO_ASEGURADO",
            "admission_nss_snapshot": "12-3",
            "admission_cedula_snapshot": "",
        },
        {
            "patient_name": "ANA",
            "canonical_ars": "SIN SEGURO",
            "coverage_status": "SIN_SEGURO_DECLARADO",
            "nss_snapshot": "123",
            "cedula_snapshot": "123",
        },
    )


@pytest.mark.parametrize("role", [None, "auxiliar", "auditor", ""])
def test_only_persisted_admin_role_can_link(role):
    backend = SimpleNamespace(
        get_user=lambda _: {"role": role},
        normalize_role=lambda x: x,
        ROLE_ADMIN="admin",
    )
    with pytest.raises(PermissionError):
        link_receipt_to_inherited_attention(1, {}, "user", backend=backend)


@pytest.mark.parametrize("turn", [1, 2])
def test_late_link_reduces_pending_without_counting_old_money(turn):
    rows = [admission("one", turn=turn)]
    old = receipt(1, created_at="2026-09-24T12:00:00-04:00", total="100")
    original = snapshot(rows, [old])
    result = snapshot(rows, [{**old, "attention_id": "one"}])
    assert original["total_pending"] == 1
    assert result["total_pending"] == 0
    assert result["receipt_count"] == original["receipt_count"] == 0
    assert float(result["amount"]) == float(original["amount"]) == 0
    assert result.keys() == original.keys()
