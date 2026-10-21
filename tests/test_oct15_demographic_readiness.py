from unittest.mock import Mock
import pytest
import admission_demographics as demographics


@pytest.mark.parametrize("stale", ["", "not-a-date"])
def test_administrative_edit_uses_authoritative_service_date(monkeypatch, stale):
    row = {
        "global_attention_id": "22222222-2222-4222-8222-222222222222",
        "global_patient_id": "11111111-1111-4111-8111-111111111111",
        "patient_name": "SYNTHETIC",
        "canonical_ars": "FUTURO",
        "nss_snapshot": "001234567",
        "cedula_snapshot": "",
        "service_date": "2026-10-15",
        "service_type": "EMERGENCIA",
        "readiness": "LISTA",
        "coverage_status": "ASEGURADO_VALIDADO",
        "server_revision": 1,
        "operational_session_id": "session",
        "latest_payload_json": {"service_date": stale, "service_type": "EMERGENCIA"},
    }
    connection = Mock()
    demographics._correct_snapshot(
        connection,
        row,
        {
            "global_patient_id": row["global_patient_id"],
            "server_revision": 2,
            "telefono": "8090000000",
        },
        "2026-10-15T12:00:00Z",
    )
    update = connection.execute.call_args_list[0]
    assert update.args[1][8] == "LISTA"
    assert update.args[1][12] == "2026-10-15"


@pytest.mark.parametrize(
    "coverage,ars,nss",
    [
        ("ASEGURADO_VALIDADO", "FUTURO", "00123456"),
        ("SIN_SEGURO_DECLARADO", "SIN SEGURO", "SIN SEGURO"),
        ("ASEGURADO_VALIDADO", "PRIVATE LEGACY", "000123"),
    ],
)
def test_personal_edit_keeps_existing_coverage_and_ready_state(coverage, ars, nss):
    row = {
        "global_attention_id": "22222222-2222-4222-8222-222222222222",
        "global_patient_id": "11111111-1111-4111-8111-111111111111",
        "patient_name": "SYNTHETIC",
        "canonical_ars": ars,
        "nss_snapshot": nss,
        "cedula_snapshot": "",
        "service_date": "2026-10-15",
        "service_type": "EMERGENCIA",
        "readiness": "LISTA",
        "coverage_status": coverage,
        "server_revision": 1,
        "operational_session_id": "session",
        "latest_payload_json": {},
    }
    connection = Mock()
    demographics._correct_snapshot(
        connection,
        row,
        {
            "global_patient_id": row["global_patient_id"],
            "server_revision": 2,
            "nombre": "SYNTHETIC FIX",
        },
        "2026-10-15T12:00:00Z",
    )
    update = connection.execute.call_args_list[0]
    assert update.args[1][8] == "LISTA"
    assert update.args[1][7] == coverage


def test_coverage_change_is_reassessed():
    from admission_contract import assess_coverage

    valid = assess_coverage("FUTURO", "001234567")
    row = {"coverage_status": "ASEGURADO_VALIDADO"}
    result = demographics._preserved_coverage(
        assess_coverage("FUTURO", ""),
        ("FUTURO", "1234567"),
        {"ars": "FUTURO", "nss": ""},
        row,
    )
    assert not result.complete
    assert (
        demographics._preserved_coverage(
            valid, ("FUTURO", "1234567"), {"ars": "FUTURO", "nss": "1234567"}, {}
        )
        == valid
    )


def test_stale_incomplete_coverage_is_repaired_from_current_valid_data():
    from admission_contract import assess_coverage

    coverage = assess_coverage("FUTURO", "000123456")
    result = demographics._preserved_coverage(
        coverage,
        ("FUTURO", "000123456"),
        {"ars": "FUTURO", "nss": "000123456"},
        {"coverage_status": "COBERTURA_INCOMPLETA"},
    )
    assert result.complete
