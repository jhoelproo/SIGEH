import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QPushButton,
    QLabel,
    QDateEdit,
    QDoubleSpinBox,
    QTableWidget,
)

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("coverage", ["Extranjero", "No asegurado"])
def test_self_pay_loads_contributive_tariff_without_authorization(
    qt_application, coverage
):
    ars = QComboBox()
    ars.addItems(["APS", "SENASA CONTRIBUTIVO"])
    form = SimpleNamespace(
        current_admission_attention=None,
        ars_combo=ars,
        btn_ars_mgmt=QPushButton(),
        authorization_edit=QLineEdit("1234"),
        set_current_ars_from_cache=Mock(),
        update_totals=Mock(),
        _update_document_flow_ui=Mock(),
    )
    app.MainWindow.on_coverage_changed(form, coverage)
    form.set_current_ars_from_cache.assert_called_once_with("SENASA CONTRIBUTIVO")
    assert ars.currentText() == "SENASA CONTRIBUTIVO"
    assert not ars.isEnabled()
    assert not form.authorization_edit.isEnabled()
    assert not form.authorization_edit.text()


def test_insured_coverage_keeps_selected_ars_flow(qt_application):
    ars = QComboBox()
    ars.addItems(["APS", "SENASA CONTRIBUTIVO"])
    form = SimpleNamespace(
        current_admission_attention=None,
        ars_combo=ars,
        btn_ars_mgmt=QPushButton(),
        authorization_edit=QLineEdit(),
        on_ars_changed=Mock(),
        update_totals=Mock(),
        _update_document_flow_ui=Mock(),
    )
    app.MainWindow.on_coverage_changed(form, "Asegurado")
    form.on_ars_changed.assert_called_once_with("APS")
    assert ars.isEnabled()
    assert form.authorization_edit.isEnabled()


@pytest.mark.parametrize(
    "coverage,status",
    [
        ("Extranjero", "PAGADO"),
        ("No asegurado", "PAGADO"),
        ("No asegurado", "EXONERADO"),
        ("Extranjero", "PENDIENTE_PAGO"),
        ("No asegurado", "PENDIENTE_PAGO"),
    ],
)
@pytest.mark.parametrize("role", [app.ROLE_AUX, app.ROLE_ADMIN])
def test_direct_payment_controls_keep_tariffs_locked(
    qt_application, coverage, status, role
):
    form = SimpleNamespace(
        current_user={"role": role},
        current_admission_attention=None,
        editing_recibo_id=None,
        name_edit=QLineEdit(),
        date_edit=QDateEdit(),
        dx_edit=QLineEdit(),
        authorization_edit=QLineEdit(),
        ars_combo=QComboBox(),
        coverage_combo=QComboBox(),
        sala_spin=QDoubleSpinBox(),
        document_flow_hint=QLabel(),
        btn_generate=QPushButton(),
        btn_validate_admission=QPushButton(),
        exemption_reason_edit=QLineEdit(),
        payment_combo=QComboBox(),
    )
    form.coverage_combo.addItems(["Asegurado", "No asegurado", "Extranjero"])
    form.coverage_combo.setCurrentText(coverage)
    form.payment_combo.addItem(status, status)
    app.MainWindow._update_document_flow_ui(form)
    assert (
        form.name_edit.isEnabled()
        and form.dx_edit.isEnabled()
        and form.date_edit.isEnabled()
    )
    assert (
        not form.ars_combo.isEnabled()
        and not form.sala_spin.isEnabled()
        and not form.authorization_edit.isEnabled()
    )
    assert form.btn_validate_admission.isHidden()
    assert form.exemption_reason_edit.isHidden() == (status != "EXONERADO")
    assert "COBRO DIRECTO" in form.document_flow_hint.text()
    assert "COBRO" in form.btn_generate.text()


