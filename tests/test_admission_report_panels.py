from unittest.mock import Mock

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from admission_report_favorites import ReportFavoriteStore
from tests.test_turn_history_report_flow import (
    historical_report as historical_report,
    wait_for,
    workspace as workspace,
)
from tests.test_admission_statistical_reports_v106 import _row


def evidence(row, **overrides):
    return dict(
        row,
        readiness="LISTA",
        receipt_id=9,
        numero=99,
        estado_facturacion="PENDIENTE",
        dx="CEFALEA",
        numero_autorizacion="0012",
        **overrides,
    )


def load_patients(report, monkeypatch):
    panels = report.controls["panels"]
    monkeypatch.setattr(
        type(report.controller.db),
        "report_billing_followup",
        lambda _self, _records: [evidence(_row(1))],
    )
    panels.setCurrentIndex(1)
    panels.load_followup()
    wait_for(lambda: panels.patients.rowCount() == 2)
    return panels


def test_followup_filters_and_opens_existing_receipt(historical_report, monkeypatch):
    panels = load_patients(historical_report, monkeypatch)
    opened = Mock()
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "open_report_receipt",
        lambda _self, receipt: opened(receipt),
    )
    panels.patients.selectRow(0)
    panels.receipt.click()
    opened.assert_called_once_with(9)
    panels.state.setCurrentText("Sin confirmar")
    assert panels.patients.rowCount() == 1
    assert not panels.receipt.isEnabled()
    panels.search.setText("nobody")
    wait_for(lambda: panels.patients.rowCount() == 0)
    assert "0 paciente" in panels.patient_status.text()


def test_followup_reuses_evidence_when_searching_or_changing_report_filters(
    historical_report, monkeypatch
):
    panels = load_patients(historical_report, monkeypatch)
    lookup = Mock(return_value=[])
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "report_billing_followup",
        lambda _self, rows: lookup(rows),
    )
    panels.search.setText("paciente")
    QTest.qWait(180)
    lookup.assert_not_called()
    historical_report.controls["coverage_var"].set("SIN SEGURO")
    wait_for(lambda: historical_report.controls["state"]["dataset"].records == ())
    lookup.assert_not_called()


def test_followup_failure_is_retryable(historical_report, monkeypatch):
    def failure(_self, _records):
        raise RuntimeError("Sin conexión")

    monkeypatch.setattr(
        type(historical_report.controller.db), "report_billing_followup", failure
    )
    panels = historical_report.controls["panels"]
    panels.setCurrentIndex(1)
    wait_for(lambda: "Sin conexión" in panels.patient_status.text())
    assert panels.refresh.isEnabled()
    load_patients(historical_report, monkeypatch)
    panels.refresh.click()
    wait_for(lambda: panels.patients.rowCount() == 2)


def test_correction_resolves_global_identity_and_checks_permission(
    historical_report, monkeypatch
):
    panels = load_patients(historical_report, monkeypatch)
    resolver, editor = Mock(return_value={"id": 700}), Mock()
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "get_attention_by_global_id",
        lambda _self, identity, **kw: resolver(identity, **kw),
        raising=False,
    )
    monkeypatch.setattr(historical_report.controller, "_abrir_editor_atencion", editor)
    panels.patients.selectRow(0)
    panels.edit.click()
    wait_for(lambda: editor.called)
    resolver.assert_called_once_with(
        _row(1)["global_attention_id"], force_central=True, include_deleted=False
    )
    assert editor.call_args.args == (700,)
    assert callable(editor.call_args.kwargs["on_saved"])
    monkeypatch.setattr(
        historical_report.controller, "_exigir_permiso", lambda *_: False
    )
    panels.edit_attention()
    assert resolver.call_count == 1


def test_compare_uses_same_ars_filter_for_reference(historical_report, monkeypatch):
    controls, panels = historical_report.controls, historical_report.controls["panels"]
    controls["ars_mode"].set("Incluir seleccionadas")
    controls["ars_vars"]["HUMANO"].set(True)
    wait_for(lambda: len(controls["state"]["dataset"].records) == 1)
    turn = dict(
        controls["state"]["dataset"].turns[0],
        turn_id=315,
        started_at="2026-08-26T08:00:00-04:00",
        ends_at="2026-08-27T08:00:00-04:00",
    )
    source = dict(
        selected_turn=turn,
        records=[_row(3, turn_id=315), _row(4, turn_id=315, ars="APS")],
    )
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "load_historical_turn_report",
        lambda _self, _turn: source,
    )
    panels.select_reference(turn)
    wait_for(lambda: panels.comparison.rowCount() > 0)
    assert panels.comparison.item(0, 1).text() == "1"
    assert panels.comparison.item(0, 2).text() == "1"
    assert "Mismos filtros" in panels.comparison_status.text()


