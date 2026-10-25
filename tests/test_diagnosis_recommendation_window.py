"""Real catalog recommendations and administrator controls in native Qt."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton, QTableWidgetItem, QInputDialog

import CALCULOS_QT as app
from catalog_workspace_design import RECOMMENDATION_ROLE
from diagnosis_recommendation_admin import RecommendationAdminDialog
from diagnosis_recommendations import normalize_config
from tests.test_mainwindow_viewport_oct08 import main_window as main_window


def prepared_config():
    return normalize_config(
        {
            "diagnoses": [
                {"name": "DIAGNÓSTICO ALFA", "aliases": ["DA"]},
                {"name": "DIAGNÓSTICO BETA", "aliases": ["DB"]},
            ],
            "rules": [
                {
                    "diagnosis": dx,
                    "specialty": "GENERAL",
                    "category": "Medicamentos",
                    "item": item,
                    "mode": "pin",
                    "priority": 1,
                }
                for dx, item in [
                    ("DA", "MEDICAMENTO DE EJEMPLO A"),
                    ("DB", "MEDICAMENTO DE EJEMPLO B"),
                ]
            ],
        }
    )


def prepare_window(window):
    window.universal["Medicamentos"] = {
        "MEDICAMENTO DE EJEMPLO A": 10,
        "MEDICAMENTO DE EJEMPLO B": 20,
        "OTRO MEDICAMENTO": 15,
    }
    window.recommendation_controller.model = {
        "config": prepared_config(),
        "groups": {},
        "cursor": 1,
    }
    window.dx_edit.setText("DA; DB")
    window.refresh_picker()
    QTest.qWait(300)


@pytest.mark.parametrize("width,height", [(1280, 680), (1920, 1000)])
def test_recommendations_are_first_unique_and_never_added_automatically(
    main_window, width, height
):
    window = main_window
    window.resize(width, height)
    prepare_window(window)
    listing = window.source_lists["Medicamentos"]
    assert listing.count() == 3
    assert listing.item(0).data(Qt.UserRole)[0] == "MEDICAMENTO DE EJEMPLO A"
    assert listing.item(1).data(Qt.UserRole)[0] == "MEDICAMENTO DE EJEMPLO B"
    assert listing.item(0).data(RECOMMENDATION_ROLE)["diagnoses"] == [
        "DIAGNÓSTICO ALFA"
    ]
    row = listing.itemWidget(listing.item(0))
    assert row.property("recommendation") is True and row.isVisible()
    assert window.cart_table.rowCount() == 0
    assert window.grab().save(str(Path("output") / f"recommendations-{width}.png"))


def test_suggested_item_uses_normal_add_quantity_and_price(main_window, monkeypatch):
    window = main_window
    prepare_window(window)
    monkeypatch.setattr(window, "maybe_prompt_honorario", Mock())
    window.qty.setValue(3)
    listing = window.source_lists["Medicamentos"]
    expected = listing.item(0).data(Qt.UserRole)[1]
    listing.itemWidget(listing.item(0)).findChild(QPushButton, "DesignAdd").click()
    assert window.cart_table.rowCount() == 1
    assert window._cart_quantity_value(0) == 3
    assert (
        window.cart_table.item(0, 3).text().replace("$", "").replace(",", "").strip()
        == f"{expected:.2f}"
    )
    assert window.qty.value() == 1


def test_normalization_is_applied_before_generation_not_add(main_window):
    window = main_window
    prepare_window(window)
    assert window.dx_edit.text() == "DA; DB"
    assert window.recommendation_controller.normalize_before_save()
    assert window.dx_edit.text() == "DIAGNÓSTICO ALFA; DIAGNÓSTICO BETA."


def test_doubtful_diagnosis_requires_selection_and_cancel_keeps_text(
    main_window, monkeypatch
):
    window = main_window
    prepare_window(window)
    window.dx_edit.setText("DIAGNOSTICO ALFAA")
    monkeypatch.setattr(QInputDialog, "getItem", lambda *_: ("", False))
    assert not window.recommendation_controller.normalize_before_save()
    assert window.dx_edit.text() == "DIAGNOSTICO ALFAA"


def test_non_administrator_cannot_open_configuration(main_window, monkeypatch):
    main_window.current_user = {"username": "aux", "role": app.ROLE_AUX}
    notice = Mock()
    monkeypatch.setattr(app.QMessageBox, "warning", notice)
    main_window.recommendation_controller.open_admin()
    notice.assert_called_once()


@pytest.fixture
def admin_dialog(monkeypatch):
    monkeypatch.setattr(RecommendationAdminDialog, "reload", lambda self: None)
    backend = SimpleNamespace(
        user_is_admin=lambda user: user.get("role") == app.ROLE_ADMIN
    )
    dialog = RecommendationAdminDialog(
        backend, {"username": "admin", "role": app.ROLE_ADMIN}, {}
    )
    data = {
        "config": {**prepared_config(), "version": 2},
        "items": [
            {"categoria": "Medicamentos", "nombre": "MEDICAMENTO DE EJEMPLO A"},
            {"categoria": "Medicamentos", "nombre": "MEDICAMENTO DE EJEMPLO B"},
        ],
        "groups": {},
    }
    dialog._loaded(data)
    dialog.show()
    QTest.qWait(100)
    yield dialog
    dialog.close()


def test_admin_can_edit_aliases_limits_priorities_and_blocks(admin_dialog):
    dialog = admin_dialog
    dialog.diagnoses.setItem(0, 1, QTableWidgetItem("DA; ALFA"))
    dialog.settings["min_receipts"].setValue(12)
    dialog.rules.cellWidget(0, 4).setCurrentText("Bloquear")
    value = dialog.configuration()
    assert value["diagnoses"][0]["aliases"] == ["ALFA", "DA"]
    assert value["settings"]["min_receipts"] == 12
    assert value["rules"][0]["mode"] == "block"
    assert dialog.grab().save("output/recommendations-admin.png")


def test_admin_reviews_ambiguous_pair_without_receipt_identifiers(admin_dialog):
    dialog = admin_dialog
    dialog.model["groups"] = {
        "example": {
            "diagnosis": "DIAGNÓSTICO ALFA",
            "specialty": "EMERGENCIOLOGÍA",
            "items": [{"category": "Laboratorios", "item": "LAB QA", "candidates": 2}],
        }
    }
    dialog.items.append({"categoria": "Laboratorios", "nombre": "LAB QA"})
    dialog._fill_pending()
    dialog.pending.selectRow(0)
    before = dialog.rules.rowCount()
    dialog.review_pending("pin")
    assert dialog.rules.rowCount() == before + 1
    assert dialog.configuration()["rules"][-1]["item"] == "LAB QA"


def test_admin_window_uses_application_theme_and_has_readable_columns(
    main_window, monkeypatch
):
    monkeypatch.setattr(RecommendationAdminDialog, "reload", lambda self: None)
    dialog = RecommendationAdminDialog(app, main_window.current_user, {}, main_window)
    data = {
        "config": {**prepared_config(), "version": 1},
        "groups": {},
        "items": [
            {"categoria": "Medicamentos", "nombre": "MEDICAMENTO DE EJEMPLO A"},
            {"categoria": "Medicamentos", "nombre": "MEDICAMENTO DE EJEMPLO B"},
        ],
    }
    dialog._loaded(data)
    dialog.show()
    QTest.qWait(150)
    assert dialog.diagnoses.columnWidth(0) >= 240
    assert dialog.grab().save("output/recommendations-admin-diagnoses.png")
    dialog.tabs.setCurrentIndex(1)
    QTest.qWait(100)
    assert dialog.rules.columnWidth(3) >= 240
    for column in range(dialog.rules.columnCount()):
        field = dialog.rules.cellWidget(0, column)
        assert dialog.rules.rowHeight(0) >= field.sizeHint().height()
    assert dialog.save_button.property("visualRole") == "primary"
    assert dialog.grab().save("output/recommendations-admin-rules.png")
    dialog.close()


def test_configuration_constructor_rejects_non_administrator():
    backend = SimpleNamespace(user_is_admin=lambda _user: False)
    with pytest.raises(PermissionError, match="administrador"):
        RecommendationAdminDialog(
            backend, {"username": "aux", "role": app.ROLE_AUX}, {}
        )


def test_busy_editor_cannot_submit_twice_or_close_mid_operation(
    admin_dialog, monkeypatch
):
    from PySide6.QtGui import QCloseEvent

    dialog = admin_dialog
    worker = Mock()
    worker.wait.return_value = False
    dialog.worker = worker
    submit = Mock()
    monkeypatch.setattr(dialog, "_task", submit)
    dialog.save()
    dialog.reload()
    dialog.reject()
    event = QCloseEvent()
    dialog.closeEvent(event)
    assert not event.isAccepted()
    submit.assert_not_called()
    dialog.shutdown_for_logout()
    worker.cancel.assert_called_once()
    worker.setParent.assert_called_once_with(None)
    dialog.worker = None
    dialog._finished()
    assert dialog.save_button.isEnabled()


def test_invalid_editor_values_show_error_without_saving(admin_dialog, monkeypatch):
    dialog = admin_dialog
    warning = Mock()
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    dialog.settings["invoiced_weight"].setValue(1)
    dialog.settings["pending_weight"].setValue(2)
    dialog.save()
    warning.assert_called_once()
    assert not dialog.saved and dialog.worker is None
    dialog.shutdown_for_logout()


def test_admin_removes_rows_and_resolved_pairs_leave_review_queue(admin_dialog):
    dialog = admin_dialog
    dialog.diagnoses.selectRow(dialog.diagnoses.rowCount() - 1)
    before = dialog.diagnoses.rowCount()
    dialog.remove_rows(dialog.diagnoses)
    assert dialog.diagnoses.rowCount() == before - 1
    dialog.model["groups"] = {
        "example": {
            "diagnosis": "DIAGNÓSTICO ALFA",
            "specialty": "EMERGENCIOLOGÍA",
            "items": [
                {
                    "category": "Medicamentos",
                    "item": "MEDICAMENTO DE EJEMPLO A",
                    "candidates": 1,
                }
            ],
        }
    }
    dialog._fill_pending()
    assert dialog.pending.rowCount() == 0
    dialog.reject()


def test_deferred_start_and_logout_control_offline_recommendation_lifecycle(
    main_window, monkeypatch
):
    window = main_window
    monkeypatch.setattr(app, "db_pool", None)
    monkeypatch.setattr(window, "_start_pdf_services", Mock())
    window.offline_login = True
    window.start_deferred_services()
    window.start_deferred_services()
    assert window.recommendation_controller.timer.isActive()
    window._start_pdf_services.assert_called_once()
    window._stop_secondary_workers()
    assert window.recommendation_controller.closed
    assert not window.recommendation_controller.timer.isActive()


def test_billing_start_and_catalog_work_without_optional_recommendation_controller(
    main_window, monkeypatch
):
    window = main_window
    window.recommendation_controller.stop()
    del window.recommendation_controller
    monkeypatch.setattr(window, "_start_pdf_services", Mock())
    window.offline_login = True
    window.start_deferred_services()
    window.refresh_picker()
    window._start_pdf_services.assert_called_once()
    assert window.source_lists["Medicamentos"].count() == 20
    assert window.btn_generate.isEnabled()


def test_learned_aliases_do_not_become_independent_diagnoses_on_admin_save(
    admin_dialog,
):
    from diagnosis_recommendations import DiagnosisCatalog

    dialog = admin_dialog
    value = prepared_config()
    for diagnosis in value["diagnoses"]:
        diagnosis["aliases"].append("X")
    groups = {
        name: {"diagnosis": name, "specialty": "EMERGENCIOLOGÍA", "items": []}
        for name in ("DA", "X", "DIAGNÓSTICO GAMMA")
    }
    dialog._loaded(
        {"config": {**value, "version": 3}, "items": dialog.items, "groups": groups}
    )
    saved = dialog.configuration()
    assert {row["name"] for row in saved["diagnoses"]} == {
        "DIAGNÓSTICO ALFA",
        "DIAGNÓSTICO BETA",
        "DIAGNÓSTICO GAMMA",
    }
    catalog = DiagnosisCatalog(saved)
    assert catalog.normalize("DA") == "DIAGNÓSTICO ALFA"
    assert len(catalog.choices("X")) == 2
