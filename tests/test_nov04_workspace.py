"""Real Qt controls with synthetic insurer and catalog data."""

from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QColor, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLabel

import CALCULOS_QT as app
from ars_manager_workspace import InsurerMergeDialog, InsurerWorkspace
from tests.test_billing_ars_profiles_and_trash_ui import _wait_for_ars_manager_load


def settle(dialog, attribute="worker"):
    for _ in range(150):
        QTest.qWait(10)
        if getattr(dialog, attribute) is None:
            return
    pytest.fail("El trabajo de prueba no terminó.")


@pytest.fixture
def manager(monkeypatch):
    qt = QApplication.instance()
    original_font = qt.font()
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeuib.ttf")
    qt.setFont(QFont("Segoe UI", 10))
    monkeypatch.setattr(app, "ars_list", lambda: ["MAPFRE", "MAPFRE/PALIC", "PRIMERA"])
    monkeypatch.setattr(
        app, "load_ars_runtime_data", lambda _name: {"sala_emergencia": 500}
    )
    monkeypatch.setattr(
        app, "get_ars_billing_profile", lambda name: {"display_name": name}
    )
    dialog = app.ARSManagerDialog({"username": "admin-qa", "role": app.ROLE_ADMIN})
    dialog.show()
    _wait_for_ars_manager_load(QApplication.instance(), dialog)
    yield dialog
    settle(dialog, "_manager_action_worker")
    _wait_for_ars_manager_load(QApplication.instance(), dialog)
    dialog.reject()
    qt.setFont(original_font)


def test_search_navigation_keeps_the_selected_insurer(manager):
    rail = manager.workspace
    rail.search.setText("palic")
    assert rail.list.item(0).isHidden()
    assert not rail.list.item(1).isHidden()
    rail.list.setCurrentRow(1)
    assert manager.ars_combo.currentText() == "MAPFRE/PALIC"
    rail.search.clear()
    assert not rail.list.item(0).isHidden()


@pytest.mark.parametrize("dark", [False, True])
def test_search_and_profile_placeholders_are_readable_in_both_themes(manager, dark):
    manager.apply_ars_theme(dark)
    QTest.qWait(20)
    background = QColor(app.visual_theme_tokens(dark)["input_bg"])
    for widget in (manager.workspace.search, manager.profile_rnc):
        foreground = widget.palette().color(QPalette.PlaceholderText)
        alpha = foreground.alphaF()
        visible = QColor(
            *[
                round(alpha * fg + (1 - alpha) * bg)
                for fg, bg in zip(foreground.getRgb()[:3], background.getRgb()[:3])
            ]
        )
        assert app.theme_contrast_ratio(visible.name(), background.name()) >= 4.5


def test_manager_mutation_runs_without_blocking_events_or_closing(manager):
    release = Event()
    heartbeat = []
    operation = Mock(side_effect=lambda: release.wait(3))
    manager._run_manager_action(operation)
    worker = manager._manager_action_worker
    manager._run_manager_action(operation)
    assert manager._manager_action_worker is worker
    manager.reject()
    assert manager.isVisible()
    QTimer.singleShot(0, lambda: (heartbeat.append(True), release.set()))
    settle(manager, "_manager_action_worker")
    assert heartbeat == [True]
    operation.assert_called_once()
    assert manager.workspace.isEnabled()


def test_manager_deletion_confirmation_and_pending_profile_are_functional(
    manager, monkeypatch
):
    deleted = Mock()
    monkeypatch.setattr(app, "delete_ars", deleted)
    monkeypatch.setattr(app.QMessageBox, "question", lambda *_args: app.QMessageBox.No)
    manager.delete_ars_clicked()
    deleted.assert_not_called()
    monkeypatch.setattr(app.QMessageBox, "question", lambda *_args: app.QMessageBox.Yes)
    manager.delete_ars_clicked()
    settle(manager, "_manager_action_worker")
    deleted.assert_called_once_with("MAPFRE", manager.current_user)
    _wait_for_ars_manager_load(QApplication.instance(), manager)
    manager._manager_load_worker = object()
    manager.on_ars_change("PRIMERA")
    changed = Mock()
    monkeypatch.setattr(manager, "on_ars_change", changed)
    manager._manager_data_finished()
    QTest.qWait(10)
    changed.assert_called_once_with("PRIMERA")
    manager.current_user = {"role": app.ROLE_AUX}
    manager.delete_ars_clicked()
    manager.open_merge_dialog()
    assert deleted.call_count == 1


@pytest.mark.parametrize("accepted", [True, False])
def test_manager_opens_the_controlled_merge_and_refreshes_after_acceptance(
    manager, monkeypatch, accepted
):
    import ars_manager_workspace

    dialog = Mock()
    dialog.exec.return_value = (
        app.QDialog.Accepted if accepted else app.QDialog.Rejected
    )
    factory = Mock(return_value=dialog)
    monkeypatch.setattr(ars_manager_workspace, "InsurerMergeDialog", factory)
    operation = Mock()
    monkeypatch.setattr(manager, "_run_manager_action", operation)
    manager.open_merge_dialog()
    assert operation.call_count == int(accepted)
    if accepted:
        operation.call_args.args[0]()


