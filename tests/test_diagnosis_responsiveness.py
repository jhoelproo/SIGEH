"""Unreviewed history stays out of completion and bulk loading yields to Qt."""

from types import SimpleNamespace
from unittest.mock import Mock
from threading import Event
from contextlib import contextmanager

import pytest
from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication, QLineEdit, QWidget

import diagnosis_recommendation_admin as admin
import diagnosis_recommendation_ui as ui
from diagnosis_recommendations import DiagnosisCatalog, normalize_config

REAL_RELOAD = admin.RecommendationAdminDialog.reload


DIRTY_HISTORY = (
    "CEFALEA /PB MIGRAÑA",
    "CEFALEA DE O/I /GASTROENTERITIS AGUDA /P/B /PARASITOSIS",
    "CEFALEA DE ORIGEN INVESTIGAR",
    "CEFALEA INTENSA VS TORTICOLIS",
    "CEFALEA INTENSIONAL/ MAREOS",
    "CEFALEA INTENZA",
    "CEFALEA INTESA",
    "CEFALEAM",
)


def test_unreviewed_incomplete_or_misspelled_history_is_never_a_suggestion():
    catalog = DiagnosisCatalog({}, DIRTY_HISTORY)
    assert set(catalog.suggestions("CEFALEA")) == {
        "CEFALEA",
        "CEFALEA MIGRAÑOSA",
        "CEFALEA TENSIONAL",
        "CEFALEA INTENSA",
        "CEFALEA DE ORIGEN A INVESTIGAR",
    }
    assert "CEFALEAM" not in catalog.suggestions("CEFALEAM")
    assert not catalog.suggestions("PARASITOSIS")


def test_admin_approved_custom_diagnosis_and_alias_remain_selectable():
    catalog = DiagnosisCatalog(
        {"diagnoses": [{"name": "DIAGNÓSTICO VALIDADO", "aliases": ["DV"]}]},
        ["DIAGNÓSTICO SIN REVISAR"],
    )
    assert catalog.suggestions("diagnostico") == ["DIAGNÓSTICO VALIDADO"]
    assert catalog.suggestions("DV") == ["DIAGNÓSTICO VALIDADO"]


@pytest.mark.parametrize("label", [*DIRTY_HISTORY, "CEFALEA M", "CEFALEA DE"])
def test_malformed_legacy_configuration_does_not_become_a_suggestion(label):
    catalog = DiagnosisCatalog({"diagnoses": [{"name": label}]})
    assert label not in catalog.suggestions("CEFALEA")


def test_approved_grade_and_distinct_hypertensive_subtypes_remain_available():
    catalog = DiagnosisCatalog({"diagnoses": [{"name": "FRACTURA GRADO I"}]})
    assert catalog.suggestions("FRACTURA GRADO") == ["FRACTURA GRADO I"]
    assert set(catalog.suggestions("CRISIS HIPERTENSIVA")) == {
        "CRISIS HIPERTENSIVA",
        "CRISIS HIPERTENSIVA TIPO URGENCIA",
        "CRISIS HIPERTENSIVA TIPO EMERGENCIA",
    }


def test_completion_cache_is_bounded_and_results_cannot_mutate_cached_choices():
    catalog = DiagnosisCatalog({})
    original = catalog.suggestions("CEFALEA")
    original.clear()
    assert catalog.suggestions("CEFALEA")
    for index in range(140):
        catalog.suggestions(f"NO COINCIDE {index}")
    assert len(catalog._suggestion_cache) <= 128
    assert catalog.suggestions("CEFALEA")


