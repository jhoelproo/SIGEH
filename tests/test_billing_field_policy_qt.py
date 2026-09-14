"""Real Qt controls exercised offscreen, without starting hospital services."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QLineEdit,
    QLabel,
    QPushButton,
    QTableWidget,
)

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("authorization", ["19", "3901505"])
def test_edit_status_is_consistent_in_real_label(qt_application, authorization):
    form = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN},
        current_admission_attention=None,
        editing_recibo_id=1,
        authorization_edit=QLineEdit(authorization),
        document_flow_hint=QLabel(),
        btn_generate=QPushButton("Guardar cambios"),
    )
    app.MainWindow._update_document_flow_ui(form)
    assert "EDICIÓN" in form.document_flow_hint.text()
    assert "Bypass" not in form.document_flow_hint.text()
    assert form.btn_generate.text() == "Guardar cambios"


@pytest.mark.parametrize("validated", [False, True])
def test_admin_receipt_edit_controls(qt_application, validated):
    widgets = {
        name: QLineEdit()
        for name in (
            "name_edit",
            "date_edit",
            "dx_edit",
            "ars_combo",
            "coverage_combo",
            "sala_spin",
        )
    }
    form = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN},
        editing_recibo_id=1,
        current_admission_attention={"attention_id": 1} if validated else None,
        service_type="CONSULTA",
        **widgets,
    )
    app.MainWindow._apply_billing_field_policy(form)
    for name in ("name_edit", "date_edit", "dx_edit", "sala_spin"):
        assert widgets[name].isEnabled()
    assert not widgets["ars_combo"].isEnabled()
    assert not widgets["coverage_combo"].isEnabled()
    form.receipt_read_only = True
    app.MainWindow._apply_billing_field_policy(form)
    assert not any(widget.isEnabled() for widget in widgets.values())


@pytest.mark.parametrize("stored_date", ["2026-09-13", "13/09/2026", "invalid"])
def test_loading_legacy_date_and_ars_never_uses_defaults(
    qt_application, monkeypatch, stored_date
):
    data = dict(
        numero=999001,
        nombre="SINTETICO",
        fecha=stored_date,
        dx="DX",
        ars="ARS HISTORICA",
        sala=100,
        items=[],
        estado_facturacion=app.BILLING_PENDING,
    )
    monkeypatch.setattr(app, "get_recibo_data", lambda _: data)
    monkeypatch.setattr(app.QMessageBox, "critical", Mock())
    form = Mock(
        current_user={"role": app.ROLE_ADMIN},
        date_edit=QDateEdit(),
        ars_combo=QComboBox(),
        name_edit=QLineEdit(),
        dx_edit=QLineEdit(),
        authorization_edit=QLineEdit(),
        coverage_combo=QComboBox(),
        sala_spin=QDoubleSpinBox(),
    )
    form.coverage_combo.addItems(["Asegurado", "No asegurado"])
    form.ars_combo.addItem("APS")
    form.cart_has_ars_items.return_value = False
    loaded = app.MainWindow.load_recibo_for_editing(form, 1)
    if stored_date == "invalid":
        assert not loaded
        form.reset_all.assert_not_called()
    else:
        assert loaded
        assert form.date_edit.date().toString("yyyy-MM-dd") == "2026-09-13"
        assert form.ars_combo.currentText() == "ARS HISTORICA"


@pytest.mark.parametrize(
    "role", [app.ROLE_AUX, app.ROLE_AUDIT, app.ROLE_MEDICAL_AUDIT, app.ROLE_ADMIN]
)
def test_real_controls_do_not_reenable_protected_fields(qt_application, role):
    form = SimpleNamespace(
        current_user={"role": role},
        current_admission_attention=None,
        name_edit=QLineEdit(),
        date_edit=QLineEdit(),
        dx_edit=QLineEdit(),
        authorization_edit=QLineEdit(),
        ars_combo=QComboBox(),
        coverage_combo=QComboBox(),
        sala_spin=QDoubleSpinBox(),
        cart_table=QTableWidget(),
        btn_generate=QPushButton(),
        btn_validate_admission=QPushButton(),
        btn_add=QPushButton(),
    )
    app.MainWindow._apply_billing_field_policy(form)
    assert form.sala_spin.isEnabled() == (role == app.ROLE_ADMIN)
    if role == app.ROLE_AUX:
        assert not form.name_edit.isEnabled()
        assert not form.dx_edit.isEnabled()
        assert not form.date_edit.isEnabled()
        assert not form.ars_combo.isEnabled()
    form.current_admission_attention = {"attention_id": 1}
    for read_only in (True, False):
        app.MainWindow._set_receipt_read_only_mode(form, read_only)
        assert not form.date_edit.isEnabled()
        assert not form.name_edit.isEnabled()
        assert not form.ars_combo.isEnabled()
        assert form.dx_edit.isEnabled() == (not read_only)
        assert form.authorization_edit.isEnabled() == (not read_only)
        assert form.sala_spin.isEnabled() == (not read_only and role == app.ROLE_ADMIN)
    form.service_type = "CONSULTA"
    app.MainWindow._apply_billing_field_policy(form)
    assert not form.sala_spin.isEnabled()


@pytest.mark.parametrize(
    "method,write_name",
    [
        (app.MainWindow.save_sala, "set_emergency_price"),
        (app.ARSManagerDialog.save_price, "upsert_ars"),
    ],
)
def test_price_actions_deny_non_admin_before_database(monkeypatch, method, write_name):
    write = Mock()
    monkeypatch.setattr(app, write_name, write)
    warning = Mock()
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    method(SimpleNamespace(current_user={"role": app.ROLE_AUDIT}, mark_activity=Mock()))
    write.assert_not_called()
    warning.assert_called_once()
