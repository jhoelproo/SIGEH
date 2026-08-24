import base64
from multiprocessing.connection import Client
import os

import pytest

from admission_auth_broker import (
    AUTH_KEY_ENV,
    AUTH_PIPE_ENV,
    AdmissionAuthBroker,
)


def _users():
    return [
        {
            "username": "admin",
            "full_name": "Administrador Principal",
            "role": "administrador",
            "is_active": 1,
            "password_hash": "never expose",
        },
        {
            "username": "aux.01",
            "full_name": "Auxiliar Uno",
            "role": "auxiliar",
            "is_active": 1,
        },
        {
            "username": "disabled",
            "full_name": "Usuario Inactivo",
            "role": "auxiliar",
            "is_active": 0,
        },
        {
            "username": "medical.audit",
            "full_name": "Auditor Médico",
            "role": "auditoria medica y cuentas",
            "is_active": 1,
        },
    ]


def _verifier(username, password):
    if username == "admin" and password == "correcta":
        return _users()[0]
    return None


def _broker(audit=None):
    return AdmissionAuthBroker(
        session_id="test-session",
        current_user=_users()[0],
        users_provider=_users,
        credential_verifier=_verifier,
        audit_callback=(
            (lambda username, details: audit.append((username, details)))
            if audit is not None
            else None
        ),
    )


def test_representatives_expose_only_safe_active_fields() -> None:
    result = _broker()._handle({"action": "list_representatives"})
    assert result["ok"] is True
    assert [item["username"] for item in result["representatives"]] == [
        "admin",
        "aux.01",
    ]
    assert all("password_hash" not in item for item in result["representatives"])


def test_shift_change_requires_current_session_credentials_and_same_target() -> None:
    broker = _broker()
    wrong_user = broker._handle(
        {
            "action": "authorize_shift_change",
            "username": "aux.01",
            "password": "correcta",
            "target_username": "aux.01",
        }
    )
    wrong_password = broker._handle(
        {
            "action": "authorize_shift_change",
            "username": "admin",
            "password": "incorrecta",
            "target_username": "aux.01",
        }
    )
    disabled = broker._handle(
        {
            "action": "authorize_shift_change",
            "username": "admin",
            "password": "correcta",
            "target_username": "disabled",
        }
    )
    assert wrong_user["ok"] is False
    assert wrong_password["ok"] is False
    assert disabled["ok"] is False


def test_success_returns_canonical_representative_and_audits() -> None:
    audit = []
    result = _broker(audit)._handle(
        {
            "action": "authorize_shift_change",
            "username": "admin",
            "password": "correcta",
            "target_username": "admin",
        }
    )
    assert result["ok"] is True
    assert result["authorized_by"] == "admin"
    assert result["representative"]["full_name"] == "Administrador Principal"
    assert audit and audit[0][0] == "admin"


def test_session_status_exposes_only_current_safe_identity() -> None:
    result = _broker()._handle({"action": "session_status"})
    assert result == {
        "ok": True,
        "username": "admin",
        "full_name": "Administrador Principal",
        "role": "administrador",
    }


def test_auxiliary_can_confirm_only_its_own_session_identity() -> None:
    auxiliary = _users()[1]
    broker = AdmissionAuthBroker(
        session_id="aux-session",
        current_user=auxiliary,
        users_provider=_users,
        credential_verifier=lambda username, password: (
            auxiliary
            if username == "aux.01" and password == "clave-aux"
            else None
        ),
    )
    own = broker._handle(
        {
            "action": "authorize_shift_change",
            "username": "aux.01",
            "password": "clave-aux",
            "target_username": "aux.01",
        }
    )
    other = broker._handle(
        {
            "action": "authorize_shift_change",
            "username": "aux.01",
            "password": "clave-aux",
            "target_username": "admin",
        }
    )
    assert own["ok"] is True
    assert other["ok"] is False
    assert "inicia con ese usuario" in other["message"]


@pytest.mark.skipif(os.name != "nt", reason="Named pipes are Windows-specific")
def test_authenticated_named_pipe_round_trip() -> None:
    broker = _broker()
    environment = broker.environment()
    key = base64.urlsafe_b64decode(environment[AUTH_KEY_ENV].encode("ascii"))
    try:
        with Client(
            environment[AUTH_PIPE_ENV],
            family="AF_PIPE",
            authkey=key,
        ) as connection:
            connection.send({"action": "list_representatives"})
            result = connection.recv()
        assert result["ok"] is True
        assert len(result["representatives"]) == 2
    finally:
        broker.stop()
