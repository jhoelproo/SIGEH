import itertools

import pytest

from billing_admission_edit import (
    AdmissionDataChanged,
    apply_owned_receipt_context,
    validate_admission_snapshot,
)
from billing_field_policy import editable_billing_fields, require_room_price


@pytest.mark.parametrize("amount", ["", "abc", "NaN", "Infinity", -1, 1000001])
def test_invalid_room_price_is_rejected_even_for_admin(amount):
    with pytest.raises(ValueError):
        require_room_price(admin=True, supplied=amount, catalog=0)


@pytest.mark.parametrize("amount", [0, 1, 1000000])
def test_room_price_boundaries(amount):
    require_room_price(admin=True, supplied=amount, catalog=0)


def test_auxiliary_unverified_header_can_only_remain_unchanged():
    from billing_field_policy import require_validated_header_edit

    values = dict(nombre="TEST", dx="TEST", fecha="2026-09-05", ars="APS", sala=460)
    require_validated_header_edit(
        auxiliary=True, validated=False, supplied=values, previous=values
    )
    require_validated_header_edit(
        auxiliary=True, validated=True, supplied=values, previous={}
    )


@pytest.mark.parametrize(
    "admin,auxiliary,validated,read_only", itertools.product([False, True], repeat=4)
)
def test_field_ownership_matrix(admin, auxiliary, validated, read_only):
    result = editable_billing_fields(
        admin=admin, auxiliary=auxiliary, validated=validated, read_only=read_only
    )
    assert result["sala_spin"] == (admin and not read_only)
    for field in ("name_edit", "date_edit", "ars_combo", "coverage_combo"):
        assert result[field] == (not read_only and not auxiliary and not validated)
    assert result["dx_edit"] == (not read_only and (validated or not auxiliary))


@pytest.mark.parametrize(
    "admin,price,existing,allowed",
    [
        (True, 999, None, True),
        (False, 460, None, True),
        (False, 461, None, False),
        (False, 0, None, False),
        (False, 500, 500, True),
        (False, 460, 500, False),
    ],
)
def test_room_price(admin, price, existing, allowed):
    if allowed:
        require_room_price(admin=admin, supplied=price, catalog=460, existing=existing)
    else:
        with pytest.raises(PermissionError, match="ADMIN"):
            require_room_price(
                admin=admin, supplied=price, catalog=460, existing=existing
            )


@pytest.mark.parametrize(
    "receipt_id,origin,state,inherited",
    [
        (1, 5, "HEREDADA_PROCESADA", True),
        (2, 5, "HEREDADA_PROCESADA", False),
        (None, 5, "HEREDADA_PROCESADA", False),
        (1, 6, "HEREDADA_PROCESADA", False),
        (1, 5, "TURNO_ACTUAL_PROCESADA", False),
    ],
)
def test_only_centrally_matched_own_receipt_keeps_inheritance(
    receipt_id, origin, state, inherited
):
    row = dict(
        linked_receipt_id=1,
        turn_id=5,
        receipt_origin_turn=origin,
        receipt_inheritance_state=state,
    )
    result = apply_owned_receipt_context(row, receipt_id)
    assert bool(result.get("explicitly_inherited")) == inherited
    assert (result["linked_receipt_id"] is None) == (receipt_id == 1)
    assert row["linked_receipt_id"] == 1


def test_snapshot_aliases_and_absent_fields():
    validate_admission_snapshot(
        {"name": "  TEST  ", "ars": "APS"},
        {"patient_name": "Test", "canonical_ars": "APS"},
    )
    validate_admission_snapshot({}, {})
    validate_admission_snapshot({"nss_clean": ""}, {"nss_snapshot": None})


@pytest.mark.parametrize(
    "snapshot,projection,label",
    [
        ({"service_date": "2026-09-04"}, {"service_date": "2026-09-05"}, "fecha"),
        ({"name": "OLD"}, {"patient_name": "NEW"}, "nombre"),
        ({"ars": "APS"}, {"canonical_ars": "FUTURO"}, "ARS"),
        ({"nss_clean": "1"}, {"nss_snapshot": "2"}, "NSS"),
        ({"cedula_clean": "1"}, {"cedula_snapshot": "2"}, "cédula"),
        (
            {"attention_type": "EMERGENCIA"},
            {"service_type": "URGENCIA"},
            "tipo de atención",
        ),
    ],
)
def test_snapshot_difference_is_explicit_without_patient_values(
    snapshot, projection, label
):
    with pytest.raises(AdmissionDataChanged) as caught:
        validate_admission_snapshot(snapshot, projection)
    assert caught.value.fields == (label,)
