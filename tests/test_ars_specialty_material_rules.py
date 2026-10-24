"""ARS specialty restrictions and proportional material allocations."""

import pytest

from billing_item_rules import (
    MaterialAllocation,
    honorarium_allowed,
    normalize_honorarium_policy,
    normalize_material_quantities,
)


@pytest.mark.parametrize(
    "specialty", ["GENERAL", "EMERGENCIOLOGÍA", "", "PEDIATRIA", "GINECOLOGÍA"]
)
def test_unconfigured_ars_preserves_existing_charges(specialty):
    assert honorarium_allowed({}, specialty)


def test_specialty_policy_and_general_alias():
    policy = normalize_honorarium_policy(
        {"GENERAL": False, "pediatria": True, "ginecologia": False}
    )
    assert not honorarium_allowed(policy, "emergenciologia")
    assert honorarium_allowed(policy, "PEDIATRÍA")
    assert not honorarium_allowed(policy, "Ginecología")


@pytest.mark.parametrize(
    "policy",
    [
        {"CARDIOLOGIA": True},
        {"GENERAL": "false"},
        {"GENERAL": False, "EMERGENCIOLOGÍA": True},
        [],
    ],
)
def test_invalid_policies_are_rejected(policy):
    with pytest.raises(ValueError):
        normalize_honorarium_policy(policy)


@pytest.mark.parametrize(
    "value",
    [
        {1: 0},
        {0: 1},
        {1: -1},
        {1: 1.5},
        {True: 1},
        {1: True},
        {1: 1001},
        {"not-id": 1},
        [],
    ],
)
def test_invalid_material_quantities_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_material_quantities(value)


def test_material_configuration_accepts_empty_and_integer_boundaries():
    assert normalize_material_quantities({}) == {}
    assert normalize_material_quantities({"1": 1, 2: 1000}) == {1: 1, 2: 1000}


def test_allocations_scale_increment_decrement_and_remove_without_duplication():
    allocation = MaterialAllocation({"JERINGA": 2, "GUANTES": 1})
    assert allocation.change(3) == {"JERINGA": 6, "GUANTES": 3}
    assert allocation.change(3) == {}
    assert allocation.change(5) == {"JERINGA": 4, "GUANTES": 2}
    assert allocation.change(1) == {"JERINGA": -8, "GUANTES": -4}
    assert allocation.change(0) == {"JERINGA": -2, "GUANTES": -1}


def test_historical_medication_quantity_is_not_retroactively_repriced():
    allocation = MaterialAllocation({"JERINGA": 2}, baseline=4)
    assert allocation.change(4) == {}
    assert allocation.change(6) == {"JERINGA": 4}
    assert allocation.change(2) == {"JERINGA": -4}
    assert allocation.change(0) == {}


@pytest.mark.parametrize("quantity", [-1, 1.5, True, "not-a-quantity"])
def test_invalid_allocation_changes_do_not_modify_state(quantity):
    allocation = MaterialAllocation({"JERINGA": 2})
    with pytest.raises(ValueError):
        allocation.change(quantity)
    assert allocation.change(1) == {"JERINGA": 2}


def test_duplicate_ars_identity_rejected_and_null_policy_snapshot_supported():
    from billing_item_rules import normalize_ars_policies, honorarium_policy_snapshot

    assert honorarium_policy_snapshot({"honorarium_specialties": None}) == {}
    assert normalize_ars_policies({"1": {"GENERAL": False}}) == {
        1: {"EMERGENCIOLOGÍA": False}
    }
    with pytest.raises(ValueError, match="duplicada"):
        normalize_ars_policies({1: {}, "1": {}})
