from types import SimpleNamespace

import CALCULOS_QT as app


def _window_for_station(
    role, local_device="PC-1", primary_device="PC-1", offline=False
):
    context = SimpleNamespace(
        configuration={
            "admission_hybrid": {
                "role": role,
                "local_device_id": local_device,
                "primary_device_id": primary_device,
                "offline": offline,
            }
        }
    )
    return SimpleNamespace(
        emergency_workspace=SimpleNamespace(admission_context=context)
    )


def test_shift_report_accepts_only_online_primary_device():
    primary = _window_for_station("PRIMARY")
    secondary = _window_for_station("SECONDARY", "PC-2", "PC-1")

    assert app.MainWindow._is_primary_admission_station(primary) is True
    assert app.MainWindow._is_primary_admission_station(secondary) is False
    assert (
        app.MainWindow._is_primary_admission_station(
            _window_for_station("PRIMARY", offline=True)
        )
        is False
    )


def test_shift_closure_schema_has_unique_durable_event_identity():
    assert "event_uuid TEXT NOT NULL UNIQUE" in app.SCHEMA


def test_existing_shift_report_is_not_generated_or_opened_twice(monkeypatch):
    opened = []
    results = []
    monkeypatch.setattr(
        app,
        "get_shift_closure",
        lambda _source, _turn: {"report_filename": "already.pdf"},
    )
    worker = app.ShiftClosureReportWorker(
        "source", 316, "admin", lambda *_args: opened.append(True)
    )
    worker.completed.connect(results.append)

    worker.run()

    assert results[0]["status"] == "ALREADY_GENERATED"
    assert opened == []


def test_empty_shift_finishes_without_pdf_open_or_print(monkeypatch):
    opened = []
    skipped = []
    results = []
    closure = {"source_instance_id": "source", "turn_id": 316}
    monkeypatch.setattr(app, "get_shift_closure", lambda *_args: None)
    monkeypatch.setattr(app, "claim_shift_closure", lambda *_args: closure)
    monkeypatch.setattr(
        app,
        "build_shift_closure_report_data",
        lambda _closure: {"details": []},
    )
    monkeypatch.setattr(
        app, "mark_shift_report_skipped_empty", lambda value: skipped.append(value)
    )
    worker = app.ShiftClosureReportWorker(
        "source", 316, "admin", lambda *_args: opened.append(True)
    )
    worker.completed.connect(results.append)

    worker.run()

    assert results[0]["status"] == "SKIPPED_EMPTY"
    assert skipped == [closure]
    assert opened == []


def test_billing_shift_report_generates_and_opens_exactly_once(monkeypatch):
    closure = {
        "source_instance_id": "source-old",
        "turn_id": 410,
        "operational_date": "2026-09-02",
        "closed_by": "admin",
    }
    opened = []
    generated = []
    marked_opened = []
    completed = []
    monkeypatch.setattr(app, "get_shift_closure", lambda *_args: None)
    monkeypatch.setattr(app, "claim_shift_closure", lambda *_args: closure)
    monkeypatch.setattr(
        app,
        "build_shift_closure_report_data",
        lambda _closure: {"details": [{"receipt_id": 1}]},
    )
    monkeypatch.setattr(app, "save_shift_report_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        app,
        "resolve_report_document",
        lambda *_args, **_kwargs: "C:/reports/billing-old-turn.pdf",
    )
    monkeypatch.setattr(
        app,
        "mark_shift_report_generated",
        lambda *args: generated.append(args),
    )
    monkeypatch.setattr(
        app,
        "mark_shift_report_opened",
        lambda *args: marked_opened.append(args),
    )
    monkeypatch.setattr(app, "log_action", lambda *_args, **_kwargs: None)
    worker = app.ShiftClosureReportWorker(
        "source-old",
        410,
        "admin",
        lambda current, path: opened.append((current, path)),
    )
    worker.completed.connect(completed.append)

    worker.run()

    assert completed[0]["status"] == "GENERATED"
    assert completed[0]["closure"] is closure
    assert len(generated) == 1
    assert marked_opened == [(closure,)]
    assert opened == [(closure, "C:/reports/billing-old-turn.pdf")]


def test_billing_report_open_failure_preserves_generated_result(monkeypatch):
    closure = {
        "source_instance_id": "source-old",
        "turn_id": 410,
        "operational_date": "2026-09-02",
        "closed_by": "admin",
    }
    generated = []
    opened_state = []
    completed = []
    failed = []
    monkeypatch.setattr(app, "get_shift_closure", lambda *_args: None)
    monkeypatch.setattr(app, "claim_shift_closure", lambda *_args: closure)
    monkeypatch.setattr(
        app,
        "build_shift_closure_report_data",
        lambda _closure: {"details": [{"receipt_id": 1}]},
    )
    monkeypatch.setattr(app, "save_shift_report_snapshot", lambda *_args: None)
    monkeypatch.setattr(
        app,
        "resolve_report_document",
        lambda *_args, **_kwargs: "C:/reports/billing-old-turn.pdf",
    )
    monkeypatch.setattr(
        app,
        "mark_shift_report_generated",
        lambda *args: generated.append(args),
    )
    monkeypatch.setattr(
        app,
        "mark_shift_report_opened",
        lambda *args: opened_state.append(args),
    )
    monkeypatch.setattr(app, "log_action", lambda *_args, **_kwargs: None)
    worker = app.ShiftClosureReportWorker(
        "source-old",
        410,
        "admin",
        lambda *_args: (_ for _ in ()).throw(OSError("viewer unavailable")),
    )
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)

    worker.run()

    assert len(generated) == 1
    assert completed[0]["status"] == "GENERATED_OPEN_FAILED"
    assert completed[0]["path"] == "C:/reports/billing-old-turn.pdf"
    assert failed == []
    assert opened_state == [(closure, "viewer unavailable")]


def test_billing_report_failure_records_the_correct_generation_stage(monkeypatch):
    closure = {"source_instance_id": "source-old", "turn_id": 410}
    generation_errors = []
    opening_errors = []
    monkeypatch.setattr(
        app,
        "mark_shift_report_error",
        lambda current, error: generation_errors.append((current, error)),
    )
    monkeypatch.setattr(
        app,
        "mark_shift_report_opened",
        lambda current, error: opening_errors.append((current, error)),
    )

    app.ShiftClosureReportWorker._record_generation_failure(
        None, False, RuntimeError("before claim")
    )
    app.ShiftClosureReportWorker._record_generation_failure(
        closure, False, RuntimeError("generation failed")
    )
    app.ShiftClosureReportWorker._record_generation_failure(
        closure, True, RuntimeError("delivery failed")
    )

    assert generation_errors == [(closure, "generation failed")]
    assert opening_errors == [(closure, "delivery failed")]


def test_billing_open_failure_completion_is_visible_without_worker_failure(
    monkeypatch,
):
    messages = []

    class Toast:
        def __init__(self, message, _parent):
            messages.append(message)

        def show(self):
            return None

    monkeypatch.setattr(app, "FloatingToast", Toast)
    window = SimpleNamespace(cierre_facturacion_en_progreso=True)

    app.MainWindow._shift_report_completed(window, {"status": "GENERATED_OPEN_FAILED"})

    assert window.cierre_facturacion_en_progreso is False
    assert messages == ["Reporte de Facturación generado; apertura pendiente."]
    app.MainWindow._shift_report_completed(window, {"status": "SKIPPED_EMPTY"})
    assert "no se generó reporte" in messages[-1]
