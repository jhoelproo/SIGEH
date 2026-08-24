from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from admission_v15_adapter import EmbeddedMainAppGateway


@dataclass(frozen=True)
class UserOption:
    username: str
    full_name: str
    role: str
    user_id: str = ""


USERS = [
    {"id": 1, "username": "admin-a", "full_name": "ADMIN A", "role": "administrador", "is_active": 1},
    {"id": 2, "username": "admin-b", "full_name": "ADMIN B", "role": "Administrador", "is_active": 1},
    {"id": 3, "username": "aux-test", "full_name": "AUX TEST", "role": "auxiliar", "is_active": 1},
    {"id": 4, "username": "genesis", "full_name": "GENESIS TORRES", "role": "auxiliar", "is_active": 1},
    {"id": 5, "username": "admin-disabled", "full_name": "ADMIN DISABLED", "role": "administrador", "is_active": 0},
]


def _gateway(*, current_user=None, audit=None, users=None):
    active_users = USERS if users is None else users

    def verify(username, password):
        if password != f"clave-{username}":
            return None
        return next(
            (
                dict(user) for user in active_users
                if user["username"] == username and user["is_active"]
            ),
            None,
        )

    return EmbeddedMainAppGateway(
        current_user=current_user or USERS[2],
        session_checker=lambda: True,
        users_provider=lambda: list(active_users),
        credential_verifier=verify,
        audit_callback=(
            (lambda username, details: audit.append((username, details)))
            if audit is not None else None
        ),
        gateway_error_class=RuntimeError,
        representative_class=UserOption,
    )


def _authorize(gateway, admin_id=1, admin_username="admin-a", password="clave-admin-a"):
    return gateway.authorize_admin_action(
        selected_admin_user_id=admin_id,
        selected_admin_username=admin_username,
        password=password,
        action="CORRECT_ADMISSION_REPRESENTATIVE",
        target_user_id=4,
        target_username="genesis",
    )


def test_logged_admin_can_authorize_with_own_credentials():
    admin, target = _authorize(_gateway(current_user=USERS[0]))

    assert admin.user_id == "1"
    assert target.username == "genesis"


def test_logged_auxiliary_can_use_offline_admin_authorizer():
    admin, target = _authorize(_gateway(current_user=USERS[2]))

    assert admin.username == "admin-a"
    assert target.username == "genesis"


def test_admin_catalog_contains_only_enabled_administrators():
    gateway = _gateway(current_user=USERS[2])

    assert [user.username for user in gateway.list_active_administrators()] == [
        "admin-a",
        "admin-b",
    ]


def test_wrong_admin_password_is_denied_without_target_authorization():
    with pytest.raises(RuntimeError, match="Credenciales de Administrador incorrectas"):
        _authorize(_gateway(), password="incorrecta")


def test_manipulated_non_admin_authorizer_is_denied():
    with pytest.raises(RuntimeError, match="rol Administrador"):
        _authorize(
            _gateway(), admin_id=3, admin_username="aux-test", password="clave-aux-test"
        )


def test_disabled_admin_is_rejected_after_dialog_cache():
    with pytest.raises(RuntimeError, match="ya no está habilitado"):
        _authorize(
            _gateway(),
            admin_id=5,
            admin_username="admin-disabled",
            password="clave-admin-disabled",
        )


def test_logged_admin_a_can_select_admin_b_and_audit_three_identities():
    audit = []
    gateway = _gateway(current_user=USERS[0], audit=audit)

    admin, target = _authorize(
        gateway, admin_id=2, admin_username="admin-b", password="clave-admin-b"
    )

    assert admin.username == "admin-b"
    assert target.username == "genesis"
    assert audit[0][0] == "admin-b"
    assert "requesting_user_id=1" in audit[0][1]
    assert "authorizing_admin_user_id=2" in audit[0][1]
    assert "target_representative_user_id=4" in audit[0][1]


def test_admin_selector_source_is_readonly_combobox_not_current_user_entry():
    source = (
        Path(__file__).resolve().parents[1]
        / "ADMISION_PYSIDE6_V15"
        / "facturacion_tabs_pyside6.py"
    ).read_text(encoding="utf-8")
    start = source.index("                def abrir_dialogo_confirmacion(representante):")
    end = source.index("                def corregir_turno_actual():", start)
    dialog = source[start:end]

    assert 'dialogo.title("Confirmar cambio de representante")' in dialog
    assert 'text="Administrador que autoriza"' in dialog
    assert "admin_combo = tb.Combobox(" in dialog
    assert 'state="readonly"' in dialog
    assert "usuario_entry = tb.Entry(" not in dialog
    assert "authorize_admin_action(" in dialog
    assert "authorizing_admin=autorizado_por" in dialog
    assert "if not administradores:" in dialog
    assert 'aplicar_btn.configure(state="disabled")' in dialog