def test_previous_next_navigation_and_boundary(historical_report, monkeypatch):
    controls = historical_report.controls
    current = dict(controls["state"]["dataset"].turns[0])
    previous = dict(current, turn_id=315)
    calls = Mock(side_effect=[previous, None])
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "adjacent_report_turn",
        lambda _self, *args: calls(*args),
    )
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "load_historical_turn_report",
        lambda _self, turn: dict(
            selected_turn=turn,
            turns=[turn],
            turn_id=turn["turn_id"],
            records=[_row(1, turn_id=turn["turn_id"])],
        ),
    )
    controls["previous_turn"].click()
    wait_for(lambda: controls["state"]["dataset"].filters.turn_id == 315)
    assert calls.call_args.args[1] == "previous"
    controls["next_turn"].click()
    wait_for(
        lambda: (
            not controls["next_turn"].isEnabled()
            and controls["previous_turn"].isEnabled()
        )
    )
    assert controls["state"]["dataset"].filters.turn_id == 315


def test_select_visible_ars_and_remove_selection(historical_report):
    controls = historical_report.controls
    controls["ars_mode"].set("Incluir seleccionadas")
    controls["ars_vars"]["ARS HISTÓRICA"].set(True)
    controls["ars_search"].setText("humano")
    controls["select_all_ars"].click()
    assert controls["ars_vars"]["HUMANO"].get()
    controls["select_none_ars"].click()
    assert not any(variable.get() for variable in controls["ars_vars"].values())


def test_comparison_can_be_selected_from_history(historical_report, monkeypatch):
    from admission_turn_history_dialog import AdmissionTurnHistoryDialog

    panels = historical_report.controls["panels"]
    turn = dict(panels.dataset.turns[0], turn_id=310)
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "load_historical_turn_report",
        lambda _self, _turn: dict(selected_turn=turn, records=[_row(3, turn_id=310)]),
    )
    panels.open_comparison_history()
    history = panels.findChild(AdmissionTurnHistoryDialog)
    assert history.isVisible()
    history.loaded([turn])
    history.use.click()
    wait_for(lambda: panels.comparison.rowCount() > 0)
    assert "310" in panels.comparison_status.text()


def test_view_menu_saves_applies_and_deletes(historical_report, tmp_path, monkeypatch):
    menu = historical_report.controls["views_button"].menu()
    menu.store = ReportFavoriteStore(
        "test", QSettings(str(tmp_path / "views.ini"), QSettings.IniFormat)
    )
    monkeypatch.setattr(
        "admission_report_panels.QInputDialog.getText", lambda *_: ("Pediatría", True)
    )
    historical_report.controls["specialty_var"].set("PEDIATRIA")
    menu.save_view()
    assert menu.store.list()[0]["specialty"] == "PEDIATRIA"
    historical_report.controls["specialty_var"].set("GENERAL")
    menu.populate()
    next(action for action in menu.actions() if action.text() == "Pediatría").trigger()
    assert historical_report.controls["specialty_var"].get() == "PEDIATRIA"
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    menu.remove_view("Pediatría")
    assert menu.store.list() == []


def test_compact_patients_view_keeps_rows_and_actions_visible(
    historical_report, monkeypatch
):
    historical_report.dialog.resize(760, 580)
    panels = load_patients(historical_report, monkeypatch)
    QTest.qWait(30)
    assert not historical_report.controls["filter_panel"].isVisible()
    assert panels.patients.height() >= 150
    assert panels.patients.horizontalHeaderItem(1).text() == "Facturación"
    for button in (panels.edit, panels.receipt, panels.refresh):
        assert historical_report.dialog.rect().contains(
            button.mapTo(historical_report.dialog, button.rect().bottomRight())
        )


def test_view_menu_cancel_replace_and_storage_errors(
    historical_report, tmp_path, monkeypatch
):
    menu = historical_report.controls["views_button"].menu()
    menu.store = ReportFavoriteStore(
        "test", QSettings(str(tmp_path / "cancel.ini"), QSettings.IniFormat)
    )
    warning = Mock()
    monkeypatch.setattr(QMessageBox, "warning", warning)
    monkeypatch.setattr(
        "admission_report_panels.QInputDialog.getText", lambda *_: ("", False)
    )
    menu.save_view()
    assert menu.store.list() == []
    menu.store.save({"name": "Existing"})
    monkeypatch.setattr(
        "admission_report_panels.QInputDialog.getText", lambda *_: ("Existing", True)
    )
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.No)
    menu.save_view()
    menu.remove_view("Existing")
    assert len(menu.store.list()) == 1
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.Yes)
    menu.save_view()
    menu.store.settings.setValue(menu.store.key, "broken")
    menu.populate()
    menu.save_view()
    menu.remove_view("Existing")
    assert warning.call_count == 2


def test_pending_followup_callbacks_are_discarded_after_filter_changes(
    historical_report, monkeypatch
):
    panels = historical_report.controls["panels"]
    jobs = []
    monkeypatch.setattr(
        historical_report.controller,
        "_ejecutar_en_segundo_plano",
        lambda _label, work, done, failed: jobs.append((done, failed)),
    )
    panels.setCurrentIndex(1)
    panels.load_followup()
    assert len(jobs) == 1
    panels.invalidate()
    jobs[0][0]([evidence(_row(1))])
    jobs[0][1](RuntimeError("Old read"))
    assert panels.rows == []
    panels.select_reference({})
    panels.open_comparison_history()


