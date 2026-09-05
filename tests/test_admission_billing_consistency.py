"""Regression contracts for the four operational consistency incidents."""

import inspect
from unittest.mock import Mock, patch

import pytest

import CALCULOS_QT as app
from admission_billing_consistency import (
    CURRENT_OPERATIONAL_SHIFT_SQL,
    ars_enabled_sql,
    foreign_claim_sql,
    normalized_name_sql,
)


def test_every_billing_current_turn_query_uses_product_epoch():
    for function in (
        app.BillingAdmissionQueryService.get_operational_candidates,
        app.BillingAdmissionQueryService.load_admission_history_batch,
        app.get_projected_billable_attention,
        app.claim_projected_billable_attention,
        app.evaluate_attention_billing_eligibility,
    ):
        assert "CURRENT_OPERATIONAL_SHIFT_SQL" in inspect.getsource(function)
    assert (
        "production_epoch_id=session.production_epoch_id"
        in CURRENT_OPERATIONAL_SHIFT_SQL
    )


def test_revalidation_rejects_urgency_before_mapping():
    source = inspect.getsource(app.get_projected_billable_attention)
    assert "service_type" in source
    assert "EMERGENCIA" in source


def test_cancel_has_owned_claim_release():
    source = inspect.getsource(app.MainWindow.reset_all)
    assert "release" in source


def test_save_uses_global_identity():
    source = inspect.getsource(app.MainWindow.generate_pdf)
    assert "global_attention_id=" in source


def test_claim_override_cannot_steal_live_claim():
    source = inspect.getsource(app.claim_projected_billable_attention)
    assert "bool(allow_claim_override)" not in source


def test_claim_release_never_deletes_history_or_another_session():
    from unittest.mock import MagicMock

    connection = MagicMock()
    connection.__enter__.return_value = connection
    with patch.object(app, "db_connect", return_value=connection):
        app.release_admission_billing_claim(
            {
                "attention_id": 17,
                "source_instance_id": "SOURCE",
                "billing_claim_acquired_at": "2026-09-04T12:00:00Z",
            },
            session_id="OWN",
        )
    sql, params = connection.execute.call_args.args
    assert "UPDATE admission_billing_claims" in sql
    assert "session_id=%s" in sql and "receipt_id IS NULL" in sql
    assert params == ("SOURCE", 17, "OWN", "2026-09-04T12:00:00Z")
    assert "DELETE" not in sql


@pytest.mark.parametrize(
    "builder", [ars_enabled_sql, foreign_claim_sql, normalized_name_sql]
)
def test_sql_fragments_reject_untrusted_aliases(builder):
    with pytest.raises(ValueError):
        builder("p; DROP TABLE users")


@pytest.mark.parametrize("data,session", [({}, "A"), ({"attention_id": 1}, "")])
def test_empty_release_never_contacts_database(data, session, monkeypatch):
    connect = Mock(side_effect=AssertionError("Unexpected network"))
    monkeypatch.setattr(app, "db_connect", connect)
    app.release_admission_billing_claim(data, session_id=session)
    app.schedule_admission_claim_release(data, session_id=session)
    connect.assert_not_called()


@pytest.mark.parametrize("failure", [False, True])
def test_release_runs_in_worker_with_captured_ownership(monkeypatch, failure):
    tasks = []

    def thread(**kwargs):
        assert kwargs["daemon"] and kwargs["name"] == "BillingClaimRelease"
        return Mock(start=lambda: tasks.append(kwargs["target"]))

    release = Mock(side_effect=RuntimeError("unavailable") if failure else None)
    log = Mock()
    monkeypatch.setattr(app.threading, "Thread", thread)
    monkeypatch.setattr(app, "release_admission_billing_claim", release)
    monkeypatch.setattr(app, "log_billing_admission_query_failure", log)
    data = {"attention_id": 1, "billing_claim_acquired_at": "original"}
    app.schedule_admission_claim_release(data, session_id="A")
    release.assert_not_called()  # no I/O on the caller/UI thread
    data["billing_claim_acquired_at"] = "new"
    tasks.pop()()
    assert release.call_args.args[0]["billing_claim_acquired_at"] == "original"
    assert log.call_count == int(failure)


