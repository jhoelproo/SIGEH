from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from admission_turn_history_dialog import AdmissionTurnHistoryDialog


@pytest.fixture
def dialog():
    application = QApplication.instance() or QApplication([])
    controller = SimpleNamespace(db=Mock(), _ejecutar_en_segundo_plano=Mock())
    window = AdmissionTurnHistoryDialog(controller, Mock())
    yield window
    if isValid(window):
        window.close()
    application.processEvents()


def rows(count):
    return [
        dict(
            turn_id=index,
            started_at="2026-09-18T08:00:00",
            ends_at=None,
            username="original",
            operational_source_id="source",
            display_name="Admisor original",
            patient_count=2,
        )
        for index in range(count)
    ]


def test_loading_blocks_duplicate_search_and_selection(dialog):
    dialog.restart()
    dialog.restart()
    dialog.next_page()
    dialog.choose()
    assert dialog.controller._ejecutar_en_segundo_plano.call_count == 1
    dialog.selected.assert_not_called()
    assert not dialog.user.isEnabled()
    job = dialog.controller._ejecutar_en_segundo_plano.call_args.args[1]
    job()
    dialog.controller.db.search_admission_turns.assert_called_once()
    dialog.loaded(rows(51))
    assert dialog.table.rowCount() == 50
    assert dialog.next.isEnabled()
    assert not dialog.previous.isEnabled()
    dialog.next_page()
    assert dialog.controller._ejecutar_en_segundo_plano.call_count == 2
    dialog.loaded(rows(1))
    assert not dialog.next.isEnabled()
    assert dialog.previous.isEnabled()
    dialog.choose()
    dialog.selected.assert_called_once_with(rows(1)[0])


def test_previous_page_returns_to_first_cursor(dialog):
    dialog.loaded(rows(51))
    dialog.next_page()
    dialog.loaded(rows(2))
    dialog.previous_page()
    job = dialog.controller._ejecutar_en_segundo_plano.call_args.args[1]
    job()
    assert dialog.controller.db.search_admission_turns.call_args.args[-1] is None
    assert dialog.page_index == 0


def test_filter_changes_clear_old_page_and_cursor(dialog):
    dialog.loaded(rows(51))
    dialog.user.setText("Otro usuario")
    assert dialog.rows == []
    assert dialog.next_cursor is None
    assert not dialog.next.isEnabled()
    assert not dialog.use.isEnabled()
    dialog.choose()
    dialog.selected.assert_not_called()


def test_empty_and_error_states_can_be_retried(dialog):
    dialog.restart()
    dialog.failed(ValueError("Rango inválido"))
    assert dialog.search.isEnabled()
    assert "Rango inválido" in dialog.status.text()
    assert not dialog.use.isEnabled()
    dialog.restart()
    dialog.loaded([])
    assert dialog.table.rowCount() == 0
    assert dialog.search.isEnabled()
    assert not dialog.use.isEnabled()


def test_late_callbacks_after_close_are_ignored(dialog):
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    dialog.loaded(rows(1))
    dialog.failed(RuntimeError("Late failure"))
