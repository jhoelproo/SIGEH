from __future__ import annotations

from types import SimpleNamespace

import pytest

from primary_transfer_ui import request_primary_transfer


class _Messages:
    def __init__(self, *, confirmed: bool = True):
        self.confirmed = confirmed
        self.calls: list[tuple[str, tuple, dict]] = []

    def _record(self, kind, *args, **kwargs):
        self.calls.append((kind, args, kwargs))

    def showwarning(self, *args, **kwargs):
        self._record("warning", *args, **kwargs)

    def showinfo(self, *args, **kwargs):
        self._record("info", *args, **kwargs)

    def showerror(self, *args, **kwargs):
        self._record("error", *args, **kwargs)

    def askyesno(self, *args, **kwargs):
        self._record("confirm", *args, **kwargs)
        return self.confirmed


class _Dialogs:
    def __init__(self, *, reason="Motivo", selected=1):
        self.reason = reason
        self.selected = selected
        self.selection_prompt = ""

    def askstring(self, *_args, **_kwargs):
        return self.reason

    def askinteger(self, _title, prompt, **_kwargs):
        self.selection_prompt = prompt
        return self.selected


class _Logger:
    def __init__(self):
        self.critical_calls = []
        self.error_calls = []

    def critical(self, *args, **kwargs):
        self.critical_calls.append((args, kwargs))

    def error(self, *args, **kwargs):
        self.error_calls.append((args, kwargs))


class _Runtime:
    offline = False
    device_id = "PC-2"

    def __init__(self, *, candidates=None, state=None, changed=None, error=None):
        self.candidates = candidates if candidates is not None else _healthy_stations()
        self.current_state = state if state is not None else _valid_state()
        self.changed = changed if changed is not None else _changed()
        self.error = error
        self.transfer_calls = []

    def state(self):
        return self.current_state

    def list_primary_transfer_candidates(self):
        if self.error == "list":
            raise RuntimeError("fallo de lista")
        return self.candidates

    def force_transfer_admission_primary(self, **kwargs):
        self.transfer_calls.append(kwargs)
        if self.error == "transfer":
            raise RuntimeError("fallo de transferencia")
        return self.changed


class _App:
    def __init__(self, runtime):
        self.db = SimpleNamespace(_runtime=runtime)
        self._primary_transfer_in_progress = False
        self.actions_refreshes = 0
        self.controls = []
        self.statuses = []
        self.panel_refreshes = 0

    def _refresh_actions_menu_state(self):
        self.actions_refreshes += 1

    def _set_turn_change_controls_enabled(self, enabled):
        self.controls.append(enabled)

    def set_status(self, *args):
        self.statuses.append(args)

    def _refresh_primary_config_panel(self):
        self.panel_refreshes += 1

    @staticmethod
    def _ejecutar_en_segundo_plano(_message, function, al_terminar=None, al_error=None):
        try:
            result = function()
        except Exception as exc:
            al_error(exc)
        else:
            al_terminar(result)


def _valid_state():
    return {
        "operational_session_id": "SESSION-1",
        "operational_revision": 9,
        "primary_device_id": "PC-1",
        "turn_id": 350,
        "generation": 4,
        "active_user_id": "USER-1",
    }


