from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

import CALCULOS_QT as app
import report_documents as documents


def _snapshot(*, mode="standard", dataset=None, context=None):
    base_context = {
        "mode": mode,
        "title": "Reporte de prueba",
        "subtitle": "Período controlado",
        "generated_by": "AUDITOR",
        "generated_at": "2026-08-24T10:00:00+00:00",
    }
    base_context.update(context or {})
    return documents.build_report_snapshot(
        source_table="report_history",
        source_key_value="71",
        report_id=71,
        report_type="Mensual",
        report_title="Reporte de prueba",
        period_start="2026-08-01",
        period_end="2026-08-24",
        generated_at="2026-08-24T10:00:00+00:00",
        generated_by="AUDITOR",
        generated_by_user_id="user-71",
        created_from_module="test",
        filters={"ars": {"mode": "include", "values": ["ARS A"]}},
        financial_basis={"status": "FACTURADO"},
        dataset=dataset or {"_total_recibos": 1, "Total General": 125.5},
        summary={"total": 125.5},
        charts={"series": [{"label": "ARS A", "total": 125.5}]},
        guided_reading={"text": "Datos congelados"},
        render_context=base_context,
    )


def _document(snapshot):
    return {
        "source_table": "report_history",
        "source_key": "71",
        "version": 1,
        "snapshot": snapshot,
        "snapshot_hash": documents.calculate_report_snapshot_hash(snapshot),
    }


class _Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class _SnapshotConnection:
    def __init__(self, current=None, document_row=None):
        self.current = current
        self.document_row = document_row
        self.calls = []

    def execute(self, statement, params=()):
        statement = " ".join(str(statement).split())
        self.calls.append((statement, params))
        if statement.startswith("SELECT id,version,snapshot_hash"):
            return _Result(self.current)
        if statement.startswith("SELECT * FROM report_document_versions"):
            return _Result(self.document_row)
        if statement.startswith("INSERT INTO report_document_versions"):
            return _Result({"id": 901})
        return _Result()


def _save_snapshot(connection, **overrides):
    args = {
        "source_table": "report_history",
        "source_key_value": "71",
        "report_id": 71,
        "report_type": "Mensual",
        "report_title": "Reporte de prueba",
        "period_start": "2026-08-01",
        "period_end": "2026-08-24",
        "generated_at": "2026-08-24T10:00:00+00:00",
        "generated_by": "AUDITOR",
        "generated_by_user_id": "user-71",
        "created_from_module": "test",
        "filters": {"ars": ["ARS A"]},
        "financial_basis": {"status": "FACTURADO"},
        "dataset": {"_total_recibos": 1, "Total General": 125.5},
        "summary": {"total": 125.5},
        "charts": {},
        "guided_reading": {},
        "render_context": {"mode": "standard", "title": "Reporte de prueba"},
    }
    args.update(overrides)
    return documents.save_report_document_snapshot(connection, **args)


def test_v2_snapshot_keeps_one_dataset_and_rebuilds_standard_render_context(tmp_path, monkeypatch):
    snapshot = _snapshot(
        context={
            "totals": {"should": "not persist here"},
            "category_rows": [("CONSULTA", 125.5)],
            "ars_rows": [("ARS A", 1)],
            "user_rows": [("AUDITOR", 1)],
            "logo_path": "C:/local/logo.png",
        }
    )
    assert "totals" not in snapshot["render_context"]
    assert "logo_path" not in snapshot["render_context"]
    assert snapshot["identity"]["report_uuid"]
    assert snapshot["identity"]["generated_by_user_id"] == "user-71"

    rendered = {}

    def render_pdf(_self, context, output_path, **_kwargs):
        rendered.update(context)
        with open(output_path, "wb") as target:
            target.write(b"%PDF-test")
        return output_path

    monkeypatch.setattr(documents, "report_cache_root", lambda: tmp_path)
    monkeypatch.setattr(documents.ReportHTMLRenderer, "render_pdf", render_pdf)
    path = documents.render_report_snapshot_pdf(_document(snapshot))
    assert path.endswith(".pdf")
    assert rendered["totals"]["Total General"] == 125.5
    assert rendered["category_rows"] == [["CONSULTA", 125.5]]


