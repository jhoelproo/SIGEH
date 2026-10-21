from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtTest import QTest
from authorization_validation import (
    numeric_authorization,
    install_authorization_validator,
)
from admission_receipt_dialog import choose_linked_receipt
from user_permissions_dialog import UserPermissionsDialog


@pytest.mark.parametrize("value", ["1", "0", "00001234", "9" * 40, " 00012 "])
def test_authorization_valid(value):
    assert numeric_authorization(value) == value.strip()


@pytest.mark.parametrize(
    "value", ["-1", "1.0", "1 2", "12a", "12/34", "١٢٣", "９９", "1" * 41]
)
def test_authorization_invalid(value):
    with pytest.raises(ValueError):
        numeric_authorization(value)


def test_empty_and_minimum():
    assert numeric_authorization(None) == ""
    with pytest.raises(ValueError):
        numeric_authorization("", allow_empty=False)
    with pytest.raises(ValueError):
        numeric_authorization("123", minimum=4)
    assert numeric_authorization("0000", minimum=4) == "0000"


def test_paste_and_keyboard_cannot_insert_non_digits():
    entry = QLineEdit()
    install_authorization_validator(entry)
    QTest.keyClicks(entry, "000123")
    assert entry.text() == "000123"
    QTest.keyClicks(entry, "ABC-/")
    assert entry.text() == "000123"
    QApplication.clipboard().setText("ABC123")
    entry.paste()
    assert entry.text() == "000123"
    entry.clear()
    QApplication.clipboard().setText("0000007")
    entry.paste()
    assert entry.text() == "0000007"
    entry.close()


def test_permission_dialog_save_deny_then_reset_and_error():
    saved = Mock()
    dialog = UserPermissionsDialog(
        {"username": "operator"}, {"x": "Example"}, {"x"}, saved
    )
    dialog.controls["x"].setCurrentIndex(2)
    dialog.save()
    saved.assert_called_once_with({"x": False}, {})
    dialog = UserPermissionsDialog(
        {"username": "operator", "permission_overrides": {"x": False}},
        {"x": "Example"},
        {"x"},
        Mock(side_effect=PermissionError("denied")),
    )
    dialog.controls["x"].setCurrentIndex(0)
    dialog.save()
    assert dialog.message.text() == "denied"
    dialog.close()


def test_receipt_selection_identifies_multiple_without_creating(monkeypatch):
    rows = [
        {"id": 4, "numero": 990004, "ars": "FUTURO", "estado_facturacion": "PENDIENTE"},
        {"id": 8, "numero": 990008, "ars": "", "estado_facturacion": "FACTURADO"},
    ]
    callback = Mock()
    monkeypatch.setattr(
        "admission_receipt_dialog.QInputDialog.getItem",
        lambda *_args: ("Recibo 990008 · Sin seguro · FACTURADO · ID 8", True),
    )
    assert choose_linked_receipt(rows, callback)
    callback.assert_called_once_with(8)
    assert not choose_linked_receipt([], callback)
    monkeypatch.setattr(
        "admission_receipt_dialog.QInputDialog.getItem", lambda *_args: ("", False)
    )
    assert not choose_linked_receipt(rows, callback)


def test_live_module_grant_and_revoke_reuses_pages():
    from types import SimpleNamespace
    from PySide6.QtWidgets import QTabBar
    import CALCULOS_QT as app

    tabs = QTabBar()
    tabs.addTab("Emergencias")
    tabs.addTab("Facturación")
    form = SimpleNamespace(
        current_user={
            "role": app.ROLE_AUX,
            "permission_overrides": {
                app.PERMISSION_VIEW_ADMISSION_QUEUE: False,
                app.PERMISSION_MANAGE_BILLING_LISTS: True,
            },
        },
        module_tabs=tabs,
        emergency_workspace=Mock(),
        monthly_lists_page=Mock(),
        offline_login=False,
        _ensure_emergency_workspace=Mock(),
        _switch_main_module=Mock(),
    )
    app.MainWindow._refresh_permission_modules(form)
    assert [tabs.tabText(i) for i in range(tabs.count())] == [
        "Facturación",
        "Listados de ARS",
    ]
    assert form.emergency_module_index is None
    assert form.monthly_lists_module_index == 1
    form._switch_main_module.assert_called_once_with(0)
    app.MainWindow._refresh_permission_modules(form)
    assert form._switch_main_module.call_count == 1
    tabs.close()