@pytest.fixture
def controller(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(
        ui,
        "local_receipt_store",
        lambda: SimpleNamespace(cached=lambda _key, default: default),
    )
    window = QWidget()
    window.dx_edit = QLineEdit(window)
    window.universal, window.ars_cache = {}, {}
    window.refresh_picker = Mock()
    backend = SimpleNamespace(write_runtime_log=Mock(), db_pool=None)
    runtime = ui.RecommendationController(window, backend)
    window.show()
    window.dx_edit.setFocus()
    application.processEvents()
    yield runtime, window
    runtime.stop()
    window.close()
    window.deleteLater()
    application.processEvents()


def test_typing_and_repainting_reuse_one_catalog_per_model(controller, monkeypatch):
    runtime, window = controller
    factory = Mock(wraps=ui.model_catalog)
    monkeypatch.setattr(ui, "model_catalog", factory)
    runtime.model = {"config": normalize_config({}), "groups": {}, "cursor": 2}
    for prefix in ("CE", "CEF", "CEFA", "CEFALEA"):
        window.dx_edit.setText(prefix)
        runtime.completion.update()
        runtime.prepare_labels()
    assert factory.call_count == 1
    runtime.model = {**runtime.model, "cursor": 3}
    runtime.completion.update()
    assert factory.call_count == 2


def bulk_data(count):
    return {
        "config": {**normalize_config({}), "version": 1},
        "items": [],
        "groups": {
            str(index): {"diagnosis": f"DIAGNÓSTICO QA {index}", "items": []}
            for index in range(count)
        },
    }


@pytest.fixture
def dialog(monkeypatch):
    application = QApplication.instance() or QApplication([])
    monkeypatch.setattr(admin.RecommendationAdminDialog, "reload", lambda self: None)
    editor = admin.RecommendationAdminDialog(
        SimpleNamespace(user_is_admin=lambda _actor: True), {"role": "admin"}, {}
    )
    yield editor
    editor.close()
    editor.deleteLater()
    application.processEvents()


def test_bulk_cells_do_not_refilter_all_rows_for_every_insert(dialog, monkeypatch):
    filter_rows = Mock(wraps=dialog._filter_table)
    monkeypatch.setattr(dialog, "_filter_table", filter_rows)
    dialog._loaded(bulk_data(60))
    for _ in range(100):
        QApplication.processEvents()
    assert dialog.diagnoses.rowCount() == 60
    assert filter_rows.call_count <= 3


def test_large_load_yields_before_finishing_and_can_be_cancelled(dialog):
    heartbeat = []
    dialog._loaded(bulk_data(800))
    QTimer.singleShot(0, lambda: heartbeat.append(dialog.diagnoses.rowCount()))
    QApplication.processEvents()
    assert heartbeat and heartbeat[0] < 800
    dialog.reject()
    before = dialog.diagnoses.rowCount()
    for _ in range(20):
        QApplication.processEvents()
    assert dialog.diagnoses.rowCount() == before


def test_cancelling_a_read_does_not_wait_on_the_gui_thread(dialog):
    worker = Mock()
    dialog.worker, dialog._operation = worker, "load"
    dialog.reject()
    worker.requestInterruption.assert_called_once()
    worker.setParent.assert_called_once_with(None)
    worker.wait.assert_not_called()
    worker.cancel.assert_not_called()
    dialog._loaded(bulk_data(10))
    assert dialog.diagnoses.rowCount() == 0
    dialog._finished()
    dialog._failed(RuntimeError("Late result after closing"))


def test_search_keeps_a_cached_index_and_invalidates_it_after_an_edit(
    dialog, monkeypatch
):
    dialog._loaded(bulk_data(10))
    read_cell = Mock(wraps=dialog._search_cell)
    monkeypatch.setattr(dialog, "_search_cell", read_cell)
    dialog.search.setText("QA")
    dialog.search.setText("QA 1")
    read_cell.assert_not_called()
    dialog.diagnoses.item(1, 0).setText("DIAGNÓSTICO ACTUALIZADO")
    assert read_cell.called
    dialog.search.setText("ACTUALIZADO")
    assert not dialog.diagnoses.isRowHidden(1)


def test_search_entered_during_loading_is_applied_once_all_rows_are_ready(dialog):
    dialog._loaded(bulk_data(80))
    dialog.search.setText("QA 79")
    dialog.save()
    assert dialog.worker is None
    for _ in range(100):
        QApplication.processEvents()
    assert dialog.diagnoses.rowCount() == 80
    assert sum(not dialog.diagnoses.isRowHidden(row) for row in range(80)) == 1
    assert len(dialog.configuration()["diagnoses"]) == 80
    assert dialog.save_button.isEnabled()


def connect_loader(dialog, callback):
    worker = ui.RecommendationTask(callback, dialog)
    dialog.worker, dialog._operation = worker, "load"
    worker.completed.connect(dialog._loaded)
    worker.failed.connect(dialog._failed)
    worker.finished.connect(dialog._finished)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker


def test_admin_preparation_runs_outside_gui_thread(dialog):
    thread_checks = []

    def prepare():
        thread_checks.append(
            QThread.currentThread() == QApplication.instance().thread()
        )
        return admin.prepare_admin_data(bulk_data(1))

    worker = connect_loader(dialog, prepare)
    assert worker.wait(2000)
    QApplication.processEvents()
    assert thread_checks == [False]
    assert dialog.diagnoses.rowCount() == 1


def test_closing_a_real_background_load_discards_late_results(dialog):
    started, release = Event(), Event()

    def prepare():
        started.set()
        assert release.wait(2)
        return admin.prepare_admin_data(bulk_data(1))

    worker = connect_loader(dialog, prepare)
    assert started.wait(2)
    dialog.reject()
    assert worker.isRunning()
    release.set()
    assert worker.wait(2000)
    QApplication.processEvents()
    assert dialog.diagnoses.rowCount() == 0


def mock_connection(dialog, rows=()):
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = list(rows)

    @contextmanager
    def connect():
        yield connection

    dialog.backend.db_connect = connect
    return connection


def test_real_reload_fetches_and_prepares_in_background_once(dialog, monkeypatch):
    group = {"diagnosis": "DIAGNÓSTICO QA", "items": []}
    mock_connection(dialog, [{"change_key": "group:qa", "payload": group}])
    configuration = {**normalize_config({}), "version": 1}
    monkeypatch.setattr(admin, "load_config", lambda _connection: configuration)
    lookup = Mock(return_value=[])
    monkeypatch.setattr(admin, "admin_catalog", lookup)
    prepare = admin.prepare_admin_data
    gui_threads = []

    def checked_prepare(data):
        gui_threads.append(QThread.currentThread() == QApplication.instance().thread())
        return prepare(data)

    monkeypatch.setattr(admin, "prepare_admin_data", checked_prepare)
    REAL_RELOAD(dialog)
    worker = dialog.worker
    REAL_RELOAD(dialog)
    assert worker.wait(2000)
    QApplication.processEvents()
    lookup.assert_called_once()
    assert gui_threads == [False]
    assert dialog.model["groups"] == {"qa": group}
    assert dialog.diagnoses.rowCount() == 1
    assert dialog.worker is None and dialog.reload_button.isEnabled()


def test_save_keeps_existing_validation_and_waits_for_background_success(
    dialog, monkeypatch
):
    dialog._loaded(bulk_data(1))
    mock_connection(dialog)
    dialog.backend._audit_billing_item_rule = Mock()
    save = Mock(return_value=2)
    monkeypatch.setattr(admin, "save_config", save)
    dialog.save()
    worker = dialog.worker
    dialog.reject()
    assert not dialog._closing
    assert worker.wait(2000)
    QApplication.processEvents()
    assert dialog.saved and dialog.version == 2
    assert save.call_args.args[2] == dialog.actor
    assert len(save.call_args.args[1]["diagnoses"]) == 1


def test_failed_reload_retains_rows_and_restores_controls(dialog, monkeypatch):
    dialog._loaded(bulk_data(2))
    original = dialog.configuration()

    dialog.backend.db_connect = Mock(
        side_effect=OSError("Synthetic connection unavailable")
    )
    warning = Mock()
    monkeypatch.setattr(admin.QMessageBox, "warning", warning)
    REAL_RELOAD(dialog)
    assert dialog.worker.wait(2000)
    QApplication.processEvents()
    assert dialog.configuration() == original
    assert dialog.save_button.isEnabled()
    warning.assert_called_once()


def test_gui_time_budget_can_yield_after_one_row(dialog, monkeypatch):
    clock = iter((0.0, 0.020))
    monkeypatch.setattr(admin, "perf_counter", lambda: next(clock))
    dialog._loaded(bulk_data(3))
    assert dialog.diagnoses.rowCount() == 1
    assert dialog.population_timer.isActive()
    monkeypatch.setattr(admin, "perf_counter", lambda: 0.0)
    dialog._populate_next()
    assert dialog.diagnoses.rowCount() == 3
    assert not dialog.population_timer.isActive()


def test_empty_admin_snapshot_is_ready_for_manual_configuration(dialog):
    dialog._loaded(bulk_data(0))
    assert dialog.search_results.text() == "0 de 0"
    assert not dialog._populating
    assert not dialog.population_timer.isActive()
    assert dialog.save_button.isEnabled()
    assert dialog.configuration()["diagnoses"] == []


def test_window_close_cancels_pending_batches_and_ignores_queued_timer(dialog):
    dialog._loaded(bulk_data(80))
    event = QCloseEvent()
    dialog.closeEvent(event)
    assert event.isAccepted()
    before = dialog.diagnoses.rowCount()
    dialog._populate_next()
    assert dialog.diagnoses.rowCount() == before


def test_logout_cancels_loading_without_waiting_for_io(dialog):
    worker = Mock()
    dialog.worker, dialog._operation = worker, "load"
    dialog.shutdown_for_logout()
    worker.requestInterruption.assert_called_once()
    worker.wait.assert_not_called()