def test_v1_snapshot_keeps_its_existing_render_context_for_legacy_compatibility():
    snapshot = _snapshot(mode="comparison", dataset={"summary": {"receipts": 1}})
    snapshot["schema_version"] = 1
    snapshot["render_context"]["data"] = {"summary": {"receipts": 9}}
    context = documents._render_context_from_snapshot(snapshot)
    assert context["data"]["summary"]["receipts"] == 9


def test_historical_excel_is_built_from_snapshot_without_querying_current_data(tmp_path):
    snapshot = _snapshot()
    output = tmp_path / "historico.xlsx"
    documents.export_report_snapshot_xlsx(_document(snapshot), str(output))
    assert output.is_file()
    from openpyxl import load_workbook

    workbook = load_workbook(output, read_only=True, data_only=True)
    assert workbook["Resumen"]["A1"].value == "Reporte de prueba"
    assert workbook["Datos históricos"]["B2"].value in (1, 125.5)


def test_period_generation_persists_snapshot_before_materializing_pdf(monkeypatch):
    calls = []
    totals = {"_total_recibos": 1, "Total General": 50.0}
    monkeypatch.setattr(app, "get_receipt_stats_between", lambda *_args, **_kwargs: totals)
    monkeypatch.setattr(app, "save_report_history", lambda *args, **kwargs: calls.append(("save", args, kwargs)) or 73)
    monkeypatch.setattr(app, "resolve_report_document", lambda *args, **kwargs: calls.append(("render", args, kwargs)) or "C:/tmp/report.pdf")
    monkeypatch.setattr(app, "_create_report_pdf", lambda *_args, **_kwargs: pytest.fail("must not render before snapshot"))

    assert app.generate_period_report_pdf("Mensual", "2026-08-01", "2026-08-24", "AUDITOR") == "C:/tmp/report.pdf"
    assert [item[0] for item in calls] == ["save", "render"]
    assert calls[0][1][4] is totals
    assert calls[0][2]["snapshot_payload"]["filters"]["period_type"] == "Mensual"
    assert calls[0][2]["snapshot_payload"]["query"]["analysis_type"]
    assert calls[1][1][:3] == ("report_history", "73", "generate")


def test_snapshot_mode_never_falls_back_to_legacy_binary(monkeypatch):
    @contextmanager
    def connect():
        yield object()

    source = {
        "id": 71,
        "source_key": "71",
        "filename": "old.pdf",
        "document_storage_mode": app.REPORT_STORAGE_SNAPSHOT,
        "report_type": "Mensual",
        "start_date": "2026-08-01",
        "end_date": "2026-08-24",
        "generated_at": "2026-08-24T10:00:00",
        "generated_by": "AUDITOR",
        "totals_json": "{}",
    }
    monkeypatch.setattr(app, "db_connect", connect)
    monkeypatch.setattr(app, "_report_source_row", lambda *_args: source)
    monkeypatch.setattr(
        app, "load_current_report_snapshot",
        lambda *_args: (_ for _ in ()).throw(documents.ReportSnapshotMissingError("missing")),
    )
    monkeypatch.setattr(
        app, "load_latest_report_snapshot",
        lambda *_args: (_ for _ in ()).throw(documents.ReportSnapshotMissingError("missing")),
    )
    monkeypatch.setattr(app, "find_external_document", lambda *_args, **_kwargs: pytest.fail("legacy fallback"))
    with pytest.raises(documents.ReportSnapshotMissingError):
        app.resolve_report_document("report_history", "71", "open")


