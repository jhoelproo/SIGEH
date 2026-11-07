from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from admission_turn_history_dialog import AdmissionTurnHistoryDialog
from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as v15
from tests.test_admission_initial_layout_20260822 import _build_admission_widget
from tests.test_admission_statistical_reports_v106 import _row


HISTORICAL_ARS = "ARS HISTÓRICA"


def wait_for(predicate):
    for _ in range(40):
        result = predicate()
        if result:
            return result
        QTest.qWait(25)
    pytest.fail("El reporte no respondió a tiempo.")


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch, host_theme_is_dark=True
    )
    stack.setCurrentWidget(widget)
    shell.show()
    controller = widget.admission
    controller.abrir_ventana_reporte()
    application.processEvents()
    error = Mock()
    monkeypatch.setattr(v15.messagebox, "showerror", error)
    dialog = controller.reporte_win
    yield SimpleNamespace(
        controller=controller,
        dialog=dialog,
        controls=dialog.report_controls,
        error=error,
    )
    widget.shutdown()
    shell.close()
    shell.deleteLater()
    application.processEvents()


@pytest.fixture
def historical_report(workspace, monkeypatch):
    turn = dict(
        turn_id=316,
        operational_source_id=_row(1)["operational_source_id"],
        started_at="2026-08-27T09:35:00-04:00",
        ends_at="2026-08-28T08:10:00-04:00",
        username="original",
        display_name="Admisor original",
        patient_count=2,
    )
    source = dict(
        turn_id=316,
        selected_turn=turn,
        turns=[turn],
        records=[_row(1), _row(2, ars=HISTORICAL_ARS)],
    )
    load = Mock(return_value=source)
    monkeypatch.setattr(
        type(workspace.controller.db),
        "load_historical_turn_report",
        lambda _self, selected: load(selected),
    )
    controls = workspace.controls
    controls["turn_history_button"].click()
    history = workspace.dialog.findChild(AdmissionTurnHistoryDialog)
    assert history.isVisible()
    history.loaded([turn])
    history.use.click()
    wait_for(lambda: controls["state"]["dataset"])
    load.assert_called_once_with(turn)
    return workspace


@pytest.mark.parametrize(
    ("station", "expected"),
    [
        ({}, "identidad operacional"),
        ({"operational_source_id": "local"}, "turno central activo"),
    ],
)
def test_report_requires_selected_shift_or_active_operational_identity(
    workspace, monkeypatch, station, expected
):
    controls = workspace.controls
    controls["turn"].set("Turno seleccionado")
    controls["generate"]()
    assert "Seleccione un turno" in workspace.error.call_args.args[1]
    controls["turn"].set("Turno actual")
    monkeypatch.setattr(
        type(workspace.controller.db),
        "get_operational_station_snapshot",
        lambda _self: station,
    )
    controls["generate"]()
    wait_for(lambda: workspace.error.call_count == 2)
    assert expected in workspace.error.call_args.args[1]
    assert controls["generate_button"].isEnabled()


def test_select_historical_shift_opens_original_user_report(historical_report):
    controls = historical_report.controls
    dataset = controls["state"]["dataset"]
    assert controls["turn"].get() == "Turno seleccionado"
    assert not controls["period"].isVisible()
    assert len(dataset.records) == 2
    assert (dataset.filters.start_at.hour, dataset.filters.start_at.minute) == (9, 35)
    assert "Admisor original" in dataset.summary["turn_label"]
    assert HISTORICAL_ARS in controls["ars_vars"]
    assert "2 ARS" in controls["ars_summary"].text()


def test_keyboard_search_and_filter_validation_in_history(historical_report):
    controls = historical_report.controls
    controls["ars_mode"].set("Incluir seleccionadas")
    controls["generate"]()
    assert "al menos una ARS" in historical_report.error.call_args.args[1]
    search = controls["ars_search"]
    assert search.isVisible()
    QTest.keyClicks(search, "historica")
    checks = controls["ars_canvas"].widget().findChildren(v15.tb.Checkbutton)
    visible = [checkbox for checkbox in checks if checkbox.isVisible()]
    assert [checkbox.text() for checkbox in visible] == [HISTORICAL_ARS]
    QTest.keyClick(visible[0], Qt.Key_Space)
    assert controls["ars_vars"][HISTORICAL_ARS].get()
    search.setText("sin coincidencia")
    assert "Sin coincidencias" in controls["ars_summary"].text()
    assert controls["ars_vars"][HISTORICAL_ARS].get()