@pytest.mark.parametrize(
    "coverage,status,reason",
    [
        ("EXTRANJERO", "PAGADO", ""),
        ("NO_ASEGURADO", "EXONERADO", "Exoneración autorizada"),
        ("EXTRANJERO", "PENDIENTE_PAGO", ""),
        ("NO_ASEGURADO", "PENDIENTE_PAGO", ""),
    ],
)
def test_reopen_direct_payment_restores_state_and_contributive_catalog(
    qt_application, monkeypatch, coverage, status, reason
):
    data = dict(
        numero=990001,
        nombre="SINTETICO",
        dx="DX",
        fecha="2026-10-01",
        ars="",
        sala=460,
        items=[],
        estado_facturacion=app.BILLING_NOT_INVOICED,
        receipt_origin="SELF_PAY",
        tipo_cobertura=coverage,
        payment_status=status,
        exemption_reason=reason,
    )
    monkeypatch.setattr(app, "get_recibo_data", lambda _: data)
    form = Mock(
        current_user={"role": app.ROLE_ADMIN},
        date_edit=QDateEdit(),
        ars_combo=QComboBox(),
        name_edit=QLineEdit(),
        dx_edit=QLineEdit(),
        authorization_edit=QLineEdit(),
        coverage_combo=QComboBox(),
        sala_spin=QDoubleSpinBox(),
        payment_combo=QComboBox(),
        exemption_reason_edit=QLineEdit(),
    )
    form.coverage_combo.addItems(["Asegurado", "No asegurado", "Extranjero"])
    form.payment_combo.addItem(status, status)
    form.sala_spin.setMaximum(1000)
    form.cart_has_ars_items.return_value = False
    assert app.MainWindow.load_recibo_for_editing(form, 1)
    assert form.ars_combo.currentText() == "SENASA CONTRIBUTIVO"
    assert (
        form.payment_combo.currentData() == status
        and form.exemption_reason_edit.text() == reason
    )
    assert form.sala_spin.value() == 460
    form._set_receipt_read_only_mode.assert_called_with(False)


@pytest.mark.parametrize("discard", [True, False])
def test_start_direct_receipt_respects_existing_draft(
    qt_application, monkeypatch, discard
):
    form = SimpleNamespace(
        cart_table=QTableWidget(),
        name_edit=QLineEdit("BORRADOR"),
        coverage_combo=QComboBox(),
        reset_all=Mock(),
    )
    form.coverage_combo.addItems(["Asegurado", "Extranjero"])
    monkeypatch.setattr(
        app.QMessageBox,
        "question",
        Mock(return_value=app.QMessageBox.Yes if discard else app.QMessageBox.No),
    )
    app.MainWindow.start_self_pay_receipt(form, "EXTRANJERO")
    assert form.reset_all.called == discard
    assert form.coverage_combo.currentText() == (
        "Extranjero" if discard else "Asegurado"
    )


def test_invalid_exemption_stops_before_receipt_worker(qt_application, monkeypatch):
    form = SimpleNamespace(
        mark_activity=Mock(),
        receipt_read_only=False,
        coverage_combo=QComboBox(),
        payment_combo=QComboBox(),
        exemption_reason_edit=QLineEdit("corto"),
    )
    form.coverage_combo.addItem("No asegurado")
    form.payment_combo.addItem("Exonerado", "EXONERADO")
    toast = Mock()
    monkeypatch.setattr(app, "FloatingToast", toast)
    app.MainWindow.generate_pdf(form)
    assert "mínimo 8" in toast.call_args.args[0]
    toast.return_value.show.assert_called_once()


