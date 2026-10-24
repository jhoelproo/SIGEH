"""Exercise administrative controls and live permission changes with synthetic data."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QDialogButtonBox
from PySide6.QtCore import QTimer

import CALCULOS_QT as app
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


@pytest.mark.parametrize(
    "ready,receipt,global_id,reasons,blocked",
    [
        (app.READINESS_READY, None, "", [], False),
        ("INCOMPLETA", None, "", [], True),
        ("INCOMPLETA", None, "", ["Falta NSS"], True),
        ("INCOMPLETA", 1, "", [], False),
        ("INCOMPLETA", None, "global-identity", [], False),
        ("", None, "", [], False),
    ],
)
def test_initial_readiness_defers_global_id_to_authoritative_transaction(
    ready, receipt, global_id, reasons, blocked
):
    attention = {
        "billing_readiness": ready,
        "global_attention_id": global_id,
        "readiness_reasons": reasons,
    }
    if blocked:
        with pytest.raises(ValueError, match="no está completa"):
            app._require_initial_attention_readiness(attention, receipt)
    else:
        app._require_initial_attention_readiness(attention, receipt)


@pytest.mark.parametrize(
    "old,new,grant,expected",
    [
        (None, "FUTURO", True, 500),
        ({"ars": "FUTURO"}, "FUTURO", True, 500),
        ({"ars": "FUTURO"}, "HUMANO", False, 500),
        ({"ars": "FUTURO"}, "HUMANO", True, None),
    ],
)
def test_only_authorized_insurer_correction_discards_previous_room_snapshot(
    old, new, grant, expected
):
    connection = Mock()
    connection.execute.return_value.fetchone.return_value = old
    actor = {
        "role": app.ROLE_AUDIT,
        "permission_overrides": {app.PERMISSION_CHANGE_RECEIPT_ARS: grant},
    }
    assert (
        app._room_price_after_insurer_change(connection, 1, new, actor, 500) == expected
    )


def test_health_change_updates_open_form_and_reuses_module_pages(
    main_window, monkeypatch
):
    window = main_window
    original = window.monthly_lists_page
    cache = Mock()
    monkeypatch.setattr(app, "_OFFLINE_AUTH_CACHE", cache)
    monkeypatch.setattr(app, "_local_device_identity", lambda: ("SYNTHETIC", "QA"))
    window.editing_recibo_id = 1
    permissions = {
        app.PERMISSION_MANAGE_BILLING_LISTS: False,
        app.PERMISSION_EDIT_PENDING: False,
    }
    window._on_session_health_completed(
        {
            "alive": True,
            "permission_context": {
                "role": app.ROLE_ADMIN,
                "is_active": 1,
                "permission_overrides": permissions,
            },
        },
        1,
    )
    assert window.module_tabs.count() == 2
    assert not window.btn_generate.isEnabled()
    assert not window.authorization_edit.isEnabled()
    assert not window.btn_add.isEnabled()
    cache.refresh_permissions.assert_called_once()
    window._on_session_health_completed(
        {"alive": True, "permission_context": {"permission_overrides": {}}}, 1
    )
    assert window.module_tabs.count() == 3
    assert window.monthly_lists_page is original
    assert window.btn_generate.isEnabled()


@pytest.mark.parametrize("opened", [True, False])
def test_patient_edit_receipt_route_preserves_existing_receipt_id(opened):
    form = SimpleNamespace(
        open_receipt_in_billing=Mock(return_value=opened),
        module_tabs=Mock(),
        billing_module_index=1,
    )
    app.MainWindow._send_emergency_history_to_billing(form, {"receipt_id": 42})
    form.open_receipt_in_billing.assert_called_once_with(42)
    assert form.module_tabs.setCurrentIndex.call_count == int(opened)


def test_user_manager_permission_action_saves_explicit_deny(monkeypatch):
    user = {
        "id": 1,
        "username": "SYNTHETIC",
        "full_name": "QA",
        "role": app.ROLE_ADMIN,
        "is_active": 1,
        "created_at": "2026-10-15",
        "last_login": "",
        "permission_overrides": {},
    }
    monkeypatch.setattr(app, "list_users", lambda: [user])
    monkeypatch.setattr(app, "get_user_receipt_counts", dict)
    monkeypatch.setattr(app, "get_active_sessions_map", dict)
    monkeypatch.setattr(app, "get_user", lambda _: dict(user))
    saved = Mock()
    monkeypatch.setattr(app, "save_user_permission_overrides", saved)
    manager = app.UsersAdminDialog(user)
    try:
        manager._selected_username = lambda: None
        manager.edit_permissions()
        manager._selected_username = lambda: "SYNTHETIC"
        monkeypatch.setattr(app, "get_user", lambda _: None)
        manager.edit_permissions()
        monkeypatch.setattr(app, "get_user", lambda _: dict(user))
        errors = []

        def operate():
            modal = QApplication.activeModalWidget()
            try:
                modal.controls[app.PERMISSION_EDIT_PENDING].setCurrentIndex(2)
                modal.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
            except Exception as exc:
                errors.append(str(exc))
                if modal:
                    modal.reject()

        QTimer.singleShot(0, operate)
        manager.edit_permissions()
        assert not errors
        saved.assert_called_once_with(
            "SYNTHETIC", {app.PERMISSION_EDIT_PENDING: False}, user, expected={}
        )
        QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
        manager.edit_permissions()
        assert saved.call_count == 1
    finally:
        manager.close()


@pytest.mark.parametrize("signal_available", [True, False])
def test_runtime_records_linked_receipts_and_notifies_current_forms(
    monkeypatch, signal_available
):
    from admission_v15_adapter import _HybridAdmissionRuntime, _HybridDatabaseProxy

    runtime = _HybridAdmissionRuntime.__new__(_HybridAdmissionRuntime)
    updated = {
        "linked_receipts": [
            {
                "id": 42,
                "numero": 990042,
                "ars": "FUTURO",
                "estado_facturacion": "PENDIENTE",
            }
        ]
    }
    runtime.patient_directory = Mock()
    runtime.patient_directory.update_patient.return_value = updated
    runtime.host = SimpleNamespace(
        user={"username": "SYNTHETIC", "role": app.ROLE_AUDIT},
        session_id="session",
        event_bus=SimpleNamespace(
            attention_updated=Mock() if signal_available else None,
            billing_requested=Mock(),
        ),
    )
    assert runtime.update_patient_directory("patient", {"nss": "00001"}) is updated
    if signal_available:
        runtime.host.event_bus.attention_updated.emit.assert_called_once_with(
            {"global_patient_id": "patient"}
        )
    proxy = _HybridDatabaseProxy.__new__(_HybridDatabaseProxy)
    object.__setattr__(proxy, "_runtime", runtime)
    assert proxy.recibos_ultima_edicion_paciente() == updated["linked_receipts"]
    monkeypatch.setattr(
        "admission_receipt_dialog.QInputDialog.getItem",
        lambda *args: (args[3][0], True),
    )
    assert proxy.abrir_recibo_ultima_edicion_paciente()
    runtime.host.event_bus.billing_requested.emit.assert_called_once_with(
        {"receipt_id": 42}
    )


@pytest.mark.parametrize("read_only", [False, True])
def test_permission_restore_preserves_preexisting_busy_and_readonly_controls(read_only):
    from PySide6.QtWidgets import QPushButton, QLineEdit

    generate = QPushButton()
    generate.setEnabled(False)
    authorization = QLineEdit()
    form = SimpleNamespace(
        btn_generate=generate,
        authorization_edit=authorization,
        receipt_read_only=read_only,
    )
    app.MainWindow._update_receipt_edit_controls(form, False)
    app.MainWindow._update_receipt_edit_controls(form, False)
    app.MainWindow._update_receipt_edit_controls(form, True)
    assert not generate.isEnabled()
    assert authorization.isEnabled() is (not read_only)
    assert authorization.property("permissionPriorEnabled") is None


@pytest.mark.parametrize("linked", [False, True])
def test_successful_patient_edit_exposes_existing_receipt_without_creating_one(
    tmp_path, linked
):
    from tests.test_patient_edit_dialog_identity import editor_callback
    from tests.test_patient_edit_all_roles_v110 import (
        _create_local_patient_database,
        _database_manager_for,
    )

    path = tmp_path / "patients.db"
    _create_local_patient_database(path)
    database, _audit = _database_manager_for(path)
    fields = {
        key: Mock(get=Mock(return_value=value))
        for key, value in {
            "Nombre": "CORREGIDO",
            "Cédula": "00100000001",
            "Teléfono": "8095550001",
            "NSS": "123456",
            "Dirección": "DIRECCION A",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "HUMANO",
        }.items()
    }
    button = Mock()
    messages = Mock()
    editor_callback(
        "guardar_edicion",
        original_identidad={"valor": "P:1"},
        campos=fields,
        self=Mock(db=database),
        estado_var=Mock(),
        messagebox=messages,
        btn_editar_recibo=button,
        receipt_reader=lambda: [{"id": 42}] if linked else [],
    )()
    messages.showerror.assert_not_called()
    assert messages.showinfo.call_args.args[0] == "Guardado"
    assert ("ya tiene recibo" in messages.showinfo.call_args.args[1]) is linked
    button.configure.assert_called_once_with(state="normal" if linked else "disabled")


@pytest.mark.parametrize("offline", [False, True])
def test_newly_granted_list_access_creates_page_only_with_connection(
    monkeypatch, offline
):
    from PySide6.QtWidgets import QTabBar

    tabs = QTabBar()
    tabs.addTab("Facturación")
    factory = Mock()
    monkeypatch.setattr(app, "MonthlyBillingListsPage", factory)
    form = SimpleNamespace(
        current_user={
            "role": app.ROLE_AUX,
            "permission_overrides": {
                app.PERMISSION_VIEW_ADMISSION_QUEUE: False,
                app.PERMISSION_MANAGE_BILLING_LISTS: True,
            },
        },
        module_tabs=tabs,
        monthly_lists_page=None,
        offline_login=offline,
        main_module_stack=Mock(),
        _switch_main_module=Mock(),
        _ensure_emergency_workspace=Mock(),
    )
    app.MainWindow._refresh_permission_modules(form)
    if offline:
        factory.assert_not_called()
        assert form.monthly_lists_page is None
    else:
        factory.assert_called_once_with(form.current_user, form)
        form.main_module_stack.addWidget.assert_called_once_with(factory.return_value)
    tabs.close()


@pytest.mark.parametrize(
    "role,grant,allowed",
    [
        (app.ROLE_AUX, False, False),
        (app.ROLE_AUX, True, True),
        (app.ROLE_ADMIN, False, True),
    ],
)
def test_user_manager_entry_respects_delegated_permission(
    monkeypatch, role, grant, allowed
):
    dialog = Mock()
    notification = Mock()
    monkeypatch.setattr(app, "UsersAdminDialog", dialog)
    monkeypatch.setattr(app, "FloatingToast", notification)
    form = SimpleNamespace(
        current_user={
            "role": role,
            "permission_overrides": {app.PERMISSION_MANAGE_USER_PERMISSIONS: grant},
        },
        mark_activity=Mock(),
    )
    app.MainWindow.open_users_admin(form)
    assert dialog.call_count == int(allowed)
    assert notification.call_count == int(not allowed)
