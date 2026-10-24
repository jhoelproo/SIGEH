"""Configuration controls and cached policy behavior, isolated from services."""

from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app
from medication_material_dialog import MedicationMaterialDialog
from billing_specialties import SPECIALTIES
from receipt_continuity import (
    local_catalog_startup,
    remember_startup_catalog,
    _store_at,
)


@pytest.fixture(autouse=True)
def readable_qt_font():
    application = QApplication.instance()
    original = application.font()
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeuib.ttf")
    application.setFont(QFont("Segoe UI", 10))
    yield
    application.setFont(original)


def settle(dialog):
    for _ in range(100):
        QTest.qWait(10)
        if dialog.worker is None:
            return
    pytest.fail("Configuration worker did not finish")


@pytest.fixture
def material_dialog():
    data = {
        "medication": {"nombre": "MED QA"},
        "materials": [
            {"id": 1, "nombre": "JERINGA", "precio": 2, "is_active": True, "units": 2},
            {"id": 2, "nombre": "CATÉTER", "precio": 3, "is_active": False, "units": 0},
        ],
    }
    save = Mock(return_value=data)
    dialog = MedicationMaterialDialog(
        10,
        load=lambda _id: data,
        save=save,
        worker_factory=app.HonorariumSettingsWorker,
    )
    dialog.show()
    settle(dialog)
    yield dialog, save
    settle(dialog)
    dialog.reject()


def test_material_filter_keeps_hidden_selection_and_saves_quantities(material_dialog):
    dialog, save = material_dialog
    assert dialog.title.text() == "MED QA"
    dialog.setStyleSheet(app.get_stylesheet(True) + app.modern_module_stylesheet(True))
    QTest.qWait(20)
    assert dialog.table.cellWidget(0, 3).height() >= 36
    assert dialog.table.rowHeight(0) >= 44
    assert dialog.grab().save(str(Path("output") / "ars-materials-configuration.png"))
    dialog.table.item(1, 0).setCheckState(Qt.Checked)
    assert dialog.table.cellWidget(1, 3).isEnabled()
    dialog.table.cellWidget(1, 3).setValue(3)
    dialog.search.setText("cateter")
    assert dialog.table.isRowHidden(0)
    assert not dialog.table.isRowHidden(1)
    assert dialog.quantities() == {1: 2, 2: 3}
    dialog.save_button.click()
    settle(dialog)
    save.assert_called_once_with(10, {1: 2, 2: 3})
    assert dialog.saved
    assert dialog.save_button.isEnabled()


def test_material_save_failure_is_visible_and_retryable(material_dialog):
    dialog, save = material_dialog
    save.side_effect = ValueError("Material inactivo")
    dialog._save()
    dialog.reject()
    assert dialog.isVisible()
    settle(dialog)
    assert "Material inactivo" in dialog.status.text()
    assert not dialog.saved and dialog.save_button.isEnabled()
    dialog.table.item(0, 0).setCheckState(Qt.Unchecked)
    assert not dialog.table.cellWidget(0, 3).isEnabled()


