import unittest
from contextlib import contextmanager
from unittest.mock import patch

import CALCULOS_QT as app
from receipt_documents import (
    RECEIPT_DOCUMENT_MIGRATION_SQL,
    SnapshotHashError,
    calculate_snapshot_hash,
    canonical_snapshot_json,
)


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self, row):
        self.row = row

    def execute(self, _sql, _params=()):
        return _Cursor(self.row)


class ReceiptDocumentSnapshotTests(unittest.TestCase):
    def test_canonical_hash_is_stable_but_preserves_item_order(self):
        first = {"header": {"receipt_number": 10}, "items": [{"order": 1}, {"order": 2}]}
        same = {"items": [{"order": 1}, {"order": 2}], "header": {"receipt_number": 10}}
        reordered = {"header": {"receipt_number": 10}, "items": [{"order": 2}, {"order": 1}]}
        self.assertEqual(canonical_snapshot_json(first), canonical_snapshot_json(same))
        self.assertEqual(calculate_snapshot_hash(first), calculate_snapshot_hash(same))
        self.assertNotEqual(calculate_snapshot_hash(first), calculate_snapshot_hash(reordered))

    def test_migration_keeps_one_current_version_and_no_binary_column(self):
        sql = RECEIPT_DOCUMENT_MIGRATION_SQL
        self.assertIn("snapshot_jsonb JSONB NOT NULL", sql)
        self.assertIn("WHERE is_current=TRUE", sql)
        self.assertIn("document_storage_mode", sql)
        self.assertNotIn("file_data", sql)

    def test_hybrid_uses_legacy_pdf_when_snapshot_integrity_fails(self):
        row = {
            "id": 25,
            "numero": 99,
            "document_storage_mode": app.STORAGE_HYBRID,
            "pdf_filename": "recibo_99.pdf",
        }

        @contextmanager
        def fake_connect():
            yield _Connection(row)

        with (
            patch.object(app, "db_connect", fake_connect),
            patch.object(
                app,
                "load_current_receipt_snapshot",
                side_effect=SnapshotHashError("hash inválido"),
            ),
            patch.object(app, "_legacy_receipt_pdf_path", return_value="legacy.pdf"),
            patch.object(app, "write_runtime_log"),
        ):
            self.assertEqual(app.resolve_receipt_document_path(25), "legacy.pdf")

    def test_snapshot_mode_does_not_hide_integrity_failure(self):
        row = {
            "id": 26,
            "numero": 100,
            "document_storage_mode": app.STORAGE_SNAPSHOT,
            "pdf_filename": "",
        }

        @contextmanager
        def fake_connect():
            yield _Connection(row)

        with (
            patch.object(app, "db_connect", fake_connect),
            patch.object(
                app,
                "load_current_receipt_snapshot",
                side_effect=SnapshotHashError("hash inválido"),
            ),
            patch.object(app, "write_runtime_log"),
        ):
            with self.assertRaises(SnapshotHashError):
                app.resolve_receipt_document_path(26)


if __name__ == "__main__":
    unittest.main()
