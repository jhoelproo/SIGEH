import pytest
from unittest.mock import Mock

from patient_directory import CentralPatientDirectoryRepository
from tests.test_patient_edit_all_roles_v110 import (
    _central_patient_row,
    _PatientConnection,
)


@pytest.mark.parametrize(
    "changes",
    [
        {"phone": "8095559999"},
        {"address": "NUEVA"},
        {"cedula": "001-0000000-1", "phone": "8095559999"},
    ],
)
def test_existing_document_conflict_does_not_block_unrelated_correction(changes):
    connection = _PatientConnection(_central_patient_row(), duplicate=True)
    repository = CentralPatientDirectoryRepository(lambda: connection)
    result = repository.update_patient(
        connection.row["global_patient_id"],
        changes,
        expected_revision=3,
        actor_user="admin",
        actor_role="admin",
    )
    assert result["server_revision"] == 4
    assert connection.update_count == 1
    assert len(connection.events) == 1


@pytest.mark.parametrize("field", ["cedula", "nss"])
def test_only_modified_identity_participates_in_conflict_query(field):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = None
    current = {"cedula": "00100000001", "nss": "123456"}
    desired = {key + "_normalized": value for key, value in current.items()}
    desired[field + "_normalized"] = "987654321"
    CentralPatientDirectoryRepository._validate_changed_documents(
        connection, "synthetic-id", current, desired
    )
    query = connection.execute.call_args.args
    assert "987654321" in query[1]
    assert current["nss" if field == "cedula" else "cedula"] not in query[1]


def test_removing_identity_does_not_claim_another_patient():
    connection = Mock()
    CentralPatientDirectoryRepository._validate_changed_documents(
        connection,
        "synthetic-id",
        {"cedula": "123", "nss": "456"},
        {"cedula_normalized": "", "nss_normalized": ""},
    )
    connection.execute.assert_not_called()