def test_snapshot_save_is_idempotent_and_marks_the_source_snapshot_mode():
    created_connection = _SnapshotConnection()
    created = _save_snapshot(created_connection)
    assert created["created"] is True
    assert created["version"] == 1
    assert any("INSERT INTO report_document_versions" in call[0] for call in created_connection.calls)
    assert any("UPDATE report_history SET document_storage_mode" in call[0] for call in created_connection.calls)

    existing_connection = _SnapshotConnection(
        current={
            "id": created["id"],
            "version": created["version"],
            "snapshot_hash": created["snapshot_hash"],
        }
    )
    existing = _save_snapshot(existing_connection)
    assert existing["created"] is False
    assert existing["id"] == created["id"]
    assert not any("INSERT INTO report_document_versions" in call[0] for call in existing_connection.calls)


def test_snapshot_loader_checks_hash_template_and_supported_contract():
    snapshot = _snapshot()
    document = _document(snapshot)
    document["template_version"] = documents.REPORT_TEMPLATE_VERSION
    connection = _SnapshotConnection(document_row=document)
    assert documents.load_current_report_snapshot(connection, "report_history", "71")["snapshot"] == snapshot

    bad_hash = dict(document)
    bad_hash["snapshot_hash"] = "0" * 64
    with pytest.raises(documents.ReportSnapshotHashError):
        documents.load_latest_report_snapshot(
            _SnapshotConnection(document_row=bad_hash), "report_history", "71"
        )

    unsupported = _snapshot()
    unsupported["schema_version"] = 99
    unsupported_document = _document(unsupported)
    unsupported_document["template_version"] = documents.REPORT_TEMPLATE_VERSION
    with pytest.raises(documents.ReportTemplateError):
        documents.load_current_report_snapshot(
            _SnapshotConnection(document_row=unsupported_document), "report_history", "71"
        )


def test_snapshot_json_normalizes_values_and_reuses_cached_render(tmp_path, monkeypatch):
    payload = {
        "money": Decimal("12.50"),
        "date": date(2026, 8, 24),
        "stamp": datetime(2026, 8, 24, 9, 30, tzinfo=timezone.utc),
        "bytes": b"historico",
    }
    assert documents._json_safe(payload) == {
        "money": 12.5,
        "date": "2026-08-24",
        "stamp": "2026-08-24T09:30:00+00:00",
        "bytes": "historico",
    }
    calls = []

    def render_pdf(_self, _context, output_path, **_kwargs):
        calls.append(output_path)
        with open(output_path, "wb") as target:
            target.write(b"%PDF-cache")
        return output_path

    snapshot = _snapshot()
    monkeypatch.setattr(documents, "report_cache_root", lambda: tmp_path)
    monkeypatch.setattr(documents.ReportHTMLRenderer, "render_pdf", render_pdf)
    first = documents.render_report_snapshot_pdf(_document(snapshot))
    second = documents.render_report_snapshot_pdf(_document(snapshot))
    assert first == second
    assert len(calls) == 1


def test_deleted_snapshot_cache_is_rebuilt_from_the_same_immutable_data(tmp_path, monkeypatch):
    calls = []

    def render_pdf(_self, _context, output_path, **_kwargs):
        calls.append(output_path)
        with open(output_path, "wb") as target:
            target.write(b"%PDF-rebuild")
        return output_path

    snapshot = _snapshot()
    monkeypatch.setattr(documents, "report_cache_root", lambda: tmp_path)
    monkeypatch.setattr(documents.ReportHTMLRenderer, "render_pdf", render_pdf)
    document = _document(snapshot)
    first = documents.render_report_snapshot_pdf(document)
    Path(first).unlink()
    rebuilt = documents.render_report_snapshot_pdf(document)
    assert rebuilt == first
    assert Path(rebuilt).is_file()
    assert len(calls) == 2


