"""Exercise the ARS chooser through the real financial transition on local SQL."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QWidget

import CALCULOS_QT as app
from tests.test_billing_consistency_postgres import server as server
from tests.test_receipt_optional_uuid_postgres import receipts as receipts, save


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize(
    "case, expected",
    [
        ("ready", app.BILLING_INVOICED),
        ("missing_diagnosis", app.BILLING_PENDING),
        ("missing_authorization", app.BILLING_PENDING),
        ("authorization_removed", app.BILLING_PENDING),
        ("cancel", app.BILLING_PENDING),
        ("no_permission", app.BILLING_PENDING),
        ("no_checklist", app.BILLING_PENDING),
    ],
)
def test_ars_chooser_uses_audited_confirmation(
    qt_app, receipts, monkeypatch, case, expected
):
    receipt_id = save(authorization_number="00001234")
    missing = {
        "missing_diagnosis": ("", "00001234"),
        "missing_authorization": ("PRUEBA", ""),
    }
    if case in missing:
        with app.db_connect() as connection:
            connection.execute(
                "UPDATE recibos SET dx=%s,numero_autorizacion=%s WHERE id=%s",
                (*missing[case], receipt_id),
            )
    candidates = app.list_available_receipts_for_batch(1, batch={"ars": "FUTURO"})
    assert len(candidates) == 1
    assert candidates[0]["estado_facturacion"] == app.BILLING_PENDING

    def confirm(dialog):
        if case == "cancel":
            return QDialog.Rejected
        dialog.unlinked_reason.setText("Verificación manual de prueba")
        for checkbox in dialog.checkboxes.values():
            checkbox.setChecked(case != "no_checklist")
        dialog._accept()
        if case == "authorization_removed":
            assert dialog.result() == QDialog.Accepted
            with app.db_connect() as connection:
                connection.execute(
                    "UPDATE recibos SET numero_autorizacion='' WHERE id=%s",
                    (receipt_id,),
                )
        return dialog.result()

    monkeypatch.setattr(app.BillingStatusDialog, "exec", confirm)
    warning, critical = Mock(), Mock()
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    monkeypatch.setattr(app.QMessageBox, "critical", critical)
    monkeypatch.setattr(app, "FloatingToast", Mock())
    parent = QWidget()
    parent.current_user = {"username": "uuid_test", "role": app.ROLE_ADMIN}
    if case == "no_permission":
        parent.current_user["permission_overrides"] = {
            app.PERMISSION_VALIDATE_RECEIPT: False
        }
    dialog = app.MonthlyBatchCandidateSelectionDialog(candidates, parent)
    try:
        assert not dialog.invoice_button.isEnabled()
        dialog.table.selectRow(0)
        assert dialog.invoice_button.isEnabled() == (case != "no_permission")
        dialog._invoice_current()
        receipt = app.get_recibo_data(receipt_id)
        assert receipt["estado_facturacion"] == expected
        assert dialog.table.item(0, 11).text() == app.billing_status_label(expected)
        assert dialog.selected_keys_in_source_order() == []
        confirmed = expected == app.BILLING_INVOICED
        assert dialog.confirmed_receipt_ids == ({receipt_id} if confirmed else set())
        if confirmed:
            assert receipt["numero_autorizacion"] == "00001234"
            assert not dialog.invoice_button.isEnabled()
        with app.db_connect() as connection:
            count = connection.execute(
                """SELECT COUNT(*) FROM recibo_facturacion_history
                   WHERE recibo_id=%s AND estado_nuevo='FACTURADO'""",
                (receipt_id,),
            ).fetchone()[0]
        assert count == int(confirmed)
        if case in (*missing, "no_checklist"):
            warning.assert_called_once()
        if case == "authorization_removed":
            assert "autorización" in critical.call_args.args[2]
    finally:
        dialog.close()
        parent.close()


def test_missing_receipt_and_failed_preflight_are_reported(qt_app, monkeypatch):
    critical = Mock()
    monkeypatch.setattr(app.QMessageBox, "critical", critical)
    monkeypatch.setattr(
        app, "get_recibo_data", Mock(side_effect=ValueError("Recibo retirado"))
    )
    parent = QWidget()
    parent.current_user = {"username": "qa", "role": app.ROLE_ADMIN}
    dialog = app.MonthlyBatchCandidateSelectionDialog(
        [{"candidate_key": "R:1"}], parent
    )
    try:
        dialog.table.selectRow(0)
        dialog.invoice_button.click()
        assert critical.call_args.args[1:] == ("Consultar recibo", "Recibo retirado")
        monkeypatch.setattr(
            app,
            "get_receipt_audit_preflight",
            Mock(side_effect=ValueError("Sin conexión")),
        )
        assert not app.run_billing_status_dialog(
            dialog, {"id": 1}, app.BILLING_INVOICED, parent.current_user
        )
        assert critical.call_args.args[1:] == (
            "Prevalidación de auditoría",
            "Sin conexión",
        )
        assert not dialog.confirmed_receipt_ids
    finally:
        dialog.close()
        parent.close()


def test_confirmed_status_survives_closing_the_chooser_without_adding(monkeypatch):
    chooser = SimpleNamespace(exec=lambda: QDialog.Rejected, confirmed_receipt_ids={1})
    monkeypatch.setattr(app, "MonthlyBatchCandidateSelectionDialog", lambda *_: chooser)
    add = Mock()
    monkeypatch.setattr(app, "add_receipts_to_monthly_batch", add)
    page = SimpleNamespace(
        available_receipts=[
            {"recibo_id": 1, "estado_facturacion": app.BILLING_PENDING},
            {"recibo_id": 2, "estado_facturacion": app.BILLING_PENDING},
        ]
    )
    app.MonthlyBillingListsPage.add_available_receipt(page)
    assert [row["estado_facturacion"] for row in page.available_receipts] == [
        app.BILLING_INVOICED,
        app.BILLING_PENDING,
    ]
    add.assert_not_called()
