import pytest

from billing_field_policy import editable_billing_fields
from receipt_edit_integrity import receipt_service_date, require_same_insurance


@pytest.mark.parametrize("editable", [False, True])
def test_receipt_header_correction_preserves_other_admission_guards(editable):
    from receipt_edit_integrity import receipt_validation_snapshot

    source = {
        "name": "TEST",
        "service_date": "2026-09-13",
        "canonical_ars": "APS",
        "attention_type": "EMERGENCIA",
        "global_attention_id": "identity",
    }
    result = receipt_validation_snapshot(source, editable_header=editable)
    assert ("name" in result) == (not editable)
    assert ("service_date" in result) == (not editable)
    assert result["canonical_ars"] == "APS"
    assert result["attention_type"] == "EMERGENCIA"
    assert result["global_attention_id"] == "identity"
    assert source["name"] == "TEST"


@pytest.mark.parametrize("validated", [False, True])
def test_admin_edit_unlocks_header_but_not_insurance(validated):
    fields = editable_billing_fields(
        admin=True, auxiliary=False, validated=validated, read_only=False, editing=True
    )
    assert fields["name_edit"] and fields["date_edit"] and fields["dx_edit"]
    assert fields["sala_spin"]
    assert not fields["ars_combo"] and not fields["coverage_combo"]


@pytest.mark.parametrize("date", ["2026-09-13", "13/09/2026", "13-09-2026"])
def test_service_date_is_preserved(date):
    assert receipt_service_date(date) == "2026-09-13"


@pytest.mark.parametrize("date", ["", None, "31/02/2026", "09/13/26"])
def test_invalid_service_date_cannot_silently_become_today(date):
    with pytest.raises(ValueError):
        receipt_service_date(date)


def test_insurance_is_immutable_for_existing_receipts():
    require_same_insurance(
        "APS", "ASEGURADO", {"ars": "APS", "tipo_cobertura": "ASEGURADO"}
    )
    with pytest.raises(PermissionError):
        require_same_insurance(
            "FUTURO", "ASEGURADO", {"ars": "APS", "tipo_cobertura": "ASEGURADO"}
        )
    with pytest.raises(PermissionError):
        require_same_insurance(
            "APS", "NO_ASEGURADO", {"ars": "APS", "tipo_cobertura": "ASEGURADO"}
        )


@pytest.mark.parametrize(
    "authorization", ["", "1", "19", "123", "1234", "39015", "3901505"]
)
def test_edit_presentation_never_claims_creation_or_bypass(authorization):
    import CALCULOS_QT as app

    for validated in (False, True):
        _, ready, text = app.billing_readiness_presentation(
            patient_validated=validated,
            authorization=authorization,
            privileged_unlinked=not validated,
            editing=True,
        )
        assert "EDICIÓN" in text
        assert "Bypass" not in text and "crear" not in text
        assert ready == bool(authorization)


@pytest.mark.parametrize("authorization", ["19", "123", "1234", "3901505"])
def test_snapshot_keeps_original_author_date_and_origin(authorization):
    from collections import defaultdict
    from types import SimpleNamespace
    from receipt_documents import build_receipt_snapshot

    row = defaultdict(
        lambda: None,
        id=1,
        numero=100,
        username="original",
        visible_user="Creador original",
        fecha="2026-09-13",
        created_at="2026-09-13 10:00:00",
        numero_autorizacion=authorization,
        receipt_origin="LEGACY",
        verification_bypassed=False,
    )
    connection = SimpleNamespace(
        execute=lambda *args: SimpleNamespace(fetchone=lambda: row, fetchall=lambda: [])
    )
    result = build_receipt_snapshot(
        connection, 1, document_context={"visible_user": "Editor"}
    )
    assert result["document"]["visible_user"] == "Creador original"
    assert result["header"]["generated_by"] == "original"
    assert result["header"]["generated_at"] == "2026-09-13 10:00:00"
    assert result["header"]["service_date"] == "2026-09-13"
    assert result["header"]["authorization_number"] == authorization
    assert result["bypass_audit"]["receipt_origin"] == "LEGACY"
    assert not result["bypass_audit"]["verification_bypassed"]