@pytest.mark.parametrize(
    ("mode", "expected"),
    [("Incluir seleccionadas", HISTORICAL_ARS), ("Excluir seleccionadas", "HUMANO")],
)
def test_ars_filters_change_totals_and_exports_without_changing_shift(
    historical_report, mode, expected
):
    controls = historical_report.controls
    original = controls["state"]["dataset"]
    controls["ars_mode"].set(mode)
    controls["ars_vars"][HISTORICAL_ARS].set(True)
    assert not controls["pdf_button"].isEnabled()
    controls["generate_button"].click()
    wait_for(lambda: controls["state"]["dataset"] is not original)
    filtered = controls["state"]["dataset"]
    assert filtered.filters.turn_id == 316
    assert [row["canonical_ars"] for row in filtered.records] == [expected]
    assert controls["cards"]["total"][0].get() == "1"
    assert HISTORICAL_ARS in controls["applied_scope"].text()
    assert controls["state"]["snapshot_store"].require_exportable() is filtered
    assert controls["pdf_button"].isEnabled() and controls["excel_button"].isEnabled()


def test_reset_preserves_historical_scope_and_removes_ars_filter(historical_report):
    controls = historical_report.controls
    controls["ars_mode"].set("Incluir seleccionadas")
    controls["ars_vars"][HISTORICAL_ARS].set(True)
    controls["clear"]()
    assert controls["turn"].get() == "Turno seleccionado"
    assert controls["ars_mode"].get() == "Todas las ARS"
    assert not controls["ars_search"].isVisible()
    assert not controls["ars_vars"][HISTORICAL_ARS].get()
    assert controls["state"]["dataset"] is None
    assert not controls["applied_scope"].isVisible()


def test_compact_report_keeps_filters_results_and_actions_visible(historical_report):
    dialog, controls = historical_report.dialog, historical_report.controls
    dialog.resize(760, 580)
    controls["ars_mode"].set("Incluir seleccionadas")
    QTest.qWait(30)
    for name in (
        "ars_search",
        "ars_canvas",
        "preview",
        "generate_button",
        "pdf_button",
        "excel_button",
    ):
        widget = controls[name]
        assert widget.isVisible()
        assert dialog.rect().contains(widget.mapTo(dialog, widget.rect().bottomRight()))
    summary = controls["ars_summary"]
    checklist = controls["ars_canvas"]
    assert (
        summary.mapTo(dialog, summary.rect().bottomRight()).y()
        < checklist.mapTo(dialog, QPoint()).y()
    )


def test_filter_changes_and_dialog_close_discard_pending_results(
    workspace, monkeypatch
):
    controls = workspace.controls
    jobs = []

    def hold_report(_message, _work, finished, failed):
        jobs.append((finished, failed))

    monkeypatch.setattr(workspace.controller, "_ejecutar_en_segundo_plano", hold_report)
    controls["generate"]()
    assert not controls["generate_button"].isEnabled()
    controls["coverage_var"].set("SIN SEGURO")
    assert controls["generate_button"].isEnabled()
    jobs[0][0](None)
    jobs[0][1](RuntimeError("Consulta anterior"))
    assert controls["state"]["dataset"] is None
    workspace.error.assert_not_called()

    controls["generate"]()
    controls["clear"]()
    jobs[1][0](None)
    assert controls["generate_button"].isEnabled()
    assert not controls["pdf_button"].isEnabled()

    controls["generate"]()
    QTest.keyClick(workspace.dialog, Qt.Key_Escape)
    assert not workspace.dialog.winfo_exists()
    jobs[2][0](None)
    jobs[2][1](RuntimeError("Diálogo cerrado"))
    workspace.error.assert_not_called()
