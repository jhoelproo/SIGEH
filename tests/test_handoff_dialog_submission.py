from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_handoff_ui import HandoffSubmission
from admission_v15_adapter import load_v15_application_module
from tests.test_turn_operation_dialog import dialog_callback


@pytest.mark.parametrize("committed", [False, True])
def test_actual_dialog_callback_only_retries_uncommitted_failure(committed):
    submission = HandoffSubmission()
    button = Mock()
    window = SimpleNamespace(
        can_change_admission_turn=Mock(return_value=(True, "", ""))
    )
    messages = Mock()

    def apply():
        if committed:
            submission.mark_committed()
        raise RuntimeError("synthetic failure")

    action = Mock(side_effect=apply)
    callback = dialog_callback(
        load_v15_application_module(),
        "aplicar_una_vez",
        aplicando=submission,
        self=window,
        win=Mock(winfo_exists=Mock(return_value=True)),
        aplicar_btn=button,
        aviso_var=Mock(),
        _aplicar_cambio=action,
        messagebox=messages,
        APP_LOG=Mock(),
    )
    for _ in range(10):
        callback()
    assert action.call_count == (1 if committed else 10)
    assert button.configure.call_args.kwargs["state"] == (
        "disabled" if committed else "normal"
    )
    assert not window._turn_change_committing
    assert ("ya se confirmó" in messages.showerror.call_args.args[1]) == committed


def test_policy_denial_leaves_dialog_available_without_submitting():
    action = Mock()
    submission = HandoffSubmission()
    callback = dialog_callback(
        load_v15_application_module(),
        "aplicar_una_vez",
        aplicando=submission,
        self=SimpleNamespace(
            can_change_admission_turn=lambda **_: (False, "", "Denied")
        ),
        win=Mock(),
        messagebox=Mock(),
        _aplicar_cambio=action,
    )
    callback()
    action.assert_not_called()
    assert not submission.blocked
