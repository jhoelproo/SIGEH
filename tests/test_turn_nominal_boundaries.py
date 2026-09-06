from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_v15_adapter import load_v15_application_module
from tests.test_admission_sheet_state import state


@pytest.mark.parametrize(
    "moment",
    [
        "2026-09-04T19:59:00",
        "2026-09-04T20:00:00",
        "2026-09-04T20:01:00",
        "2026-09-05T00:00:00",
    ],
)
@pytest.mark.parametrize("restart", [False, True])
def test_integrated_generation_uses_active_identity_across_clock_and_restart(
    monkeypatch, moment, restart
):
    v15 = load_v15_application_module()
    snapshot = state()
    original = dict(snapshot)
    window = SimpleNamespace(
        db=SimpleNamespace(get_operational_station_snapshot=lambda: snapshot),
        _snapshot_operacional_integrado=lambda: snapshot,
        apply_operational_snapshot=Mock(),
    )
    monkeypatch.setattr(
        v15, "cargar_turno_config", Mock(side_effect=AssertionError("local mirror"))
    )
    if not restart:
        v15.App._generation_turn_config(window)
    config = v15.App._generation_turn_config(window)
    assert v15.turno_config_es_vigente(config, datetime.fromisoformat(moment))
    assert config["turn_id"] == 3949
    assert config["generation"] == 10
    assert snapshot == original


def test_administrative_adapter_requests_allocation_explicitly():
    from tests.test_primary_formal_shift_handover_20260826 import _runtime_for_handover

    runtime = _runtime_for_handover()
    runtime.change_primary_turn(
        999,
        shift_metadata={
            "turno_codigo": "8PM_8AM",
            "administrative_override": True,
            "override_reason": "SYNTHETIC AUDIT",
        },
    )
    assert not runtime.session_service.handover_calls
    (command,) = runtime.session_service.turn_only_calls
    assert command["allocate_central_turn_id"] is True
    assert command["administrative_override"] is True
    assert command["expected_previous_turn_id"] == 500
    assert command["expected_current_representative_id"] == "10"
    assert command["expected_generation"] == 7
    assert command["expected_operational_source_id"] == "source-1"
    assert command["transition_id"]