def test_opening_multiple_receipts_keeps_patient_identity(
    historical_report, monkeypatch
):
    panels = load_patients(historical_report, monkeypatch)
    monkeypatch.setattr(
        type(historical_report.controller.db),
        "report_billing_followup",
        lambda _self, _: [
            evidence(_row(1)),
            dict(evidence(_row(1)), receipt_id=10, numero=100),
        ],
    )
    panels.refresh.click()
    wait_for(lambda: panels.rows and len(panels.rows[0]["receipts"]) == 2)
    chooser = Mock()
    monkeypatch.setattr("admission_receipt_dialog.choose_linked_receipt", chooser)
    panels.open_receipt()
    chooser.assert_not_called()
    panels.patients.selectRow(0)
    panels.open_receipt()
    assert [receipt["id"] for receipt in chooser.call_args.args[0]] == [9, 10]


def test_correction_rechecks_permission_and_handles_deleted_or_unavailable_identity(
    historical_report, monkeypatch
):
    panels = load_patients(historical_report, monkeypatch)
    jobs, editor = [], Mock()
    monkeypatch.setattr(
        historical_report.controller,
        "_ejecutar_en_segundo_plano",
        lambda _label, work, done, failed: jobs.append((done, failed)),
    )
    monkeypatch.setattr(historical_report.controller, "_abrir_editor_atencion", editor)
    info, warning = Mock(), Mock()
    monkeypatch.setattr(QMessageBox, "information", info)
    monkeypatch.setattr(QMessageBox, "warning", warning)
    panels.patients.selectRow(0)
    panels.edit_attention()
    jobs[0][0]({"id": 7, "is_deleted": True})
    assert info.called
    jobs[0][1](RuntimeError("Unavailable"))
    assert warning.called
    panels.edit_attention()
    monkeypatch.setattr(
        historical_report.controller, "_exigir_permiso", lambda *_: False
    )
    jobs[1][0]({"id": 7})
    editor.assert_not_called()


def test_comparison_rejects_same_shift_and_handles_errors(
    historical_report, monkeypatch
):
    panels = historical_report.controls["panels"]
    turn = dict(panels.dataset.turns[0])
    panels.select_reference(turn)
    assert "diferente" in panels.comparison_status.text()
    jobs = []
    monkeypatch.setattr(
        historical_report.controller,
        "_ejecutar_en_segundo_plano",
        lambda _label, work, done, failed: jobs.append((done, failed)),
    )
    panels.select_reference(dict(turn, turn_id=315))
    jobs[0][1](RuntimeError("Offline"))
    assert "Offline" in panels.comparison_status.text()
    panels.select_reference(dict(turn, turn_id=314))
    panels.invalidate()
    jobs[1][0]({})
    jobs[1][1](RuntimeError("Old request"))
    assert panels.comparison.rowCount() == 0


def test_automatic_filters_require_an_ars_selection(historical_report):
    controls = historical_report.controls
    controls["ars_mode"].set("Incluir seleccionadas")
    wait_for(lambda: "Selecciona al menos una ARS" in controls["status"].text())
    assert not controls["pdf_button"].isEnabled()


def test_navigation_errors_restore_buttons_and_discard_stale_callbacks(
    historical_report, monkeypatch
):
    jobs = []
    monkeypatch.setattr(
        historical_report.controller,
        "_ejecutar_en_segundo_plano",
        lambda _label, work, done, failed: jobs.append((done, failed)),
    )
    controls = historical_report.controls
    controls["previous_turn"].click()
    jobs[0][1](RuntimeError("Offline"))
    assert "Offline" in controls["status"].text()
    assert controls["previous_turn"].isEnabled()
    controls["next_turn"].click()
    controls["clear"]()
    jobs[1][0]({"turn_id": 999})
    jobs[1][1](RuntimeError("Old request"))
    assert controls["turn"].get() == "Turno seleccionado"
    assert "Old request" not in controls["status"].text()
    assert len(jobs) == 2


def test_navigation_ignores_queued_signal_without_selected_turn(workspace, monkeypatch):
    work = Mock()
    monkeypatch.setattr(workspace.controller, "_ejecutar_en_segundo_plano", work)
    workspace.controls["previous_turn"].clicked.emit()
    work.assert_not_called()


def test_saved_view_restores_selected_ars(historical_report, tmp_path):
    controls = historical_report.controls
    menu = controls["views_button"].menu()
    menu.store = ReportFavoriteStore(
        "test", QSettings(str(tmp_path / "selected.ini"), QSettings.IniFormat)
    )
    menu.store.save(
        {"name": "HUMANO", "ars_mode": "INCLUIR", "selected_ars": ["HUMANO"]}
    )
    menu.populate()
    next(action for action in menu.actions() if action.text() == "HUMANO").trigger()
    wait_for(
        lambda: (
            controls["state"]["dataset"] is not None
            and controls["state"]["dataset"].filters.selected_ars == ("HUMANO",)
        )
    )
    assert controls["ars_vars"]["HUMANO"].get()
