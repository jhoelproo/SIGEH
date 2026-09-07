from unittest.mock import Mock

import pytest

from turn_excel_delivery import enqueue, jobs, deliver


def test_pending_survives_restart_and_submits_only_once(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    context = {"transition_id": "turn-1", "turn_id": 17}
    enqueue(path, context, 2)
    enqueue(path, context, 2)
    assert len(jobs(path)) == 1
    generate = Mock(return_value="old-turn.xlsx")
    printer = Mock(return_value=True)
    assert deliver(path, "turn-1", generate, printer)
    assert not deliver(path, "turn-1", generate, printer)
    printer.assert_called_once_with("old-turn.xlsx", 2)
    assert jobs(path) == []
    assert len(jobs(path, "SUBMITTED")) == 1


def test_crash_before_generation_leaves_recoverable_job(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    enqueue(path, {"transition_id": "turn-1"}, 1)
    with pytest.raises(RuntimeError):
        deliver(path, "turn-1", Mock(side_effect=RuntimeError()), Mock())
    assert len(jobs(path)) == 1


@pytest.mark.parametrize("raises", [True, False])
def test_uncertain_print_is_not_automatically_duplicated(tmp_path, raises):
    path = tmp_path / "jobs.sqlite3"
    enqueue(path, {"transition_id": "turn-1"}, 1)
    printer = Mock(side_effect=RuntimeError()) if raises else Mock(return_value=False)
    if raises:
        with pytest.raises(RuntimeError):
            deliver(path, "turn-1", lambda _: "list.xlsx", printer)
    else:
        assert not deliver(path, "turn-1", lambda _: "list.xlsx", printer)
    assert len(jobs(path, "SUBMITTING")) == 1
    assert not deliver(path, "turn-1", Mock(), printer)
    assert printer.call_count == 1


def test_empty_turn_and_missing_transition(tmp_path):
    path = tmp_path / "jobs.sqlite3"
    with pytest.raises(ValueError):
        enqueue(path, {}, 1)
    enqueue(path, {"transition_id": "empty"}, 1)
    printer = Mock()
    assert deliver(path, "empty", lambda _: "", printer)
    printer.assert_not_called()
    assert jobs(path, "EMPTY")


def test_print_runs_even_when_shutdown_discards_ui_callback(monkeypatch, tmp_path):
    from datetime import datetime
    from types import SimpleNamespace
    from tests.test_turn_excel_post_commit import _v15_module, _ImmediatePostCommitApp

    v15 = _v15_module()
    monkeypatch.setattr(
        v15, "EXCEL_PRINT_QUEUE_PATH", str(tmp_path / "delivery.sqlite3")
    )
    context = v15.OutgoingTurnContext(
        "source",
        17,
        1,
        1,
        "rep",
        "REPRESENTANTE",
        datetime(2026, 9, 6, 8),
        datetime(2026, 9, 6, 20),
        "8AM_8PM",
        datetime(2026, 9, 6).date(),
        transition_id="transition-17",
        new_turn_id=18,
    )
    current = _ImmediatePostCommitApp(v15)
    current.app_settings["turnos_save_excel_copy"] = False
    current._ejecutar_en_segundo_plano = lambda _, operation, **callbacks: operation()
    monkeypatch.setattr(v15, "build_turn_closure_report_snapshot", Mock())
    generate = Mock(
        return_value=SimpleNamespace(patient_count=1, excel_path="old.xlsx")
    )
    monkeypatch.setattr(v15, "generate_turn_closure_report_files", generate)
    printer = Mock(return_value=True)
    monkeypatch.setattr(v15, "imprimir_excel", printer)
    v15.App._run_turn_post_commit_effects(current, context)
    assert generate.call_args.kwargs["generate_excel"] is True
    printer.assert_called_once_with(
        "old.xlsx", 2, permitir_reintento=False, mostrar_error=False
    )
    assert len(jobs(v15.EXCEL_PRINT_QUEUE_PATH, "SUBMITTED")) == 1


def test_reopened_app_recovers_exact_outgoing_turn(monkeypatch, tmp_path):
    from dataclasses import asdict
    from datetime import datetime
    from types import SimpleNamespace
    from tests.test_turn_excel_post_commit import _v15_module

    v15 = _v15_module()
    path = str(tmp_path / "restart.sqlite3")
    monkeypatch.setattr(v15, "EXCEL_PRINT_QUEUE_PATH", path)
    context = v15.OutgoingTurnContext(
        "source-original",
        17,
        1,
        1,
        "rep",
        "REPRESENTANTE",
        datetime(2026, 9, 6, 8),
        datetime(2026, 9, 6, 20),
        "8AM_8PM",
        datetime(2026, 9, 6).date(),
        transition_id="original",
        new_turn_id=18,
    )
    enqueue(path, asdict(context), 2)
    reopened = SimpleNamespace(_run_turn_post_commit_effects=Mock(), root=Mock())
    v15.App._resume_turn_excel_delivery(reopened)
    reopened._run_turn_post_commit_effects.assert_called_once_with(context)
