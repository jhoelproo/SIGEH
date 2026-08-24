from __future__ import annotations

import re

from admission_database_import import AdmissionDatabaseImporter
from admission_hybrid import (
    ADMISSION_IMPORT_BATCH_COLUMNS,
    ADMISSION_IMPORT_STAGING_COLUMNS,
    ensure_admission_import_progress_schema,
)


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _LegacyImportSchemaConnection:
    """Records the idempotent PostgreSQL migration over the old table shape."""

    def __init__(self):
        self.batch_columns = {
            "import_batch_id", "source_filename", "source_sha256",
            "legacy_source_instance_id", "imported_by", "imported_at", "mode",
            "status", "totals_json", "applied_at",
        }
        self.staging_columns = {"import_batch_id", "row_number"}
        self.historical_batch = {
            "import_batch_id": "batch-old", "status": "COMPLETED",
            "imported_at": "2026-08-01T10:00:00Z", "applied_at": "2026-08-01T11:00:00Z",
            "totals_json": {"records": 7},
        }
        self.statements: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=()):
        compact = " ".join(sql.split())
        self.statements.append(compact)
        match = re.search(
            r"ALTER TABLE admission_import_(batches|staging) ADD COLUMN IF NOT EXISTS (\w+)",
            compact,
            flags=re.IGNORECASE,
        )
        if match:
            target = self.batch_columns if match.group(1).lower() == "batches" else self.staging_columns
            target.add(match.group(2).lower())
        if compact.startswith("UPDATE admission_import_batches SET"):
            row = self.historical_batch
            row["started_at"] = row["imported_at"]
            row["progress_updated_at"] = row["applied_at"]
            row["completed_at"] = row["applied_at"]
            row["progress_percent"] = 100
            row["processed_records"] = 7
            row["total_records"] = 7
        return _Result()


def test_legacy_import_schema_is_migrated_idempotently_without_losing_batch():
    connection = _LegacyImportSchemaConnection()

    ensure_admission_import_progress_schema(connection)
    first_count = len(connection.statements)
    ensure_admission_import_progress_schema(connection)

    assert ADMISSION_IMPORT_BATCH_COLUMNS <= connection.batch_columns
    assert ADMISSION_IMPORT_STAGING_COLUMNS <= connection.staging_columns
    assert connection.historical_batch["import_batch_id"] == "batch-old"
    assert connection.historical_batch["started_at"] == connection.historical_batch["imported_at"]
    assert connection.historical_batch["completed_at"] == connection.historical_batch["applied_at"]
    assert connection.historical_batch["progress_percent"] == 100
    assert connection.historical_batch["processed_records"] == 7
    assert connection.historical_batch["total_records"] == 7
    assert len(connection.statements) > first_count


def test_importer_migrates_before_its_first_batch_query():
    connection = _LegacyImportSchemaConnection()
    importer = AdmissionDatabaseImporter(lambda: connection)

    assert importer.find_active_task() is None

    first_batch_query = next(
        index for index, sql in enumerate(connection.statements)
        if sql.startswith("SELECT import_batch_id FROM admission_import_batches")
    )
    assert any(
        "ADD COLUMN IF NOT EXISTS started_at" in sql
        for sql in connection.statements[:first_batch_query]
    )
