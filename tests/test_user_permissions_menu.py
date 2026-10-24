"""Permission actions target the clicked user; filtering never changes grants."""

import os
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QMenu,
    QPushButton,
    QTableWidgetItem,
)

import CALCULOS_QT as app
import user_permissions_dialog as permissions_ui


@pytest.fixture(scope="module")
def qt_application():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def user_manager(qt_application, monkeypatch):
    monkeypatch.setattr(app.UsersAdminDialog, "load_users", lambda _self: None)
    dialog = app.UsersAdminDialog(
        {"username": "admin", "role": app.ROLE_ADMIN, "is_active": 1}
    )
    dialog.table.setRowCount(2)
    for row, username in enumerate(("operator_a", "operator_b")):
        dialog.table.setItem(row, 0, QTableWidgetItem(username))
    dialog.table.setCurrentCell(0, 0)
    yield dialog
    dialog.close()


def capture_context_menu(monkeypatch, invoke=None):
    captured = []

    class CapturedMenu(QMenu):
        def exec(self, _position):
            captured.extend(
                action for action in self.actions() if not action.isSeparator()
            )
            if invoke:
                next(
                    action for action in self.actions() if action.text() == invoke
                ).trigger()

    monkeypatch.setattr(app, "QMenu", CapturedMenu)
    return captured


def row_point(dialog, row):
    return dialog.table.visualItemRect(dialog.table.item(row, 0)).center()


def test_permission_action_targets_right_clicked_row(user_manager, monkeypatch):
    selected = []
    monkeypatch.setattr(
        user_manager,
        "edit_permissions",
        lambda: selected.append(user_manager._selected_username()),
    )
    capture_context_menu(monkeypatch, "Permisos individuales")
    user_manager.show_context_menu(row_point(user_manager, 1))
    assert selected == ["operator_b"]
    assert user_manager.table.currentRow() == 1
    assert user_manager.table.editTriggers() == QAbstractItemView.NoEditTriggers
    assert not any(
        button.text() == "Permisos individuales"
        for button in user_manager.findChildren(QPushButton)
    )


@pytest.mark.parametrize(
    "role,override,allowed",
    [
        (app.ROLE_ADMIN, None, True),
        (app.ROLE_ADMIN, False, False),
        (app.ROLE_AUX, True, True),
        (app.ROLE_AUX, None, False),
    ],
)
def test_context_action_uses_effective_permission(
    user_manager, monkeypatch, role, override, allowed
):
    user_manager.current_user["role"] = role
    user_manager.current_user["permission_overrides"] = (
        {} if override is None else {app.PERMISSION_MANAGE_USER_PERMISSIONS: override}
    )
    captured = capture_context_menu(monkeypatch)
    user_manager.show_context_menu(row_point(user_manager, 0))
    assert (
        any(action.text() == "Permisos individuales" for action in captured) is allowed
    )


def test_empty_space_has_no_context_action(user_manager, monkeypatch):
    captured = capture_context_menu(monkeypatch)
    user_manager.show_context_menu(
        QPoint(20, user_manager.table.viewport().height() + 20)
    )
    assert captured == []
    assert user_manager.table.currentRow() == 0


def test_direct_permission_action_rechecks_permission(user_manager, monkeypatch):
    user_manager.current_user["permission_overrides"] = {
        app.PERMISSION_MANAGE_USER_PERMISSIONS: False
    }
    get_user = Mock(side_effect=AssertionError("must deny before loading target"))
    toast = Mock()
    monkeypatch.setattr(app, "get_user", get_user)
    monkeypatch.setattr(app, "FloatingToast", toast)
    user_manager.edit_permissions()
    get_user.assert_not_called()
    toast.return_value.show.assert_called_once()


