from __future__ import annotations

import pytest

import CALCULOS_QT as app


def _fail_if_called(*_args, **_kwargs):
    raise AssertionError("No debe crear ni guardar un reporte vacio")


def test_daily_empty_report_is_skipped_before_file_and_history(monkeypatch):
    monkeypatch.setattr(
        app, "get_receipt_stats_by_date", lambda *_args, **_kwargs: {"_total_recibos": 0}
    )
    monkeypatch.setattr(app, "_create_report_pdf", _fail_if_called)
    monkeypatch.setattr(app, "save_daily_report_record", _fail_if_called)

    with pytest.raises(app.EmptyReportDatasetError):
        app.generate_daily_report_pdf("2026-08-12", "PRUEBA")


def test_period_empty_report_is_skipped_before_file_and_history(monkeypatch):
    monkeypatch.setattr(
        app, "get_receipt_stats_between", lambda *_args, **_kwargs: {"_total_recibos": 0}
    )
    monkeypatch.setattr(app, "_create_report_pdf", _fail_if_called)
    monkeypatch.setattr(app, "save_report_history", _fail_if_called)

    with pytest.raises(app.EmptyReportDatasetError):
        app.generate_period_report_pdf(
            "Periodo", "2026-08-01", "2026-08-12", "PRUEBA"
        )


def test_pending_empty_daily_report_is_not_an_error(monkeypatch):
    monkeypatch.setattr(app, "report_exists", lambda *_args: False)
    monkeypatch.setattr(
        app,
        "generate_daily_report_pdf",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            app.EmptyReportDatasetError("sin registros")
        ),
    )
    monkeypatch.setattr(app, "write_runtime_log", lambda *_args: None)

    assert app.safe_generate_pending_reports("PRUEBA") == (False, False)