@pytest.mark.parametrize("saved", [False, True])
def test_catalog_button_uses_stable_medication_identity_and_admin_actor(
    monkeypatch, saved
):
    import medication_material_dialog

    monkeypatch.setattr(
        app.UniversalCatalogAdminDialog, "_start_worker", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(app, "FloatingToast", Mock())
    child = SimpleNamespace(exec=Mock(), saved=saved)
    factory = Mock(return_value=child)
    monkeypatch.setattr(medication_material_dialog, "MedicationMaterialDialog", factory)
    save = Mock()
    monkeypatch.setattr(app, "save_medication_material_links", save)
    actor = {"username": "admin-qa", "role": app.ROLE_ADMIN}
    dialog = app.UniversalCatalogAdminDialog(actor)
    QTest.qWait(10)
    dialog._catalog_data = {
        "items": [
            {
                "id": 45,
                "categoria": "Medicamentos",
                "nombre": "MED QA",
                "precio": 10,
                "is_active": 1,
            }
        ],
        "markup": 35,
    }
    dialog._populate_tables()
    dialog.tables["Medicamentos"].setCurrentCell(-1, -1)
    dialog._edit_material_links()
    factory.assert_not_called()
    dialog.tables["Medicamentos"].setCurrentCell(0, 0)
    dialog.material_links_button.click()
    assert factory.call_args.args == (45,)
    factory.call_args.kwargs["save"](45, {8: 2})
    save.assert_called_once_with(45, {8: 2}, actor)
    assert dialog.catalog_changed is saved
    dialog.close()


def test_refresh_cache_failure_preserves_live_rules_and_catalog(monkeypatch):
    import receipt_continuity
    from network_retry import NetworkRetryGate

    store = Mock()
    store.cache.side_effect = OSError("disk unavailable")
    monkeypatch.setattr(receipt_continuity, "local_receipt_store", lambda: store)
    context = SimpleNamespace(
        material_cart=SimpleNamespace(rules={}),
        _material_rules_retry=NetworkRetryGate(),
        refresh_picker=Mock(),
        _material_rules_version=1,
        _request_material_rules=Mock(),
    )
    app.MainWindow._material_rules_loaded(
        context, {"links": {"QA": []}, "version": 2, "universal": {"Materiales": {}}}
    )
    assert context.material_cart.rules == {"QA": []}
    assert context._material_rules_version == 2
    app.MainWindow._update_billing_item_rules_cache(
        context, {"medication_markup_version": 2}
    )
    context._request_material_rules.assert_not_called()


def test_historical_fee_guard_handles_unknown_receipt_and_changed_ars():
    from billing_item_rule_store import _receipt_specialty, _unchanged_historical_fees

    con = Mock()
    con.execute.return_value.fetchone.return_value = None
    assert _receipt_specialty(con, 7, "") == ""
    assert not _unchanged_historical_fees(con, 7, "ARS QA", "GENERAL", [])
    con.execute.return_value.fetchone.return_value = {
        "ars": "OTRA ARS",
        "specialty_snapshot": "GENERAL",
    }
    assert not _unchanged_historical_fees(con, 7, "ARS QA", "GENERAL", [])


@pytest.mark.parametrize(
    "role,editable", [(app.ROLE_ADMIN, True), (app.ROLE_AUX, False)]
)
def test_specialty_checkboxes_and_read_only_permissions(monkeypatch, role, editable):
    rows = [
        {
            "ars_id": 9,
            "ars_name": "ARS QA",
            "suppress_honorarium_prompt": True,
            "honorarium_specialties": {"GENERAL": False},
        }
    ]
    monkeypatch.setattr(app.ARSHonorariumSettingsDialog, "_load", lambda self: None)
    dialog = app.ARSHonorariumSettingsDialog({"username": "qa", "role": role})
    dialog._loaded_rows(rows)
    assert dialog.table.columnCount() == 5
    assert dialog.table.item(0, 2).checkState() == Qt.Unchecked
    assert dialog.table.item(0, 3).checkState() == Qt.Checked
    assert bool(dialog.table.item(0, 2).flags() & Qt.ItemIsUserCheckable) == editable
    if editable:
        dialog.resize(1080, 620)
        dialog.show()
        QTest.qWait(20)
        assert dialog.grab().save(str(Path("output") / "ars-materials-specialties.png"))
        dialog._enable_specialties()
        assert all(
            dialog.table.item(0, column).checkState() == Qt.Checked
            for column in range(2, 5)
        )
        dialog._clear_suppressions()
        assert dialog.table.item(0, 1).checkState() == Qt.Unchecked
        run = Mock()
        monkeypatch.setattr(dialog, "_run", run)
        save = Mock(return_value=rows)
        monkeypatch.setattr(app, "save_ars_honorarium_prompt_settings", save)
        dialog._save()
        run.call_args.args[0]()
        assert save.call_args.kwargs["specialty_changes"][9] == dict.fromkeys(
            SPECIALTIES, True
        )
    dialog.close()


@pytest.mark.parametrize(
    "specialty,allowed",
    [
        ("GENERAL", False),
        ("EMERGENCIOLOGIA", False),
        ("PEDIATRIA", True),
        ("GINECOLOGÍA", False),
        ("UNKNOWN", False),
    ],
)
def test_cached_policy_blocks_manual_and_automatic_fee_without_sql(
    specialty, allowed, monkeypatch
):
    app.HONORARIUM_PROMPT_SETTINGS.update(
        [
            {
                "ars_id": 8,
                "ars_name": "ARS QA",
                "honorarium_specialties": {
                    "GENERAL": False,
                    "PEDIATRIA": True,
                    "GINECOLOGIA": False,
                },
            }
        ]
    )
    context = SimpleNamespace(
        _current_ars_id=8,
        current_ars="ARS QA",
        current_admission_attention={"specialty": specialty},
        ars_cache={"Honorarios": {"DIA": 100}},
        _honorarium_prompted_ars=set(),
        cart_has_category=lambda _cat: False,
    )
    monkeypatch.setattr(
        app, "db_connect", Mock(side_effect=AssertionError("Unexpected SQL"))
    )
    assert app.receipt_honorarium_allowed(context) is allowed
    assert app.should_show_honorarium_prompt(8, context) is allowed
    context.current_admission_attention = None
    context._editing_specialty_snapshot = "PEDIATRIA"
    assert app.receipt_honorarium_allowed(context)
    app.HONORARIUM_PROMPT_SETTINGS.update([])


def test_rules_and_policy_survive_local_store_reopen(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    data = {
        "material_rules": {
            "links": {"MED QA": [{"name": "GUANTES", "units": 2, "active": True}]},
            "version": 3,
        },
        "honorarium_settings": [
            {"ars_id": 8, "honorarium_specialties": {"GENERAL": False}}
        ],
    }
    remember_startup_catalog(data, "qa")
    _store_at.cache_clear()
    restored = local_catalog_startup("qa")
    assert restored["material_rules"] == data["material_rules"]
    assert restored["honorarium_settings"] == data["honorarium_settings"]
