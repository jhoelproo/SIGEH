from unittest.mock import Mock

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from admission_turn_history_dialog import AdmissionTurnHistoryDialog
from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as v15
from tests.test_admission_initial_layout_20260822 import _build_admission_widget
from tests.test_admission_statistical_reports_v106 import _row


def test_select_historical_shift_generates_original_user_dataset(tmp_path, monkeypatch):
    application, shell, stack, _billing, widget = _build_admission_widget(
        tmp_path, monkeypatch
    )
    try:
        shell.resize(1600, 900)
        stack.setCurrentWidget(widget)
        shell.show()
        controller = widget.admission
        controller.abrir_ventana_reporte()
        controls = controller.reporte_win.report_controls
        error = Mock()
        monkeypatch.setattr(v15.messagebox, "showerror", error)
        controls["turn"].set("Turno seleccionado")
        controls["generate"]()
        assert "Seleccione un turno" in error.call_args.args[1]
        controls["turn"].set("Turno actual")
        monkeypatch.setattr(
            type(controller.db), "get_operational_station_snapshot", lambda _self: {}
        )
        controls["generate"]()
        for _ in range(40):
            QTest.qWait(25)
            if error.call_count == 2:
                break
        assert error.call_count == 2
        assert "identidad operacional" in error.call_args.args[1]
        turn = dict(
            turn_id=316,
            operational_source_id=_row(1)["operational_source_id"],
            started_at="2026-08-27T09:35:00-04:00",
            ends_at="2026-08-28T08:10:00-04:00",
            username="original",
            display_name="Admisor original",
            patient_count=1,
        )
        source = dict(turn_id=316, selected_turn=turn, turns=[turn], records=[_row(1)])
        load = Mock(return_value=source)
        monkeypatch.setattr(
            type(controller.db),
            "load_historical_turn_report",
            lambda _self, selected: load(selected),
        )
        history_button = next(
            button
            for button in controller.reporte_win.findChildren(QPushButton)
            if button.text() == "Historial de turnos"
        )
        assert history_button.parent() is controls["pdf_button"].parent()
        assert controls["turn_history_button"] is history_button
        history_button.click()
        history = controller.reporte_win.findChild(AdmissionTurnHistoryDialog)
        assert history.isVisible()
        history.loaded([turn])
        history.use.click()
        assert controls["turn"].get() == "Turno seleccionado"
        controls["generate"]()
        for _ in range(40):
            application.processEvents()
            if controls["state"]["dataset"] is not None:
                break
            QTest.qWait(25)
        dataset = controls["state"]["dataset"]
        assert dataset is not None
        assert len(dataset.records) == 1
        assert dataset.filters.start_at.hour == 9
        assert dataset.filters.start_at.minute == 35
        assert "Admisor original" in dataset.summary["turn_label"]
        load.assert_called_once_with(turn)
        controls["clear"]()
        assert controls["turn"].get() == "Turno actual"
    finally:
        widget.shutdown()
        shell.close()
        shell.deleteLater()
        application.processEvents()
