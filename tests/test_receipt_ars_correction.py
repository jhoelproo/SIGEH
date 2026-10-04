import itertools

import pytest

from billing_field_policy import editable_billing_fields
from receipt_edit_integrity import require_same_insurance


@pytest.mark.parametrize(
    "validated,self_pay,read_only", itertools.product([False, True], repeat=3)
)
def test_admin_can_correct_only_insured_edit_ars(validated, self_pay, read_only):
    fields = editable_billing_fields(
        admin=True,
        auxiliary=False,
        validated=validated,
        read_only=read_only,
        editing=True,
        self_pay=self_pay,
    )
    assert fields["ars_combo"] == (not self_pay and not read_only)
    assert not fields["coverage_combo"]


def test_persistence_allows_admin_ars_correction_but_keeps_coverage():
    previous = {"ars": "APS", "tipo_cobertura": "ASEGURADO"}
    require_same_insurance("FUTURO", "ASEGURADO", previous, admin=True)
    with pytest.raises(PermissionError):
        require_same_insurance("FUTURO", "ASEGURADO", previous)
    with pytest.raises(PermissionError):
        require_same_insurance("FUTURO", "EXTRANJERO", previous, admin=True)


@pytest.mark.parametrize("coverage", ["NO_ASEGURADO", "EXTRANJERO"])
def test_direct_receipt_never_gets_an_insurer(coverage):
    with pytest.raises(PermissionError):
        require_same_insurance(
            "APS", coverage, {"ars": "", "tipo_cobertura": coverage}, admin=True
        )


def test_tariff_plan_preserves_quantities_and_recalculates_all_categories():
    from receipt_ars_correction import reprice_items

    items = [
        ("Laboratorios", "HEMOGRAMA", 10, 2, 20, "APS"),
        ("Medicamentos", "PRUEBA", 2, 3, 6, ""),
    ]
    prices = {"Laboratorios": {"hemograma": 187.25}, "Medicamentos": {"PRUEBA": 10}}
    result = reprice_items(
        items,
        prices,
        lambda category, price: price * (1.35 if category == "Medicamentos" else 1),
    )
    assert result == [
        ("Laboratorios", "HEMOGRAMA", 187.25, 2, 374.5),
        ("Medicamentos", "PRUEBA", 13.5, 3, 40.5),
    ]
    assert items[0][2] == 10


@pytest.mark.parametrize(
    "price,quantity", [(None, 1), (-1, 1), (float("inf"), 1), (10, 0), (10, -1)]
)
def test_missing_or_invalid_tariff_is_rejected(price, quantity):
    from receipt_ars_correction import reprice_items

    with pytest.raises(ValueError):
        reprice_items(
            [("Laboratorios", "PRUEBA", 0, quantity, 0, "APS")],
            {"Laboratorios": {"PRUEBA": price}},
            lambda category, value: value,
        )


def test_empty_plan():
    from receipt_ars_correction import reprice_items

    assert reprice_items([], {}, lambda category, value: value) == []


@pytest.mark.parametrize(
    "attention,previous",
    [
        (None, {}),
        (
            {"global_attention_id": "other"},
            {"admission_global_attention_id": "original"},
        ),
        ({"attention_id": 2}, {"admission_atencion_id": 1}),
    ],
)
def test_link_guard_rejects_missing_or_other_attention(attention, previous):
    from receipt_ars_correction import correct_linked_insurer

    with pytest.raises(ValueError):
        correct_linked_insurer(None, previous, attention, "HUMANO", {})


def test_legacy_source_identity_is_required():
    from receipt_ars_correction import _require_matching_identity

    previous = {"admission_atencion_id": 1, "admission_source_instance_id": "ORIGINAL"}
    with pytest.raises(ValueError, match="origen"):
        _require_matching_identity(
            previous, {"attention_id": 1, "source_instance_id": "OTHER"}
        )
    _require_matching_identity(
        previous, {"attention_id": 1, "source_instance_id": "ORIGINAL"}
    )
