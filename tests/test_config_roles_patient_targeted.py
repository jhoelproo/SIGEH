from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ADMISSION_SOURCE = ROOT / "admission_source"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ADMISSION_SOURCE) not in sys.path:
    sys.path.insert(0, str(ADMISSION_SOURCE))

from admission_hybrid import (  # noqa: E402
    AdmissionWriteBlocked,
    OperationalSessionService,
    StationRole,
    evaluate_admission_access,
)


def _load_operational_fake():
    path = ROOT / "tests" / "test_operational_primary_transition.py"
    spec = importlib.util.spec_from_file_location("_operational_target_fake", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"No se pudo cargar {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._OperationalDB()


def test_config_roles_remote_primary_transfer_uses_authenticated_admin_session(
    tmp_path: Path,
):
    aux = evaluate_admission_access(
        {"role": "auxiliar"},
        {
            "base_write_allowed": True,
            "device_role": "PRIMARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    admin_secondary = evaluate_admission_access(
        {"role": "administrador"},
        {
            "base_write_allowed": True,
            "device_role": "SECONDARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    admin_primary = evaluate_admission_access(
        {"role": "admin"},
        {
            "base_write_allowed": True,
            "device_role": "PRIMARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    audit = evaluate_admission_access(
        {"role": "facturador de auditoría"},
        {
            "base_write_allowed": True,
            "device_role": "PRIMARY",
            "connection_state": "CONNECTED",
            "status": "ACTIVE",
        },
    )
    assert aux.write_allowed and aux.can_generate_attention
    assert not aux.can_manage_primary and not aux.can_change_turn
    assert admin_secondary.view_allowed and admin_secondary.write_allowed
    assert admin_secondary.can_manage_primary and not admin_secondary.can_change_turn
    assert admin_primary.can_manage_primary and admin_primary.can_change_turn
    assert admin_primary.write_allowed
    assert audit.view_allowed and not audit.write_allowed
    assert not audit.can_manage_primary and not audit.can_change_turn

    database = _load_operational_fake()
    service = OperationalSessionService(lambda: database)
    service.attach_device(
        login_username="admin",
        login_user_id=7,
        login_role="administrador",
        device_id="PC-1",
        login_session_id="ADMIN-P1",
        turn_id=500,
    )
    # The current policy requires an Administrator to create the very first
    # operational context.  This fake then represents the separately audited
    # representative correction that is outside this transfer-focused test.
    database.session.update(
        {
            "active_username": "auxiliar_a",
            "active_user_id": "22",
            "active_user_display_name": "Auxiliar A",
            "turn_id": 500,
        }
    )
    primary = service.attach_device(
        device_id="PC-1",
        login_username="auxiliar_a",
        login_user_id=22,
        login_role="auxiliar",
        login_session_id="AUX-P1",
        turn_id=500,
    )
    before = primary.operational_session
    assert primary.role is StationRole.PRIMARY
    assert primary.writable

    admin_login = service.attach_device(
        login_username="admin",
        login_user_id=7,
        login_role="administrador",
        device_id="PC-2",
        login_session_id="ADMIN-S2",
        turn_id=999,
    )
    audit_login = service.attach_device(
        login_username="audit",
        login_user_id=9,
        login_role="facturador de auditoría",
        device_id="PC-3",
        login_session_id="AUDIT-S3",
        turn_id=999,
    )
    assert admin_login.role is StationRole.SECONDARY and admin_login.writable
    assert audit_login.role is StationRole.NONE and not audit_login.writable
    assert database.session["primary_device_id"] == "PC-1"
    assert database.session["turn_id"] == 500
    assert database.session["active_user_id"] == "22"
    assert database.session["generation"] == before.generation

    database.active_sessions["ADMIN-S2"] = True
    database.active_session_users["ADMIN-S2"] = {
        "username": "admin",
        "device_id": "PC-2",
        "user_id": "7",
        "role": "administrador",
    }
    changed = service.force_transfer_admission_primary(
        operational_session_id=before.operational_session_id,
        target_device_id="PC-2",
        target_login_session_id="ADMIN-S2",
        actor_device_id="PC-2",
        actor_login_session_id="ADMIN-S2",
        expected_operational_revision=before.operational_revision,
        admin_user_id=7,
        admin_username="admin",
        admin_role="administrador",
        reason="Transferencia administrativa explícita",
    )
    assert changed.primary_device_id == "PC-2"
    assert changed.turn_id == before.turn_id == 500
    assert changed.active_user_id == before.active_user_id == "22"
    assert changed.generation == before.generation
    assert changed.lease_generation == before.lease_generation + 1
    assert database.active_sessions["AUX-P1"] is True
    assert database.devices["PC-1"]["invalidated_reason"] is None

    database.active_sessions["AUDIT-S3"] = True
    database.active_session_users["AUDIT-S3"] = {
        "username": "audit",
        "device_id": "PC-3",
        "user_id": "9",
        "role": "facturador de auditoria",
    }
    with pytest.raises(AdmissionWriteBlocked):
        service.force_transfer_admission_primary(
            operational_session_id=before.operational_session_id,
            target_device_id="PC-3",
            target_login_session_id="AUDIT-S3",
            actor_device_id="PC-3",
            actor_login_session_id="AUDIT-S3",
            expected_operational_revision=changed.operational_revision,
            admin_user_id=9,
            admin_username="audit",
            admin_role="facturador de auditoria",
            reason="No autorizada",
        )
