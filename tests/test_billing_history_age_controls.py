"""An age exception changes the time window, not insurer or coverage access."""

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication, QLabel

import CALCULOS_QT as app


AGE = app.PERMISSION_BILL_OLDER_ATTENTION
UNINSURED = app.PERMISSION_VIEW_UNINSURED


def operator(role, age, uninsured):
    return {
        "username": "SYNTHETIC",
        "role": role,
        "is_active": 1,
        "permission_overrides": {AGE: age, UNINSURED: uninsured},
    }


@pytest.mark.parametrize("role", [app.ROLE_AUX, app.ROLE_ADMIN])
@pytest.mark.parametrize("age", [True, False])
@pytest.mark.parametrize("uninsured", [True, False])
def test_history_query_keeps_age_insurer_and_coverage_independent(
    monkeypatch, role, age, uninsured
):
    monkeypatch.setattr(app, "write_runtime_log", Mock())
    reader = Mock()
    reader.current_operational_context.return_value = {
        "source_instance_id": "STATION",
        "operational_source_id": "SOURCE",
        "turn_id": 17,
    }
    reader.fetch_all.return_value = ([], {})
    user = operator(role, age, uninsured)
    result = app.BillingAdmissionQueryService(
        central_reader=reader
    ).load_admission_history_batch(current_user=user, coverage_filter="TODOS")
    sql, parameters = reader.fetch_all.call_args.args
    assert "WHERE (%s OR" in sql
    assert parameters[2] is (role == app.ROLE_ADMIN)
    assert parameters[4] is age
    assert result["full_history"] is age
    assert "ASEGURADOS" in parameters if not uninsured else "TODOS" in parameters
    assert app.can_override_admission_billing_claim(user) is (role == app.ROLE_ADMIN)


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("role", [app.ROLE_AUX, app.ROLE_ADMIN])
@pytest.mark.parametrize("age", [True, False])
@pytest.mark.parametrize("uninsured", [True, False])
def test_history_controls_match_effective_age_and_coverage_permissions(
    qt_application, role, age, uninsured
):
    dialog = app.AdmissionHistoryDialog(
        operator(role, age, uninsured), initial_search=False
    )
    try:
        dialog.show()
        qt_application.processEvents()
        assert dialog._full_history is age
        expected_scope = (
            "Todo el historial" if age else "Últimos 2 días + turno vigente"
        )
        assert dialog.turn_combo.itemText(0) == expected_scope
        subtitle = next(
            label.text()
            for label in dialog.findChildren(QLabel)
            if label.text().startswith("Consulta progresiva")
        )
        assert ("historial completo" in subtitle) is age
        assert dialog.coverage_combo.isVisible() is uninsured
        choices = [
            dialog.coverage_combo.itemData(row)
            for row in range(dialog.coverage_combo.count())
        ]
        assert choices == (
            ["TODOS", "ASEGURADOS", "SIN_SEGURO"] if uninsured else ["ASEGURADOS"]
        )
        assert dialog._filters()["coverage_filter"] == (
            "TODOS" if uninsured else "ASEGURADOS"
        )
    finally:
        dialog.close()
        dialog.coverage_combo.close()
