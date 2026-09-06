"""Same-attention revalidation must preserve an existing billing draft."""

from dataclasses import replace
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from admission_bridge import AdmissionAttention


def attention(**changes):
    fields = dict(
        attention_id=7,
        patient_id=3,
        name="SYNTHETIC",
        service_date="2026-09-06",
        service_time="10:00",
        nss="1",
        nss_clean="1",
        cedula="",
        cedula_clean="",
        ars="FUTURO",
        attention_type="EMERGENCIA",
        source_updated_at="old",
        uninsured=False,
        source_instance_id="TEST",
        canonical_ars="FUTURO",
        coverage_status="ASEGURADO_VALIDADO",
        global_attention_id="11111111-1111-4111-8111-111111111111",
        billing_readiness="LISTA",
    )
    return AdmissionAttention(**(fields | changes))


@pytest.mark.parametrize("editing_id", [None, 31])
def test_revalidation_of_same_patient_keeps_items_authorization_and_receipt_version(
    monkeypatch, editing_id
):
    original = attention()
    verified = replace(
        original,
        name="SYNTHETIC CORRECTED",
        nss_clean="2",
        version=2,
        billing_claim_acquired_at="new-claim",
    )
    form = Mock(
        current_admission_attention=original.snapshot(),
        editing_recibo_id=editing_id,
        editing_recibo_numero=51,
        session_id="OWN",
        authorization_edit=Mock(text=Mock(return_value="AUTH-UNCHANGED")),
        cart_table=Mock(rowCount=Mock(return_value=2)),
        receipt_revision=9,
    )
    monkeypatch.setattr(
        app.QMessageBox, "question", Mock(return_value=app.QMessageBox.Yes)
    )
    monkeypatch.setattr(app, "FloatingToast", Mock())
    app.MainWindow._complete_verified_admission(form, verified)
    form.reset_all.assert_not_called()
    form._apply_admission_attention.assert_not_called()
    form.cart_table.setRowCount.assert_not_called()
    form.authorization_edit.clear.assert_not_called()
    form.authorization_edit.setText.assert_not_called()
    assert form.editing_recibo_id == editing_id
    assert form.receipt_revision == 9
    assert form.current_admission_attention["billing_claim_acquired_at"] == "new-claim"
    assert form.current_admission_attention["version"] == 2
    form.name_edit.setText.assert_called_once_with("SYNTHETIC CORRECTED")


@pytest.mark.parametrize(
    "changes",
    [
        {"global_attention_id": ""},
        {"global_attention_id": "invalid"},
        {"global_attention_id": "22222222-2222-4222-8222-222222222222"},
        {"uninsured": True},
        {"billing_readiness": "REVISION"},
        {"source_status": "ANULADA"},
        {"service_date": "2026-09-07"},
        {"canonical_ars": "OTHER"},
        {"attention_type": "CONSULTA"},
        {"coverage_status": "COBERTURA_EN_REVISION"},
        {"source_instance_id": "OTHER"},
        {"attention_id": 8},
    ],
)
def test_draft_refresh_rejects_identity_or_billing_changes(changes):
    from billing_admission_edit import can_refresh_patient_in_draft

    old = attention().snapshot()
    assert not can_refresh_patient_in_draft(old, old | changes)


@pytest.mark.parametrize(
    "field",
    [
        "global_attention_id",
        "service_date",
        "canonical_ars",
        "attention_type",
        "coverage_status",
        "source_instance_id",
        "attention_id",
    ],
)
def test_incomplete_previous_snapshot_requires_existing_review(field):
    from billing_admission_edit import can_refresh_patient_in_draft

    new = attention().snapshot()
    old = dict(new)
    old.pop(field)
    assert not can_refresh_patient_in_draft(old, new)


def test_demographic_refresh_accepts_equivalent_uuid_and_updates_name():
    from billing_admission_edit import can_refresh_patient_in_draft

    old = attention().snapshot()
    new = old | {
        "name": "UPDATED",
        "cedula_clean": "1",
        "nss_clean": "2",
        "global_attention_id": "{11111111-1111-4111-8111-111111111111}",
    }
    assert can_refresh_patient_in_draft(old, new)
    assert old["name"] == "SYNTHETIC"


def test_real_qt_revalidation_keeps_physical_draft_controls(monkeypatch):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QLineEdit,
        QTableWidget,
        QTableWidgetItem,
    )

    qt = QApplication.instance() or QApplication([])
    cart = QTableWidget(1, 2)
    cart.setItem(0, 0, QTableWidgetItem("SYNTHETIC ITEM"))
    cart.setItem(0, 1, QTableWidgetItem("125.50"))
    authorization = QLineEdit("AUTH-UNCHANGED")
    version = QLabel("Versión 9")
    name = QLineEdit("SYNTHETIC")
    form = SimpleNamespace(
        current_admission_attention=attention().snapshot(),
        editing_recibo_id=31,
        cart_table=cart,
        authorization_edit=authorization,
        lbl_edit_mode=version,
        name_edit=name,
    )
    monkeypatch.setattr(app, "FloatingToast", Mock())
    app.MainWindow._complete_verified_admission(
        form, attention(name="SYNTHETIC CORRECTED", version=2)
    )
    qt.processEvents()
    assert name.text() == "SYNTHETIC CORRECTED"
    assert cart.rowCount() == 1
    assert cart.item(0, 0).text() == "SYNTHETIC ITEM"
    assert cart.item(0, 1).text() == "125.50"
    assert authorization.text() == "AUTH-UNCHANGED"
    assert version.text() == "Versión 9"
    assert form.editing_recibo_id == 31
