import pytest

from billing_closure_categories import (
    classify_closure_attention,
    closure_category_counts,
)


@pytest.mark.parametrize("authorized", [False, True])
@pytest.mark.parametrize(
    "inherited,origin,previous,prefix",
    [
        (False, 3, 2, ""),
        (True, 2, 2, "HEREDADA"),
        (True, 1, 2, "HISTÓRICA"),
        (True, 1, None, "HISTÓRICA"),
    ],
)
def test_classification_uses_immediately_previous_turn(
    authorized, inherited, origin, previous, prefix
):
    result = classify_closure_attention(
        authorized=authorized,
        inherited=inherited,
        origin_turn=origin,
        previous_turn=previous,
    )
    expected = (
        f"{prefix} {'AUTORIZADA' if authorized else 'PENDIENTE'}"
        if inherited
        else ("AUTORIZADA" if authorized else "PENDIENTE DE AUTORIZACIÓN")
    )
    assert result == expected


def test_separate_categories_reconcile_to_total():
    result = closure_category_counts(
        [
            "AUTORIZADA",
            "PENDIENTE DE AUTORIZACIÓN",
            "HEREDADA AUTORIZADA",
            "HEREDADA PENDIENTE",
            "HISTÓRICA AUTORIZADA",
            "HISTÓRICA PENDIENTE",
        ]
    )
    assert result["inherited_received"] == result["historical_received"] == 2
    assert result["historical_authorized"] == result["historical_pending"] == 1
    assert result["worked_applicable"] == 6
    assert result["pending_next"] == result["authorized_applicable"] == 3
    assert result["authorization_rate"] == 50


def test_empty_closure_has_zero_rate():
    assert set(closure_category_counts([]).values()) == {0}