@pytest.mark.parametrize("attention", [object(), ["invalid"]])
def test_invalid_form_never_blocks_logout_or_assumes_claim_ownership(
    monkeypatch, attention
):
    release = Mock()
    log = Mock()
    window = Mock(current_admission_attention=attention, session_id="A")
    monkeypatch.setattr(app, "release_admission_billing_claim", release)
    monkeypatch.setattr(app, "log_billing_admission_query_failure", log)
    app.MainWindow._clear_sensitive_session_references(window)
    assert window.current_user == {} and window.session_id == ""
    assert window.current_admission_attention is None
    release.assert_not_called()
    log.assert_called_once()
    assert log.call_args.kwargs["sql_stage"] == "CLAIM_RELEASE_SNAPSHOT"


def selected_attention(**changes):
    return app._attention_from_projection(
        {
            "attention_id": 329,
            "source_instance_id": "ORIGIN",
            "global_attention_id": "22222222-2222-4222-8222-222222222222",
            "billing_claim_acquired_at": "2026-09-04T12:00:00Z",
            "canonical_ars": "FUTURO",
            **changes,
        }
    )


@pytest.mark.parametrize("preserve", [False, True])
def test_cancel_form_expires_claim_but_reselect_same_attention_preserves_it(
    monkeypatch, preserve
):
    attention = selected_attention()
    window = Mock(current_admission_attention=attention.snapshot(), session_id="A")
    release = Mock()
    monkeypatch.setattr(app, "schedule_admission_claim_release", release)
    monkeypatch.setattr(app, "set_button_role", Mock())
    app.MainWindow.reset_all(window, preserve_claim=attention if preserve else None)
    assert release.call_count == int(not preserve)
    assert window.current_admission_attention is None


@pytest.mark.parametrize("editing", [None, 17])
def test_refuse_replace_patient_releases_new_claim(monkeypatch, editing):
    attention = selected_attention()
    window = Mock(editing_recibo_id=editing, session_id="A")
    window.cart_table.rowCount.return_value = 1
    release = Mock()
    monkeypatch.setattr(app, "schedule_admission_claim_release", release)
    monkeypatch.setattr(
        app.QMessageBox, "question", Mock(return_value=app.QMessageBox.No)
    )
    app.MainWindow._complete_verified_admission(window, attention)
    release.assert_called_once_with(attention, session_id="A")
    window._apply_admission_attention.assert_not_called()


def test_accept_patient_preserves_new_claim_through_form_reset(monkeypatch):
    attention = selected_attention()
    window = Mock(editing_recibo_id=None, session_id="A")
    window.cart_table.rowCount.return_value = 0
    window.name_edit.text.return_value = ""
    monkeypatch.setattr(app, "FloatingToast", Mock())
    app.MainWindow._complete_verified_admission(window, attention)
    window.reset_all.assert_called_once_with(preserve_claim=attention)
    window._apply_admission_attention.assert_called_once_with(attention)


@pytest.mark.parametrize("case", ["excluded", "error", "eligible"])
def test_receipt_save_revalidates_uuid_and_preserves_claim_token(monkeypatch, case):
    attention = selected_attention()
    window = Mock(
        receipt_read_only=False,
        editing_recibo_id=None,
        current_admission_attention=attention.snapshot(),
        current_user={},
        session_id="A",
    )
    window.name_edit.text.return_value = ""  # stop before any receipt write
    live = (
        selected_attention(billing_claim_acquired_at="") if case == "eligible" else None
    )
    query = Mock(
        return_value=live,
        side_effect=RuntimeError("controlled") if case == "error" else None,
    )
    release = Mock()
    monkeypatch.setattr(app, "get_projected_billable_attention", query)
    monkeypatch.setattr(app, "schedule_admission_claim_release", release)
    monkeypatch.setattr(app.QMessageBox, "critical", Mock())
    monkeypatch.setattr(app.QMessageBox, "warning", Mock())
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    monkeypatch.setattr(app, "FloatingToast", Mock())
    app.MainWindow.generate_pdf(window)
    assert (
        query.call_args.kwargs["global_attention_id"] == attention.global_attention_id
    )
    assert release.call_count == int(case == "excluded")
    assert (
        window.current_admission_attention["billing_claim_acquired_at"]
        == attention.billing_claim_acquired_at
    )