@pytest.mark.parametrize("missing", ["selection", "user"])
def test_permission_action_missing_target_is_safe(user_manager, monkeypatch, missing):
    monkeypatch.setattr(app, "FloatingToast", Mock())
    if missing == "selection":
        user_manager.table.setCurrentCell(-1, -1)
    monkeypatch.setattr(app, "get_user", lambda _username: None)
    dialog = Mock(side_effect=AssertionError("must not open without a target"))
    monkeypatch.setattr(permissions_ui, "UserPermissionsDialog", dialog)
    user_manager.edit_permissions()
    dialog.assert_not_called()


def test_permission_dialog_callback_uses_clicked_user(user_manager, monkeypatch):
    target = {"username": "operator_b", "role": app.ROLE_AUX}
    refreshed = {"username": "admin", "role": app.ROLE_ADMIN, "full_name": "Updated"}
    get_user = Mock(side_effect=[target, refreshed])
    saved = Mock()
    monkeypatch.setattr(app, "get_user", get_user)
    monkeypatch.setattr(app, "save_user_permission_overrides", saved)

    def open_dialog(user, permissions, defaults, save, parent):
        assert user == target
        assert permissions == app.PERMISSION_LABELS
        assert defaults == app.ROLE_PERMISSIONS[app.ROLE_AUX]
        assert parent is user_manager
        save({app.PERMISSION_EDIT_PENDING: False}, {})
        return Mock(exec=lambda: QDialog.Accepted)

    monkeypatch.setattr(permissions_ui, "UserPermissionsDialog", open_dialog)
    user_manager.table.setCurrentCell(1, 0)
    user_manager.edit_permissions()
    saved.assert_called_once_with(
        "operator_b",
        {app.PERMISSION_EDIT_PENDING: False},
        user_manager.current_user,
        expected={},
    )
    assert user_manager.current_user["full_name"] == "Updated"


@pytest.fixture
def permission_dialog(qt_application):
    save = Mock()
    dialog = permissions_ui.UserPermissionsDialog(
        {"username": "operator", "permission_overrides": {"billing": False}},
        {"billing": "Facturar más de 48 horas", "admission": "Acceder a Admisión"},
        {"admission"},
        save,
    )
    yield dialog, save
    dialog.close()


def test_permission_search_filters_without_changing_or_saving(permission_dialog):
    dialog, save = permission_dialog
    dialog.permission_search.setText("  FACTURAR  ")
    assert not dialog.table.isRowHidden(0)
    assert dialog.table.isRowHidden(1)
    assert dialog.controls["billing"].currentData() is False
    dialog.permission_search.clear()
    assert not any(dialog.table.isRowHidden(row) for row in range(2))
    save.assert_not_called()


def test_search_handles_empty_permission_caption(permission_dialog):
    dialog, save = permission_dialog
    dialog.table.takeItem(0, 0)
    dialog.permission_search.setText("Facturar")
    assert dialog.table.isRowHidden(0)
    save.assert_not_called()


def test_save_includes_hidden_permissions_and_restore_default(permission_dialog):
    dialog, save = permission_dialog
    dialog.controls["billing"].setCurrentIndex(0)
    dialog.controls["admission"].setCurrentIndex(1)
    dialog.permission_search.setText("Facturar")
    dialog.save()
    save.assert_called_once_with({"admission": True}, {"billing": False})
    assert dialog.result() == QDialog.Accepted


@pytest.mark.parametrize(
    "error", [ValueError("Concurrent change"), PermissionError("Denied")]
)
def test_save_failure_keeps_dialog_open(permission_dialog, error):
    dialog, save = permission_dialog
    save.side_effect = error
    dialog.save()
    assert dialog.result() != QDialog.Accepted
    assert dialog.message.text() == str(error)


def test_cancel_does_not_save(permission_dialog):
    dialog, save = permission_dialog
    dialog.controls["admission"].setCurrentIndex(2)
    buttons = dialog.findChild(QDialogButtonBox)
    buttons.button(QDialogButtonBox.Cancel).click()
    save.assert_not_called()
    assert dialog.result() == QDialog.Rejected