@pytest.mark.parametrize(
    "coverage,states",
    [
        ("Extranjero", ["PAGADO", "PENDIENTE_PAGO"]),
        ("No asegurado", ["PAGADO", "EXONERADO", "PENDIENTE_PAGO"]),
        ("Asegurado", []),
    ],
)
def test_changing_coverage_builds_only_applicable_payment_states(
    qt_application, coverage, states
):
    ars = QComboBox()
    ars.addItems(["APS", "SENASA CONTRIBUTIVO"])
    form = SimpleNamespace(
        current_admission_attention=None,
        ars_combo=ars,
        btn_ars_mgmt=QPushButton(),
        authorization_edit=QLineEdit(),
        payment_combo=QComboBox(),
        exemption_reason_edit=QLineEdit(),
        set_current_ars_from_cache=Mock(),
        on_ars_changed=Mock(),
        update_totals=Mock(),
        _update_document_flow_ui=Mock(),
    )
    app.MainWindow.on_coverage_changed(form, coverage)
    assert [
        form.payment_combo.itemData(i) for i in range(form.payment_combo.count())
    ] == states
    assert form.payment_combo.isHidden() == (coverage == "Asegurado")


def test_foreign_coverage_cannot_reprice_existing_other_ars_items(
    qt_application, monkeypatch
):
    combo = QComboBox()
    combo.addItems(["Asegurado", "Extranjero"])
    combo.setCurrentText("Extranjero")
    form = SimpleNamespace(
        current_ars="APS",
        locked_ars="APS",
        cart_has_ars_items=Mock(return_value=True),
        coverage_combo=combo,
        _last_coverage="Asegurado",
    )
    monkeypatch.setattr(app, "FloatingToast", Mock())
    app.MainWindow.on_coverage_changed(form, "Extranjero")
    assert combo.currentText() == "Asegurado"


def test_clear_receipt_does_not_reuse_previous_exemption(qt_application, monkeypatch):
    monkeypatch.setattr(app, "schedule_replaced_admission_claim_release", Mock())
    form = Mock(
        current_admission_attention=None,
        session_id="QA",
        exemption_reason_edit=QLineEdit("Motivo anterior"),
        btn_validate_admission=QPushButton(),
    )
    app.MainWindow.reset_all(form)
    assert form.exemption_reason_edit.text() == ""
    form.coverage_combo.setCurrentText.assert_called_with("Asegurado")


def test_main_window_opens_own_history_and_integrated_preview(monkeypatch):
    import self_pay_history_dialog as ui

    dialog = Mock()
    monkeypatch.setattr(ui, "SelfPayHistoryDialog", dialog)
    form = Mock(current_user={"username": "QA", "full_name": "QA LOCAL"})
    app.MainWindow.open_self_pay_history(form)
    assert dialog.call_args.kwargs["actor"] == "QA LOCAL"
    dialog.return_value.exec.assert_called_once()
    preview = Mock()
    monkeypatch.setattr(app, "ComparisonPdfDialog", preview)
    app.MainWindow._preview_self_pay_report(form, "report.pdf")
    preview.return_value.show.assert_called_once()
    assert form._self_pay_preview == preview.return_value


def test_package_report_smoke_includes_direct_payment_exports(tmp_path, monkeypatch):
    renderer = Mock()
    monkeypatch.setattr(app, "ReportHTMLRenderer", lambda: renderer)
    assert app.run_report_exports_self_test(str(tmp_path)) == 0
    contexts = [call.args[0] for call in renderer.render_pdf.call_args_list]
    direct = next(context for context in contexts if context["mode"] == "self_pay")
    assert direct["data"]["summary"][0]["collected"] == "560.00"
    assert direct["data"]["summary"][1]["collected"] == "0.00"
    assert (tmp_path / "cobros_directos.xlsx").exists()


@pytest.mark.parametrize(
    "current,locked,items,expected",
    [
        ("APS", None, True, False),
        ("SENASA CONTRIBUTIVO", "SENASA CONTRIBUTIVO", True, False),
        ("APS", "APS", False, False),
        ("APS", "APS", True, True),
    ],
)
def test_coverage_tariff_guard_requires_conflicting_cart_items(
    current, locked, items, expected
):
    form = SimpleNamespace(
        current_ars=current,
        locked_ars=locked,
        cart_has_ars_items=Mock(return_value=items),
    )
    assert app.MainWindow._coverage_conflicts_with_cart(form, "Extranjero") == expected