@pytest.mark.parametrize("dark", [True, False])
@pytest.mark.parametrize("size", [(1024, 680), (1280, 680), (1920, 1000)])
def test_manager_tabs_fit_the_desktop_and_keep_actions_reachable(manager, size, dark):
    qt = QApplication.instance()
    original = qt.font()
    qt.setFont(QFont("Segoe UI", 10))
    try:
        manager.apply_ars_theme(dark)
        manager.resize(*size)
        for index in range(manager.workspace.tabs.count()):
            manager.workspace.tabs.setCurrentIndex(index)
            QTest.qWait(20)
            filename = (
                f"output/nov04-ars-{size[0]}-{'dark' if dark else 'light'}-{index}.png"
            )
            assert manager.grab().save(str(Path(filename)))
            assert manager.ars_scroll.horizontalScrollBar().maximum() == 0, [
                (
                    manager.workspace.tabs.tabText(i),
                    manager.workspace.tabs.widget(i).minimumSizeHint().width(),
                )
                for i in range(manager.workspace.tabs.count())
            ]
            assert manager.workspace.tabs.tabBar().isVisible()
        assert manager.rect().contains(
            manager.mapFromGlobal(manager.btn_fuse.mapToGlobal(QPoint(0, 0)))
        )
    finally:
        qt.setFont(original)


def test_workspace_can_refresh_empty_names():
    combo = QComboBox()
    combo.addItems(["MAPFRE"])
    owner = SimpleNamespace(ars_combo=combo, _ars_names=["MAPFRE"])
    workspace = InsurerWorkspace(owner, [("Tarifas", [QLabel("Sala")])])
    workspace.refresh_names([])
    assert workspace.list.count() == 0
    workspace.close()


def merge_dialog(preview=None, merge=None):
    return InsurerMergeDialog(
        ["MAPFRE", "HUMANO/PRIMERA"],
        preview or Mock(),
        merge or Mock(),
        app.HonorariumSettingsWorker,
    )


def test_merge_preview_invalidation_and_canonical_selection():
    plan = {
        "source": "MAPFRE",
        "destination": "MAPFRE/PALIC",
        "token": "qa",
        "insurers": {"MAPFRE/PALIC": {"sala_emergencia": 700}},
    }
    merge = Mock()
    dialog = merge_dialog(preview=Mock(return_value=plan), merge=merge)
    dialog.show()
    assert dialog.destination.currentText() == "MAPFRE/PALIC"
    dialog._merge()
    merge.assert_not_called()
    dialog.check.click()
    settle(dialog)
    assert dialog.save_button.isEnabled()
    assert "700.00" in dialog.status.text()
    dialog.source.setCurrentText("HUMANO/PRIMERA")
    assert dialog.destination.currentText() == "PRIMERA"
    assert not dialog.save_button.isEnabled()
    dialog.source.setCurrentText("MAPFRE")
    dialog.check.click()
    settle(dialog)
    dialog.save_button.click()
    settle(dialog)
    merge.assert_called_once_with("MAPFRE", "MAPFRE/PALIC", "qa")
    assert dialog.result() == app.QDialog.Accepted


def test_merge_failure_is_retryable_and_does_not_block_close():
    dialog = merge_dialog(preview=Mock(side_effect=ValueError("No equivalentes")))
    dialog.show()
    dialog.check.click()
    worker = dialog.worker
    dialog._run(Mock(), Mock())
    assert dialog.worker is worker
    dialog.reject()
    assert dialog.isVisible()
    settle(dialog)
    assert "No equivalentes" in dialog.status.text()
    assert not dialog.save_button.isEnabled()
    dialog.reject()
    assert not dialog.isVisible()
    dialog.source.addItem("ARS DESCONOCIDA")
    dialog.source.setCurrentText("ARS DESCONOCIDA")


def test_catalog_context_actions_target_the_clicked_row(monkeypatch):
    monkeypatch.setattr(
        app.UniversalCatalogAdminDialog, "reload_async", lambda _self: None
    )
    dialog = app.UniversalCatalogAdminDialog({"role": app.ROLE_ADMIN})
    dialog._catalog_data = {
        "items": [
            {
                "id": 3,
                "categoria": "Materiales",
                "nombre": "GUANTES",
                "precio": 2,
                "is_active": 0,
            }
        ]
    }
    dialog._populate_tables()
    dialog.tabs.setCurrentIndex(1)
    dialog.show()
    QTest.qWait(20)
    table = dialog.tables["Materiales"]
    position = table.visualItemRect(table.item(0, 0)).center()
    delete = Mock()
    monkeypatch.setattr(dialog, "_delete_item", delete)
    dialog._show_catalog_menu("Materiales", position)
    action = next(a for a in dialog._catalog_menu.actions() if a.text() == "Eliminar")
    assert action.isEnabled()
    action.trigger()
    delete.assert_called_once_with("Materiales")
    assert dialog._selected("Materiales")["id"] == 3
    dialog._catalog_menu.close()
    dialog._show_catalog_menu("Materiales", QPoint(-1, -1))
    assert [a.isEnabled() for a in dialog._catalog_menu.actions()] == [
        True,
        False,
        False,
        False,
    ]
    dialog._catalog_menu.close()
    dialog._worker = object()
    dialog._show_catalog_menu("Medicamentos", QPoint())
    dialog._worker = None
    dialog._show_catalog_menu("Medicamentos", QPoint(-1, -1))
    assert dialog._catalog_menu.actions()[-1].text() == "Materiales vinculados"
    dialog._catalog_menu.close()
    dialog.close()
