import pytest

from admission_contract import (
    normalize_service_type,
    assess_billing_readiness,
    assess_coverage,
)
from admission_v15_adapter import _HybridDatabaseProxy


@pytest.mark.parametrize("kind", ["URGENCIA", " urgencia ", "URGENCIAS"])
def test_urgency_remains_distinct_after_central_normalization(kind):
    normalized = normalize_service_type(kind)
    assert normalized == "URGENCIA"
    counts = _HybridDatabaseProxy._calculate_turn_counts(
        [{"service_type": normalized, "specialty": "GENERAL", "ars": "HUMANO"}]
    )
    assert counts["URGENCIAS"] == counts["total"] == 1
    assert counts["GENERAL"] == 0
    readiness = assess_billing_readiness(
        name="SYNTHETIC",
        service_date="2026-09-09",
        attention_type=normalized,
        coverage=assess_coverage("HUMANO", "123456789"),
    )
    assert readiness.status != "LISTA"
