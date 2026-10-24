"""Individual 48h exceptions never grant unrelated billing capabilities."""

from datetime import date, timedelta
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from billing_date_policy import validate_receipt_date
from user_permissions import validate_overrides


TODAY = date(2026, 10, 22)
PERMISSION = app.PERMISSION_BILL_OLDER_ATTENTION


@pytest.mark.parametrize("days,expected", [(0, 0), (1, 0), (2, 0), (3, 1), (30, 1)])
def test_existing_date_window_is_preserved(days, expected):
    selected = (TODAY - timedelta(days=days)).isoformat()
    assert validate_receipt_date(selected, today=TODAY, allow_older=True) == expected
    if days > 2:
        with pytest.raises(PermissionError, match="48 horas"):
            validate_receipt_date(selected, today=TODAY, allow_older=False)
    else:
        assert validate_receipt_date(selected, today=TODAY, allow_older=False) == 0


@pytest.mark.parametrize("allowed", [True, False])
def test_permission_does_not_allow_future_dates(allowed):
    with pytest.raises(ValueError, match="futuras"):
        validate_receipt_date("2026-10-23", today=TODAY, allow_older=allowed)


@pytest.mark.parametrize("value", ["", "not a date", "2026-02-30"])
def test_invalid_dates_are_rejected(value):
    with pytest.raises(ValueError):
        validate_receipt_date(value, today=TODAY, allow_older=True)


@pytest.mark.parametrize(
    "role,override,expected",
    [
        (app.ROLE_ADMIN, None, True),
        (app.ROLE_AUDIT, None, True),
        (app.ROLE_MEDICAL_AUDIT, None, False),
        (app.ROLE_AUX, None, False),
        (app.ROLE_ADMIN, False, False),
        (app.ROLE_AUX, True, True),
    ],
)
def test_individual_exception_has_priority_over_role(role, override, expected):
    overrides = {} if override is None else {PERMISSION: override}
    user = {"role": role, "permission_overrides": overrides}
    assert app.can_bill_older_attention(user) is expected
    assert validate_overrides(overrides, app.PERMISSION_LABELS) == overrides
    if expected:
        assert app.validate_receipt_date_for_user("2026-10-01", user, today=TODAY) == 1
    else:
        with pytest.raises(PermissionError):
            app.validate_receipt_date_for_user("2026-10-01", user, today=TODAY)


def test_inactive_user_cannot_use_exception():
    assert not app.can_bill_older_attention(
        {
            "role": app.ROLE_ADMIN,
            "is_active": 0,
            "permission_overrides": {PERMISSION: True},
        }
    )


@pytest.mark.parametrize("scope", ["CURRENT", "INHERITED", "HISTORICAL"])
def test_granted_exception_does_not_skip_source_or_receipt_guards(scope):
    user = {"role": app.ROLE_AUX, "permission_overrides": {PERMISSION: True}}
    attention = {
        "operational_source_id": "S",
        "turn_id": 1,
        "turn_scope": scope,
        "explicitly_inherited": scope == "INHERITED",
        "within_history_billing_window": False,
    }
    current = {"operational_source_id": "S", "turn_id": 1 if scope == "CURRENT" else 2}
    assert app.evaluate_admission_billing_access(attention, user, current)[
        "can_use_for_billing"
    ]
    attention.update(
        linked_receipt_id=17,
        linked_billing_status="FACTURADO",
        linked_document_status="FINAL",
    )
    assert not app.evaluate_admission_billing_access(attention, user, current)[
        "can_use_for_billing"
    ]
    attention.update(
        operational_source_id="OTHER",
        linked_receipt_id=None,
        linked_billing_status="SIN_RECIBO",
        linked_document_status="",
    )
    assert not app.evaluate_admission_billing_access(attention, user, current)[
        "can_use_for_billing"
    ]


def test_deny_applies_to_administrator_history_time_but_not_current_scope():
    user = {"role": app.ROLE_ADMIN, "permission_overrides": {PERMISSION: False}}
    attention = {
        "operational_source_id": "S",
        "turn_id": 1,
        "within_history_billing_window": False,
    }
    current = {"operational_source_id": "S", "turn_id": 2}
    result = app.evaluate_admission_billing_access(attention, user, current)
    assert result["reason_code"] == "HISTORICAL_TIME_DENIED"
    assert not result["can_use_for_billing"]
    attention["within_history_billing_window"] = True
    assert app.evaluate_admission_billing_access(attention, user, current)[
        "can_use_for_billing"
    ]
    attention["turn_id"] = 2
    attention["within_history_billing_window"] = False
    assert app.evaluate_admission_billing_access(attention, user, current)[
        "can_use_for_billing"
    ]


def test_saving_rechecks_permission_before_writing_even_if_flag_is_forged(monkeypatch):
    user = {
        "username": "operator",
        "role": app.ROLE_ADMIN,
        "permission_overrides": {PERMISSION: False},
    }
    monkeypatch.setattr(app, "get_user", lambda _name: user)
    connection = Mock(side_effect=AssertionError("No receipt may be written"))
    monkeypatch.setattr(app, "db_connect", connection)
    with pytest.raises(PermissionError, match="48 horas"):
        app.save_receipt_with_items(
            None,
            1,
            "SYNTHETIC",
            "2020-01-01",
            "TEST",
            "FUTURO",
            0,
            100,
            "",
            "operator",
            False,
            "",
            [],
            authorization_number="123456",
            verification_bypass={"reason": "SYNTHETIC CONTROLLED TEST"},
        )
    connection.assert_not_called()


def test_offline_worker_rejects_old_date_before_enqueuing(monkeypatch):
    from tests.test_local_receipts import receipt_job

    job = receipt_job()
    job["date_str"] = "2020-01-01"
    job["current_user"]["permission_overrides"] = {PERMISSION: False}
    job["current_user"]["_offline_login"] = True
    worker = app.PDFDatabaseWorker("demo", "session-demo")
    worker.signals = Mock()
    prepare = Mock(side_effect=AssertionError("Must not enqueue"))
    monkeypatch.setattr(worker, "_prepare_local_job", prepare)
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    worker.process(job)
    prepare.assert_not_called()
    assert worker.signals.finished_signal.emit.call_args.args[0] is False
    assert "48 horas" in worker.signals.finished_signal.emit.call_args.args[1]


def test_reopening_existing_local_pdf_does_not_require_age_exception(
    tmp_path, monkeypatch
):
    from receipt_continuity import local_receipt_store
    from tests.test_local_receipts import receipt_job

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    monkeypatch.setattr(
        "receipt_continuity.render_local_receipt", Mock(return_value="existing.pdf")
    )
    job = receipt_job()
    job["date_str"] = "2020-01-01"
    job["current_user"]["role"] = app.ROLE_AUX
    job["current_user"]["permission_overrides"] = {PERMISSION: False}
    store = local_receipt_store()
    identity = store.enqueue(job)
    job["reopen_local_request"] = identity
    worker = app.PDFDatabaseWorker("demo", "session-demo")
    worker.signals = Mock()
    number = Mock(side_effect=AssertionError("Reopening must not create a receipt"))
    monkeypatch.setattr(app, "get_next_recibo_number", number)

    worker.process(job)

    number.assert_not_called()
    assert len(store.rows("demo")) == 1
    result = worker.signals.finished_signal.emit.call_args.args
    assert result[0] is True
    assert result[1].startswith("__LOCAL_RECEIPT_REOPENED__:")
    assert result[2] == "existing.pdf"
