"""Keep event lookup indexes without storing duplicate copies of the same keys."""

import re

import pytest

from admission_hybrid import POSTGRES_HYBRID_SCHEMA
from patient_directory import POSTGRES_PATIENT_DIRECTORY_SCHEMA


@pytest.mark.parametrize(
    ("schema", "index_name", "constraint"),
    [
        (
            POSTGRES_HYBRID_SCHEMA,
            "idx_admission_sync_events_cursor",
            "sequence BIGSERIAL PRIMARY KEY",
        ),
        (
            POSTGRES_HYBRID_SCHEMA,
            "idx_admission_sync_events_event_uuid",
            "event_uuid UUID NOT NULL UNIQUE",
        ),
        (
            POSTGRES_PATIENT_DIRECTORY_SCHEMA,
            "idx_admission_patient_directory_events_cursor",
            "sequence BIGSERIAL PRIMARY KEY",
        ),
    ],
    ids=["attention-sequence", "attention-uuid", "patient-sequence"],
)
def test_schema_retains_constraint_instead_of_recreating_duplicate(
    schema, index_name, constraint
):
    assert constraint in schema
    assert not re.search(
        r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+" + index_name + r"\b", schema
    )