def test_panel_excel_export_uses_snapshot_dataset_only(tmp_path, monkeypatch):
    calls = []

    def panel_export(data, destination, generated_by, logo_path):
        calls.append((data, destination, generated_by, logo_path))
        with open(destination, "wb") as target:
            target.write(b"xlsx")
        return destination

    import report_engine.excel_exporter as exporter

    monkeypatch.setattr(exporter, "export_panel_xlsx", panel_export)
    snapshot = _snapshot(mode="panel", dataset={"summary": {"receipts": 2}})
    path = documents.export_report_snapshot_xlsx(
        _document(snapshot), str(tmp_path / "panel.xlsx"), "logo.png"
    )
    assert path.endswith("panel.xlsx")
    assert calls[0][0] == {"summary": {"receipts": 2}}
    assert calls[0][2] == "AUDITOR"


def test_snapshot_contract_rejects_empty_invalid_and_incompatible_records():
    with pytest.raises(documents.ReportSnapshotMissingError):
        documents._load_document_record(None, latest=False)
    with pytest.raises(documents.ReportSnapshotMissingError):
        documents._snapshot_from_record({"snapshot": "[]"})

    invalid = _snapshot()
    invalid["identity"] = {}
    with pytest.raises(documents.ReportDocumentError):
        documents._validate_snapshot(invalid)

    template_mismatch = _document(_snapshot())
    template_mismatch["template_version"] = "removed_template"
    with pytest.raises(documents.ReportTemplateError):
        documents._load_document_record(template_mismatch, latest=False)


def test_snapshot_helpers_cover_all_source_types_and_version_rollover():
    assert "UPDATE daily_reports" in documents._source_update_sql("daily_reports")
    assert "UPDATE billing_shift_closures" in documents._source_update_sql(
        "billing_shift_closures"
    )
    with pytest.raises(ValueError):
        documents._source_update_sql("other")
    assert documents._source_update_params("daily_reports", "15", "SNAPSHOT", 2) == (
        "SNAPSHOT",
        2,
        15,
    )
    assert documents._source_update_params(
        "billing_shift_closures", "device-7|9", "SNAPSHOT", 3
    ) == ("SNAPSHOT", 3, "device-7", 9)

    current = {"id": 89, "version": 1, "snapshot_hash": "f" * 64}
    connection = _SnapshotConnection(current=current)
    result = _save_snapshot(connection)
    assert result["created"] is True
    assert result["version"] == 2
    assert any(
        statement.startswith("UPDATE report_document_versions SET is_current=FALSE")
        for statement, _params in connection.calls
    )


def test_migration_and_safe_archive_root_helpers(monkeypatch, tmp_path):
    class MigrationConnection:
        def __init__(self):
            self.sql = ""

        def executescript(self, sql):
            self.sql = sql

    connection = MigrationConnection()
    documents.apply_report_document_migration(connection)
    assert "report_document_versions" in connection.sql
    monkeypatch.setenv("DOCUMENT_ARCHIVE_ROOT", str(tmp_path / "archive"))
    assert documents.default_archive_root() == (tmp_path / "archive").resolve()
    monkeypatch.delenv("DOCUMENT_ARCHIVE_ROOT")
    monkeypatch.setenv("OneDrive", str(tmp_path / "OneDrive"))
    assert documents.default_archive_root() == (tmp_path / "OneDrive" / "HospitalDocumentArchive").resolve()
    assert documents._spreadsheet_value("=bad") == "'=bad"
    assert documents._spreadsheet_value("normal") == "normal"
    assert documents._json_safe(object()).startswith("<object object at")


def test_render_removes_a_failed_temporary_file(tmp_path, monkeypatch):
    snapshot = _snapshot()

    def render_empty(_self, _context, output_path, **_kwargs):
        with open(output_path, "wb"):
            pass
        return output_path

    monkeypatch.setattr(documents, "report_cache_root", lambda: tmp_path)
    monkeypatch.setattr(documents.ReportHTMLRenderer, "render_pdf", render_empty)
    with pytest.raises(documents.ReportDocumentError):
        documents.render_report_snapshot_pdf(_document(snapshot))
    assert not list(tmp_path.glob("*.tmp.pdf"))


