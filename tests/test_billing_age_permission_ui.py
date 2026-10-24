"""The billing form enforces the same individual date permission before submit."""

from datetime import datetime, timedelta
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QDate

import CALCULOS_QT as app


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 22, 12, tzinfo=tz)


@pytest.mark.parametrize(
    "role,grant,days,expected",
    [
        (app.ROLE_AUX, False, 0, True),
        (app.ROLE_AUX, False, 2, True),
        (app.ROLE_AUX, False, 3, False),
        (app.ROLE_AUX, True, 3, True),
        (app.ROLE_ADMIN, False, 3, False),
        (app.ROLE_AUDIT, True, 30, True),
        (app.ROLE_AUX, True, -1, False),
        (app.ROLE_AUX, False, -1, False),
    ],
)
def test_receipt_form_checks_age_before_disabling_and_submitting(
    monkeypatch, role, grant, days, expected
):
    monkeypatch.setattr(app, "datetime", FixedDateTime)
    toast = Mock()
    monkeypatch.setattr(app, "FloatingToast", toast)
    selected = FixedDateTime.now().date() - timedelta(days=days)
    form = Mock(
        _pending_ars_correction=None,
        receipt_read_only=False,
        editing_recibo_id=7,
        editing_recibo_numero=990007,
        current_admission_attention=None,
        current_verification_bypass=None,
        current_user={
            "username": "SYNTHETIC",
            "role": role,
            "permission_overrides": {app.PERMISSION_BILL_OLDER_ATTENTION: grant},
        },
        session_id="SYNTHETIC-SESSION",
        current_ars="FUTURO",
        _last_generate_time=0,
    )
    form.coverage_combo.currentText.return_value = "Asegurado"
    form.name_edit.text.return_value = "SYNTHETIC"
    form.date_edit.date.return_value = QDate(
        selected.year, selected.month, selected.day
    )
    form.dx_edit.text.return_value = "SYNTHETIC"
    form.authorization_edit.text.return_value = "123456"
    form.cart_table.rowCount.return_value = 0
    form.sala_spin.value.return_value = 500

    app.MainWindow.generate_pdf(form)

    if expected:
        payload = form.pdf_worker.submit.call_args.args[0]
        assert payload["date_str"] == selected.isoformat()
        assert payload["is_backdated"] == int(days > 2)
        form.btn_generate.setEnabled.assert_called_once_with(False)
        toast.assert_not_called()
    else:
        form.pdf_worker.submit.assert_not_called()
        form.btn_generate.setEnabled.assert_not_called()
        assert (
            "futuras" in toast.call_args.args[0]
            if days < 0
            else "48 horas" in toast.call_args.args[0]
        )
