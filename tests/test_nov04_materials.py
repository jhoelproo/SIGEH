import pytest
from billing_material_cart import specialty_materials
from tests.test_billing_material_cart import cart as cart, add, remove, edit, quantity


def test_shared_consumables_once_per_receipt_even_with_multiple_doses(cart):
    add(cart, "MED A", 2)
    add(cart, "MED B", 3)
    assert quantity(cart, "Materiales", "GUANTES") == 1
    assert quantity(cart, "Materiales", "JERINGA") == 1
    remove(cart, "MED A")
    assert quantity(cart, "Materiales", "GUANTES") == 1
    remove(cart, "MED B")
    assert quantity(cart, "Materiales", "GUANTES") == 0


def test_manual_material_prevents_automatic_duplicate_and_survives_removal(cart):
    cart.insert_or_increment_cart_item("Materiales", "GUANTES", 2, 2)
    add(cart, "MED A", 5)
    assert quantity(cart, "Materiales", "GUANTES") == 2
    remove(cart, "MED A")
    assert quantity(cart, "Materiales", "GUANTES") == 2


def test_mask_selection_matches_specialty_without_a_dialog(cart):
    cart.universal["Materiales"].update(
        {"MASCARILLA PEDIATRICA": 10, "MASCARILLA ADULTO": 12}
    )
    cart.material_cart.rules["NEBULIZAR"] = [
        {"name": name, "units": 1, "active": True}
        for name in ("MASCARILLA PEDIATRICA", "MASCARILLA ADULTO")
    ]
    cart.current_admission_attention = {"specialty": "PEDIATRIA"}
    add(cart, "NEBULIZAR", 2)
    assert quantity(cart, "Materiales", "MASCARILLA PEDIATRICA") == 1
    assert quantity(cart, "Materiales", "MASCARILLA ADULTO") == 0


@pytest.mark.parametrize(
    "specialty,expected",
    [
        ("PEDIATRÍA", "MASCARILLA PEDIATRICA"),
        ("GINECOLOGÍA", "MASCARILLA ADULTO"),
        ("GENERAL", "MASCARILLA ADULTO"),
        ("EMERGENCIOLOGIA", "MASCARILLA ADULTO"),
    ],
)
def test_specific_mask_takes_precedence_over_a_generic_mask(specialty, expected):
    materials = [
        {"name": name}
        for name in (
            "MASCARILLA",
            "MASCARILLA PEDIATRICA",
            "MASCARILLA ADULTO",
            "JERINGA",
        )
    ]
    assert [row["name"] for row in specialty_materials(materials, specialty)] == [
        expected,
        "JERINGA",
    ]
    assert specialty_materials([], specialty) == []


def test_manual_mask_or_gloves_of_another_model_prevent_duplicate(cart):
    cart.universal["Materiales"]["MASCARILLA ADULTO"] = 10
    cart.material_cart.rules["MED A"].append(
        {"name": "MASCARILLA ADULTO", "active": True}
    )
    cart.insert_or_increment_cart_item("Materiales", "MASCARILLA PERSONALIZADA", 12, 1)
    cart.insert_or_increment_cart_item("Materiales", "GUANTES ESTÉRILES", 3, 1)
    add(cart, "MED A", 2)
    assert quantity(cart, "Materiales", "MASCARILLA ADULTO") == 0
    assert quantity(cart, "Materiales", "GUANTES") == 0
    remove(cart, "MED A")
    assert quantity(cart, "Materiales", "MASCARILLA PERSONALIZADA") == 1
    assert quantity(cart, "Materiales", "GUANTES ESTÉRILES") == 1


def test_manually_edited_automatic_material_becomes_an_explicit_choice(cart):
    add(cart, "MED A", 2)
    edit(cart, "GUANTES", 4, "Materiales")
    remove(cart, "MED A")
    assert quantity(cart, "Materiales", "GUANTES") == 4


def test_editing_specialty_fallback_and_generic_mask(cart):
    cart._editing_specialty_snapshot = "PEDIATRIA"
    assert cart.material_cart._specialty() == "PEDIATRIA"
    assert specialty_materials([{"name": "MASCARILLA"}], "GENERAL") == [
        {"name": "MASCARILLA"}
    ]


def test_material_removed_outside_the_callback_does_not_leave_stale_ownership(cart):
    add(cart, "MED A", 1)
    cart.cart_table.removeRow(cart.material_cart.row("Materiales", "GUANTES"))
    remove(cart, "MED A")
    assert cart.cart_table.rowCount() == 0