def _changed(**overrides):
    values = {
        "primary_device_id": "PC-2",
        "turn_id": 350,
        "generation": 4,
        "active_user_id": "USER-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _healthy_stations(*, multiple=False):
    stations = [
        {
            "device_id": "PC-1",
            "device_name": "Admisión 1",
            "station_role": "PRIMARY",
            "login_session_id": "LOGIN-1",
            "login_username": "admin",
            "last_seen": "ahora",
            "health_status": "HEALTHY",
            "sync_status": "NOT_REPORTED",
        },
        {
            "device_id": "PC-2",
            "device_name": "Admisión 2",
            "station_role": "SECONDARY",
            "login_session_id": "LOGIN-2",
            "login_username": "operador",
        },
    ]
    if multiple:
        stations.append(
            {
                "device_id": "PC-3",
                "station_role": "SECONDARY",
                "login_session_id": "LOGIN-3",
            }
        )
    return stations


def _run(runtime=None, *, is_admin=True, messages=None, dialogs=None, app=None):
    runtime = runtime if runtime is not None else _Runtime()
    app = app if app is not None else _App(runtime)
    messages = messages if messages is not None else _Messages()
    dialogs = dialogs if dialogs is not None else _Dialogs()
    logger = _Logger()
    request_primary_transfer(
        app,
        parent="parent",
        messagebox=messages,
        simpledialog=dialogs,
        logger=logger,
        is_admin=is_admin,
    )
    return app, runtime, messages, dialogs, logger


@pytest.mark.parametrize("scenario", ["not_admin", "missing", "offline", "busy", "identity"])
def test_transfer_rejects_invalid_initial_state_without_mutation(scenario):
    runtime = _Runtime()
    app = _App(runtime)
    is_admin = True
    if scenario == "not_admin":
        is_admin = False
    elif scenario == "missing":
        app.db._runtime = None
    elif scenario == "offline":
        runtime.offline = True
    elif scenario == "busy":
        app._primary_transfer_in_progress = True
    else:
        runtime.current_state = {"operational_revision": 0}

    app, runtime, messages, *_ = _run(runtime, is_admin=is_admin, app=app)
    assert runtime.transfer_calls == []
    assert app._primary_transfer_in_progress is (scenario == "busy")
    if scenario != "busy":
        assert any(kind == "warning" for kind, *_rest in messages.calls)


@pytest.mark.parametrize(
    ("candidates", "dialog", "confirmed", "expected_kind"),
    [
        ([], _Dialogs(), True, "warning"),
        (_healthy_stations()[:1], _Dialogs(), True, "info"),
        (_healthy_stations(multiple=True), _Dialogs(selected=None), True, None),
        (_healthy_stations(), _Dialogs(reason=None), True, None),
        (_healthy_stations(), _Dialogs(reason="   "), True, "warning"),
        (_healthy_stations(), _Dialogs(), False, "confirm"),
    ],
)
def test_transfer_candidate_and_confirmation_cancellations_are_absolute_noops(
    candidates, dialog, confirmed, expected_kind
):
    runtime = _Runtime(candidates=candidates)
    app, runtime, messages, *_ = _run(
        runtime,
        dialogs=dialog,
        messages=_Messages(confirmed=confirmed),
    )
    assert runtime.transfer_calls == []
    assert app._primary_transfer_in_progress is False
    if expected_kind:
        assert any(kind == expected_kind for kind, *_rest in messages.calls)


def test_transfer_selects_requested_secondary_and_preserves_identity():
    runtime = _Runtime(candidates=_healthy_stations(multiple=True))
    dialogs = _Dialogs(reason="  mantenimiento técnico  ", selected=2)
    app, runtime, messages, dialogs, logger = _run(runtime, dialogs=dialogs)
    assert runtime.transfer_calls == [
        {
            "target_device_id": "PC-3",
            "target_login_session_id": "LOGIN-3",
            "expected_operational_revision": 9,
            "reason": "mantenimiento técnico",
        }
    ]
    assert "Admisión 2" in dialogs.selection_prompt
    assert app.statuses[-1] == ("Conectado · Principal · Sincronizado", "ok")
    assert app.controls == [True]
    assert app.panel_refreshes == 1
    assert logger.critical_calls == []
    assert any(kind == "info" and args[0] == "Transferencia completada" for kind, args, _ in messages.calls)


def test_transfer_rejects_changed_identity_after_commit():
    runtime = _Runtime(changed=_changed(turn_id=351))
    app, _runtime, messages, _dialogs, logger = _run(runtime)
    assert app._primary_transfer_in_progress is False
    assert logger.critical_calls
    assert any(kind == "error" for kind, *_rest in messages.calls)


@pytest.mark.parametrize("failure_stage", ["list", "transfer"])
def test_transfer_failure_releases_progress_and_reports_error(failure_stage):
    runtime = _Runtime(error=failure_stage)
    app, _runtime, messages, _dialogs, logger = _run(runtime)
    assert app._primary_transfer_in_progress is False
    assert app.statuses[-1] == ("No se pudo transferir PRIMARY", "error")
    assert app.panel_refreshes == 1
    assert logger.error_calls
    assert any(kind == "error" for kind, *_rest in messages.calls)
