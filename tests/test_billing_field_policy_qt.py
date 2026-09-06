"""Real Qt controls exercised offscreen, without starting hospital services."""

import os
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QLineEdit,
    QPushButton,
    QTableWidget,
)

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


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
