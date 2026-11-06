"""Navigate each administrative workspace using synthetic records."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QPushButton

import CALCULOS_QT as app
from diagnosis_recommendation_admin import RecommendationAdminDialog
from diagnosis_recommendations import DEFAULT_SETTINGS


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture(params=[(1024, 690, True), (1360, 820, False)])
def host(qt, request):
    width, height, dark = request.param
    window = QMainWindow()
    window.is_dark_mode = dark
    window.current_user = {"username": "admin", "role": app.ROLE_ADMIN}
    yield window, width, height
    window.close()


def capture(dialog, host, name):
    parent, width, height = host
    dialog.resize(width, height)
    dialog.show()
    QApplication.processEvents()
    assert dialog.width() == width
    for button in dialog.findChildren(QPushButton):
        if button.isVisible() and button.text() in {
            "Cerrar",
            "Guardar configuración",
            "Acciones del usuario",
            "Generar y abrir reporte PDF",
            "Aplicar filtros",
        }:
            assert dialog.rect().contains(
                button.mapTo(dialog, button.rect().bottomRight())
            )
    folder = Path("output/nov06-admin-previews")
    folder.mkdir(parents=True, exist_ok=True)
    theme = "oscuro" if parent.is_dark_mode else "claro"
    assert dialog.grab().save(str(folder / f"{name}-{width}-{theme}.png"))


def test_users_filter_and_selection_stay_consistent(host, monkeypatch):
    parent, *_ = host
    users = [
        {
            "username": "qa_ana",
            "full_name": "ANA DE PRUEBA",
            "role": app.ROLE_AUX,
            "is_active": 1,
            "created_at": "2026-11-05",
        },
        {
            "username": "qa_luis",
            "full_name": "LUIS DE PRUEBA",
            "role": app.ROLE_ADMIN,
            "is_active": 0,
            "created_at": "2026-11-06",
        },
    ]
    monkeypatch.setattr(app, "list_users", lambda: users)
    monkeypatch.setattr(app, "get_user_receipt_counts", lambda: {"qa_ana": 24})
    monkeypatch.setattr(app, "get_active_sessions_map", lambda: {"qa_ana": True})
    dialog = app.UsersAdminDialog(parent.current_user, parent)
    try:
        assert not dialog.btn_manage.isEnabled()
        dialog.table.selectRow(0)
        assert dialog.btn_manage.isEnabled()
        assert "24" in dialog.user_inspector.record.toPlainText()
        capture(dialog, host, "usuarios")
        dialog.btn_refresh.click()
        assert dialog._selected_username() == "qa_ana"
        opened = Mock()

        class CapturedMenu(QMenu):
            def exec(self, _position):
                opened([action.text() for action in self.actions()])

        monkeypatch.setattr(app, "QMenu", CapturedMenu)
        dialog.btn_manage.click()
        assert "Permisos individuales" in opened.call_args.args[0]
        dialog.search_edit.setText("luis")
        assert not dialog.btn_manage.isEnabled()
        assert dialog.table.isRowHidden(0)
        dialog.btn_refresh.click()
        assert dialog.table.isRowHidden(0)
        assert dialog.user_count.text() == "1 de 2 usuarios"
        dialog.search_edit.setText("sin resultados")
        assert "No hay usuarios" in dialog.user_count.text()
        monkeypatch.setattr(app, "list_users", Mock(side_effect=ValueError))
        dialog.load_users()
        assert "No se pudieron cargar" in dialog.user_count.text()
    finally:
        QTest.keyClick(dialog, Qt.Key_Escape)
        assert not dialog.isVisible()


def test_action_history_displays_full_selected_record(host, monkeypatch):
    parent, *_ = host
    load = Mock()
    monkeypatch.setattr(
        app.HistoryDialog, "load_rows", lambda self, **kwargs: load(**kwargs)
    )
    dialog = app.HistoryDialog(parent)
    try:
        row = {
            "created_at": "2026-11-06 10:30:00",
            "username": "qa_admin",
            "role": "Administrador",
            "module": "Facturación",
            "action": "Validar recibo",
            "entity_type": "Recibo",
            "entity_id": "QA-700",
            "ars_name": "ARS de prueba",
            "details": "<texto de prueba completo>",
        }
        dialog._history_rows_loaded({"users": ["qa_admin"]}, [row], True, True)
        dialog.table.selectRow(0)
        assert (
            "<texto de prueba completo>" in dialog.action_inspector.record.toPlainText()
        )
        assert dialog.count_label.text() == "1 acción cargada"
        capture(dialog, host, "acciones")
        dialog.user_filter.setCurrentIndex(1)
        dialog.btn_apply.click()
        load.assert_called_with(reset=True)
        dialog.btn_reset_filters.click()
        assert dialog.user_filter.currentIndex() == 0
        dialog._history_rows_loaded({}, [], False, True)
        assert "No hay acciones" in dialog.count_label.text()
        dialog._history_load_failed("TestError")
        assert "No fue posible" in dialog.count_label.text()
    finally:
        dialog.close()


def sample_dashboard():
    summary = {"receipts": 12, "total": 54000, "average": 4500, "room": 2500}
    category = {"label": "Laboratorios", "total": 54000, "percentage": 1}
    return {
        "summary": summary,
        "start_date": "2026-11-01",
        "end_date": "2026-11-06",
        "previous_start": "2026-10-01",
        "previous_end": "2026-10-06",
        "previous": {"receipts": 10, "total": 42000, "average": 4200, "room": 2000},
        "filters": {"compare_previous": True, "trend_granularity": "day"},
        "show_ars_comparison": True,
        "summary_table": [
            {
                "label": "ARS de prueba",
                **summary,
                "money_percentage": 1,
                "receipt_percentage": 1,
            }
        ],
        "trend": [
            {"label": "05/11", "total": 27000},
            {"label": "06/11", "total": 27000},
        ],
        "categories": [category],
        "category_comparison": [
            {"label": "Laboratorios", "current": 54000, "previous": 42000}
        ],
        "coverage": [{"label": "Asegurados", "receipts": 12}],
        "shift_billing": {
            "admitted": 12,
            "invoiced": 12,
            "pending": 0,
            "by_ars": [{"ars": "ARS de prueba", "admitted": 12, "invoiced": 12}],
        },
    }


def test_reports_navigate_dashboard_pdf_and_history(host, monkeypatch):
    parent, *_ = host
    monkeypatch.setattr(app, "ars_list", lambda: ["ARS de prueba"])
    monkeypatch.setattr(app, "list_usernames", lambda: ["qa_admin"])
    monkeypatch.setattr(
        app,
        "list_report_history",
        lambda: [
            {
                "report_type": "Reporte comparativo de prueba",
                "start_date": "2026-11-01",
                "end_date": "2026-11-06",
                "generated_at": "2026-11-06 11:15:00",
                "generated_by": "qa_admin",
                "source_table": "report_history",
                "record_id": 1,
                "totals_json": json.dumps(
                    {
                        "period": {
                            "period_label": "Noviembre de prueba",
                            "comparison_label": "Octubre de prueba",
                        }
                    }
                ),
            }
        ],
    )
    monkeypatch.setattr(app.ReportsDialog, "refresh_dashboard", lambda self: None)
    monkeypatch.setattr(
        app,
        "load_current_shift_billing_summary",
        lambda: sample_dashboard()["shift_billing"],
    )
    generate = Mock()
    monkeypatch.setattr(
        app.ReportsDialog, "generate_selected_report", lambda self: generate()
    )
    dialog = app.ReportsDialog(parent.current_user, parent)
    try:
        dialog._dashboard_loaded(sample_dashboard())
        for page in range(dialog.dashboard_results_tabs.count()):
            dialog.dashboard_results_tabs.setCurrentIndex(page)
            capture(dialog, host, f"reportes-panel-{page}")
        assert not dialog.dashboard_filter_options.isVisible()
        dialog.btn_dashboard_filters.click()
        assert dialog.dashboard_filter_options.isVisible()
        dialog.btn_dashboard_filters.click()
        assert not dialog.dashboard_filter_options.isVisible()
        dialog.tabs.setCurrentIndex(1)
        capture(dialog, host, "reportes-generar")
        dialog.btn_generate.click()
        generate.assert_called_once()
        dialog.tabs.setCurrentIndex(2)
        dialog.table.selectRow(0)
        assert "Octubre de prueba" in dialog.report_inspector.record.toPlainText()
        capture(dialog, host, "reportes-historial")
        dialog.search_edit.setText("sin resultados")
        assert not dialog.btn_open.isEnabled()
        dialog.btn_history_refresh.click()
        assert dialog.table.isRowHidden(0)
        assert "No hay reportes" in dialog.history_count.text()
        monkeypatch.setattr(app, "list_report_history", Mock(side_effect=ValueError))
        dialog.btn_history_refresh.click()
        assert "No se pudo cargar" in dialog.history_count.text()
    finally:
        dialog.close()


def test_recommendation_tabs_keep_scope_and_search(host, monkeypatch):
    parent, *_ = host
    monkeypatch.setattr(RecommendationAdminDialog, "reload", lambda self: None)
    dialog = RecommendationAdminDialog(app, parent.current_user, {}, parent)
    try:
        dialog._loaded(
            {
                "config": {
                    "version": 1,
                    "diagnoses": [{"name": "DIAGNÓSTICO QA", "aliases": ["QA"]}],
                    "rules": [],
                    "settings": DEFAULT_SETTINGS,
                },
                "groups": {},
                "items": [],
                "insurers": [],
            }
        )
        dialog._append_pending_row(
            (
                "DIAGNÓSTICO DE PRUEBA COMPLETO",
                "EMERGENCIOLOGÍA",
                "Laboratorios",
                "ANÁLISIS DE PRUEBA COMPLETO",
            )
        )
        for index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(index)
            capture(dialog, host, f"recomendaciones-{index}")
            assert dialog.scope_bar.isVisible() == (index in (1, 2))
            assert dialog.search.isVisible() == (index != 3)
        dialog.tabs.setCurrentIndex(2)
        recommend = next(
            button
            for button in dialog.findChildren(QPushButton)
            if button.text() == "Recomendar selección"
        )
        assert not recommend.isEnabled()
        dialog.pending.selectRow(0)
        assert recommend.isEnabled()
        assert (
            "ANÁLISIS DE PRUEBA COMPLETO"
            in dialog.pending_inspector.record.toPlainText()
        )
        dialog.search.setText("no coincide")
        assert not recommend.isEnabled()
        assert dialog.pending.isRowHidden(0)
        assert dialog.search_results.text() == "Sin coincidencias"
        dialog.tabs.setCurrentIndex(0)
        assert dialog.diagnoses.isRowHidden(0)
        dialog.search.clear()
        assert not dialog.diagnoses.isRowHidden(0)
        dialog.settings["min_receipts"].setValue(12)
        assert dialog.configuration()["settings"]["min_receipts"] == 12
    finally:
        dialog.close()
