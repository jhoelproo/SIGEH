from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import CALCULOS_QT as app


@pytest.mark.parametrize("recovered", [False, True])
def test_success_preserves_session_without_recovery_modal(recovered):
    dialog = SimpleNamespace(lbl_error=Mock(), accept=Mock())
    result = {
        "status": "OK",
        "user": {"username": "test"},
        "session_id": "session",
        "startup_data": {"ready": True},
        "recovered_same_device": recovered,
    }
    with patch.object(app.QMessageBox, "information") as information:
        app.LoginDialog._login_completed(dialog, result)
    information.assert_not_called()
    assert dialog.user == result["user"]
    assert dialog.session_id == "session"
    assert dialog.startup_data == {"ready": True}
    dialog.accept.assert_called_once()


@pytest.mark.parametrize("status", ["INVALID", "LOCAL_SESSION_ACTIVE", "ERROR", ""])
def test_failed_authentication_is_not_silenced(status):
    dialog = SimpleNamespace(_show_login_error=Mock(), password=Mock(), accept=Mock())
    app.LoginDialog._login_completed(dialog, {"status": status})
    dialog._show_login_error.assert_called_once()
    dialog.accept.assert_not_called()
