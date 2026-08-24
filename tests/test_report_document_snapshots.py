from report_documents import (
    build_report_snapshot,
    calculate_report_snapshot_hash,
    source_key,
)
from pathlib import Path


def _snapshot(dataset):
    return build_report_snapshot(
        source_table="report_history",
        source_key_value="25",
        report_id=25,
        report_type="Mensual",
        report_title="Reporte mensual",
        period_start="2026-07-01",
        period_end="2026-07-31",
        generated_at="2026-07-28T12:00:00+00:00",
        generated_by="usuario",
        filters={"ars": ["ARS A"]},
        financial_basis={"state": "FACTURADO"},
        dataset=dataset,
        summary={"total": 100},
        charts={},
        guided_reading={},
        render_context={"title": "Reporte mensual", "logo_path": "local.png"},
    )


def test_report_snapshot_hash_is_stable_and_ignores_local_logo_path():
    first = _snapshot({"rows": [{"id": 1, "total": 100}]})
    second = _snapshot({"rows": [{"total": 100, "id": 1}]})
    assert first["render_context"].get("logo_path") is None
    assert calculate_report_snapshot_hash(first) == calculate_report_snapshot_hash(
        second
    )


def test_report_snapshot_hash_preserves_dataset_order():
    first = _snapshot({"rows": [{"id": 1}, {"id": 2}]})
    second = _snapshot({"rows": [{"id": 2}, {"id": 1}]})
    assert calculate_report_snapshot_hash(first) != calculate_report_snapshot_hash(
        second
    )


def test_report_source_keys_are_unambiguous():
    assert source_key("report_history", {"id": 18}) == "18"
    assert (
        source_key(
            "billing_shift_closures",
            {"source_instance_id": "PC-A", "turn_id": 7},
        )
        == "PC-A|7"
    )


def test_runtime_document_flows_do_not_insert_pdf_binaries():
    root = Path(__file__).resolve().parents[1]
    for filename in (
        "CALCULOS_QT.py",
        "receipt_documents.py",
        "receipt_document_migration.py",
        "report_documents.py",
    ):
        source = (root / filename).read_text(encoding="utf-8").upper()
        assert "INSERT INTO PDF_STORAGE" not in source
