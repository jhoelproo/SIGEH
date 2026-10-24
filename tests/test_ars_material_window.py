"""Exercise production billing controls in the real three-module window."""

from pathlib import Path
from unittest.mock import Mock

from PySide6.QtTest import QTest
from PySide6.QtGui import QCloseEvent

import CALCULOS_QT as app
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


def test_explicit_materials_override_legacy_helpers_and_follow_quantity(
    main_window, monkeypatch
):
    window = main_window
    window.universal["Medicamentos"] = {"MED QA": 10}
    window.universal["Materiales"] = {"JERINGA QA": 2}
    window.material_cart.rules = {
        "MED QA": [{"name": "JERINGA QA", "units": 2, "active": True}]
    }
    helpers = [Mock() for _ in range(4)]
    for name, helper in zip(
        (
            "maybe_auto_add_guantes",
            "maybe_auto_add_bajante_cateter",
            "maybe_prompt_honorario",
            "maybe_prompt_mascarilla",
        ),
        helpers,
    ):
        monkeypatch.setattr(window, name, helper)
    window.tabs.setCurrentIndex(app.ALL_CATEGORIES.index("Medicamentos"))
    window.refresh_picker()
    window.source_lists["Medicamentos"].setCurrentRow(0)
    window.qty.setValue(3)
    window.add_selected_item()
    assert window.material_cart._quantity("Medicamentos", "MED QA") == 3
    assert window.material_cart._quantity("Materiales", "JERINGA QA") == 6
    helpers[0].assert_not_called()
    helpers[1].assert_not_called()
    helpers[2].assert_called_once()
    helpers[3].assert_not_called()
    row = window.material_cart.row("Medicamentos", "MED QA")
    window._cart_quantity_editor(row).setValue(4)
    assert window.material_cart._quantity("Materiales", "JERINGA QA") == 8
    for width, height in ((1280, 680), (1920, 1000)):
        window.resize(width, height)
        QTest.qWait(150)
        window.grab().save(str(Path("output") / f"ars-materials-window-{width}.png"))
        assert window.btn_generate.isVisible() and window.btn_generate.isEnabled()
    window._remove_cart_row(row)
    assert window.cart_table.rowCount() == 0
    window.reset_all()
    assert window.material_cart.allocations == {}


def test_honorarium_blocked_in_real_cart_but_self_pay_remains_allowed(
    main_window, monkeypatch
):
    window = main_window
    monkeypatch.setattr(app, "FloatingToast", Mock())
    window.current_admission_attention = {"specialty": "GINECOLOGIA"}
    window._current_honorarium_specialties = {"GINECOLOGIA": False}
    app.HONORARIUM_PROMPT_SETTINGS.update([])
    assert window.insert_or_increment_cart_item("Honorarios", "DIA", 100) == "blocked"
    assert window.cart_table.rowCount() == 0
    window._current_honorarium_specialties = {"GINECOLOGIA": True}
    assert window.insert_or_increment_cart_item("Honorarios", "DIA", 100) == "added"


def test_material_refresh_reuses_worker_updates_cache_and_backs_off(
    main_window, monkeypatch
):
    from receipt_continuity import local_catalog_startup

    window = main_window
    operation = Mock(
        return_value={
            "links": {"MED QA": []},
            "version": 9,
            "universal": window.universal,
        }
    )
    monkeypatch.setattr(app, "refresh_billing_material_catalog", operation)
    window._request_material_rules()
    first = window._material_rules_worker
    window._request_material_rules()
    assert window._material_rules_worker is first
    event = QCloseEvent()
    monkeypatch.setattr(first, "isRunning", lambda: True)
    app.MainWindow.closeEvent(window, event)
    assert not event.isAccepted()
    first.finished.disconnect(window.close)
    for _ in range(100):
        QTest.qWait(10)
        if window._material_rules_worker is None:
            break
    assert operation.call_count == 1
    assert window.material_cart.rules == {"MED QA": []}
    assert window._material_rules_version == 9
    assert local_catalog_startup("qa")["material_rules"]["version"] == 9
    window._material_rules_failed("project is restricted")
    assert not window._material_rules_retry.ready
    window._request_material_rules()
    assert window._material_rules_worker is None
    window._material_rules_retry.succeeded()
    window.offline_login = True
    window._request_material_rules()
    assert window._material_rules_worker is None
    window.offline_login = False
    request = Mock()
    monkeypatch.setattr(window, "_request_material_rules", request)
    window._update_billing_item_rules_cache({"medication_markup_version": 9})
    request.assert_not_called()
    window._update_billing_item_rules_cache({"medication_markup_version": 10})
    request.assert_called_once()
