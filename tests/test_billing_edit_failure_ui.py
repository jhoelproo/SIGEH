"""Exercise the real save preflight without hospital services or modal dialogs."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from billing_admission_edit import AdmissionDataChanged


@pytest.mark.parametrize(
    "error,dialog",
    [
        (AdmissionDataChanged(["fecha"]), "warning"),
        (app.AdmissionAttentionUnavailableError("TOMBSTONED"), "warning"),
        (ConnectionError("test disconnected"), "critical"),
    ],
)
def test_save_preflight_preserves_draft_and_authorization_on_failure(
    monkeypatch, error, dialog
):
    attention = {"attention_id": 17, "source_instance_id": "TEST"}
    form = SimpleNamespace(
        mark_activity=Mock(),
        receipt_read_only=False,
        editing_recibo_id=31,
        current_admission_attention=attention,
        current_user={"role": app.ROLE_ADMIN},
        session_id="TEST_SESSION",
    )
    lookup = Mock(side_effect=error)
    popup = Mock()
    save = Mock()
    release = Mock()
    monkeypatch.setattr(app, "get_projected_billable_attention", lookup)
    monkeypatch.setattr(app.QMessageBox, dialog, popup)
    monkeypatch.setattr(app, "save_receipt_with_items", save)
    monkeypatch.setattr(app, "schedule_admission_claim_release", release)
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    app.MainWindow.generate_pdf(form)
    assert form.current_admission_attention is attention
    assert form.editing_recibo_id == 31
    assert lookup.call_args.kwargs["expected_snapshot"] is attention
    assert lookup.call_args.kwargs["receipt_id"] == 31
    assert lookup.call_args.kwargs["explain_denial"] is True
    popup.assert_called_once()
    save.assert_not_called()
    release.assert_not_called()


def test_optional_controls_do_not_break_field_policy():
    app.MainWindow._apply_billing_field_policy(
        SimpleNamespace(
            current_user={"role": app.ROLE_AUX}, current_admission_attention=None
        )
    )


@pytest.mark.parametrize(
    "method", [app.MainWindow.save_sala, app.ARSManagerDialog.save_price]
)
def test_admin_can_update_ars_tariff(monkeypatch, method):
    write = Mock()
    monkeypatch.setattr(app, "set_emergency_price", write)
    monkeypatch.setattr(app, "upsert_ars", write)
    monkeypatch.setattr(app, "log_action", Mock())
    monkeypatch.setattr(app, "FloatingToast", Mock())
    monkeypatch.setattr(app, "medication_ars_is_selectable", lambda name: True)
    monkeypatch.setattr(app, "ARS_RUNTIME_CACHE", Mock())
    form = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN, "username": "TEST_ADMIN"},
        mark_activity=Mock(),
        current_ars="TEST_ARS",
        ars_combo=SimpleNamespace(currentText=lambda: "TEST_ARS"),
        sala_spin=SimpleNamespace(value=lambda: 460),
        consulta_spin=SimpleNamespace(value=lambda: 0),
        update_totals=Mock(),
    )
    method(form)
    write.assert_called_once()
    assert write.call_args.args[:2] == ("TEST_ARS", 460)


def test_projection_denial_explains_real_reason(monkeypatch):
    monkeypatch.setattr(
        app,
        "evaluate_attention_billing_eligibility",
        Mock(
            return_value={
                "eligible": False,
                "reason_code": "TOMBSTONED",
                "reason": "Atención anulada.",
            }
        ),
    )
    with pytest.raises(app.AdmissionAttentionUnavailableError) as caught:
        app.get_projected_billable_attention(17, "TEST", explain_denial=True)
    assert caught.value.reason_code == "TOMBSTONED"


def test_projection_preflight_rejects_changed_snapshot(monkeypatch):
    monkeypatch.setattr(
        app,
        "evaluate_attention_billing_eligibility",
        Mock(
            return_value={
                "eligible": True,
                "_projection": {"service_date": "2026-09-06"},
            }
        ),
    )
    with pytest.raises(AdmissionDataChanged):
        app.get_projected_billable_attention(
            17, "TEST", expected_snapshot={"service_date": "2026-09-05"}
        )


def test_missing_projection_cannot_continue_transaction():
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = None
    with pytest.raises(app.AdmissionAttentionUnavailableError) as caught:
        app._lock_and_validate_admission_processing(
            connection,
            {"attention_id": 17, "source_instance_id": "TEST"},
            session_id="A",
        )
    assert caught.value.reason_code == "ATTENTION_NOT_FOUND"
    assert connection.execute.call_count == 2


def test_missing_claim_cannot_continue_transaction(monkeypatch):
    connection = Mock()
    connection.execute.return_value.fetchone.side_effect = [
        {"attention_id": 17, "source_instance_id": "TEST"},
        None,
    ]
    monkeypatch.setattr(
        app,
        "evaluate_attention_billing_eligibility",
        Mock(return_value={"eligible": True, "_projection": {}}),
    )
    with pytest.raises(app.AdmissionAttentionUnavailableError) as caught:
        app._lock_and_validate_admission_processing(
            connection,
            {"attention_id": 17, "source_instance_id": "TEST"},
            session_id="A",
        )
    assert caught.value.reason_code == "CLAIM_NOT_OWNED"


def test_unvalidated_auxiliary_header_change_is_denied():
    from billing_field_policy import require_validated_header_edit

    with pytest.raises(PermissionError, match="validar"):
        require_validated_header_edit(
            auxiliary=True,
            validated=False,
            supplied={"nombre": "CHANGED", "dx": "", "fecha": "", "ars": "", "sala": 0},
            previous={
                "nombre": "ORIGINAL",
                "dx": "",
                "fecha": "",
                "ars": "",
                "sala": 0,
            },
        )
