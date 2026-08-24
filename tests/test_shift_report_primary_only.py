from types import SimpleNamespace

import CALCULOS_QT as app


def _window_for_station(role, local_device="PC-1", primary_device="PC-1", offline=False):
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
    assert app.MainWindow._is_primary_admission_station(
        _window_for_station("PRIMARY", offline=True)
    ) is False


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