def test_daily_report_saves_its_snapshot_before_materializing(monkeypatch):
    calls = []
    totals = {"_total_recibos": 1, "Total General": 50.0}
    monkeypatch.setattr(app, "get_receipt_stats_by_date", lambda *_args, **_kwargs: totals)
    monkeypatch.setattr(
        app,
        "save_daily_report_record",
        lambda *args, **kwargs: calls.append(("save", args, kwargs)) or 74,
    )
    monkeypatch.setattr(
        app,
        "resolve_report_document",
        lambda *args, **kwargs: calls.append(("render", args, kwargs)) or "C:/tmp/daily.pdf",
    )
    assert app.generate_daily_report_pdf(
        "2026-08-24", "AUDITOR", generated_by_user_id="user-71"
    ) == "C:/tmp/daily.pdf"
    assert [kind for kind, _args, _kwargs in calls] == ["save", "render"]
    assert calls[0][2]["snapshot_payload"]["filters"]["period_type"] == "Diario"
    assert calls[1][1][:3] == ("daily_reports", "74", "generate")


def test_comparison_report_saves_exact_panel_snapshot_before_rendering(monkeypatch):
    calls = []
    data = {
        "filters": {"compare_previous": True},
        "previous": {"receipts": 1},
        "period": {"period_label": "Agosto", "comparison_label": "Julio"},
        "start_date": "2026-08-01",
        "end_date": "2026-08-24",
        "previous_start": "2026-07-01",
        "previous_end": "2026-07-24",
    }
    monkeypatch.setattr(
        app,
        "save_report_history",
        lambda *args, **kwargs: calls.append(("save", args, kwargs)) or 75,
    )
    monkeypatch.setattr(
        app,
        "resolve_report_document",
        lambda *args, **kwargs: calls.append(("render", args, kwargs)) or "C:/tmp/comparison.pdf",
    )
    assert app.generate_comparison_report_pdf(data, "AUDITOR", "user-71") == "C:/tmp/comparison.pdf"
    assert [kind for kind, _args, _kwargs in calls] == ["save", "render"]
    assert calls[0][1][4] == data
    assert calls[0][2]["created_from_module"] == "reporte_comparativo"


def test_shift_closure_snapshot_has_its_own_immutable_source(monkeypatch):
    captured = {}

    @contextmanager
    def connect():
        yield object()

    closure = {
        "source_instance_id": "device-7",
        "turn_id": 9,
        "operational_date": "2026-08-24",
    }
    monkeypatch.setattr(app, "db_connect", connect)
    monkeypatch.setattr(app, "shift_closure_report_type", lambda _closure: "Cierre")
    monkeypatch.setattr(
        app,
        "_save_report_snapshot_for_source",
        lambda _con, **kwargs: captured.update(kwargs),
    )
    app.save_shift_report_snapshot(
        closure,
        {"details": [{"receipt": "frozen"}]},
        {"mode": "shift_closure", "generated_by": "AUDITOR"},
        "2026-08-24T10:00:00+00:00",
    )
    assert captured["source_table"] == "billing_shift_closures"
    assert captured["source_key"] == "device-7|9"
    assert captured["created_from_module"] == "cierre_turno"


def test_offline_history_open_uses_only_a_valid_existing_snapshot_cache(tmp_path, monkeypatch):
    @contextmanager
    def unavailable_connection():
        raise ConnectionError("offline")
        yield  # pragma: no cover

    cache = tmp_path / "report_history_71_v1_deadbeefcafe.pdf"
    cache.write_bytes(b"%PDF-offline-cache")
    monkeypatch.setattr(app, "db_connect", unavailable_connection)
    monkeypatch.setattr(app, "report_cache_root", lambda: tmp_path)
    assert app.resolve_report_document("report_history", "71", "open") == str(cache)
    with pytest.raises(documents.ReportDocumentError, match="No hay conexión"):
        app.resolve_report_document("report_history", "72", "open")
