import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest.mock import patch

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication, QPushButton, QWidget, QVBoxLayout

import CALCULOS_QT as app
from app_icons import APP_ICONS, AppIcons


EXPECTED = {
    "Usar en Facturación": "verify",
    "Buscar": "search",
    "Actualizar": "refresh",
    "Historial de Admisión": "history",
    "Reporte estadístico": "report",
    "Abrir Listado en Excel": "excel",
    "Editar paciente": "edit",
    "Configuración interna": "settings",
    "Cambiar Turno": "shift",
    "Menú": "menu",
    "Limpiar": "clear",
    "Generar PDF": "pdf",
    "VERIFICAR PACIENTE PARA FACTURAR": "verify",
    "Continuar sin verificar": "next",
    "Descartar de lista": "delete",
    "Confirmar paciente": "confirm",
    "Cancelar": "cancel",
    "Anterior": "previous",
    "Siguiente": "next",
    "Imprimir": "print",
    "Vista previa": "preview",
    "Abrir hoja": "open",
    "Anular seleccionado": "delete",
    "Cerrar sesión": "logout",
}


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_all_scoped_semantic_buttons_resolve_to_non_null_icons():
    _qt_app()
    root = QWidget()
    layout = QVBoxLayout(root)
    buttons = []
    for text, expected in EXPECTED.items():
        button = QPushButton(text, root)
        layout.addWidget(button)
        buttons.append((button, expected))
    result = APP_ICONS.decorate_tree(root)
    assert result["decorated"] == len(EXPECTED)
    for button, expected in buttons:
        assert AppIcons.semantic_key(button) == expected
        assert not button.icon().isNull()
        assert button.iconSize().width() > 0
        assert button.iconSize().height() > 0
    root.deleteLater()


def test_real_billing_admission_dialog_buttons_have_icons():
    _qt_app()
    with patch.object(app.QTimer, "singleShot", return_value=None):
        selector = app.AdmissionValidationDialog(
            current_user={"username": "audit", "role": app.ROLE_AUDIT},
            session_id="test",
        )
        history = app.AdmissionHistoryDialog(
            current_user={"username": "audit", "role": app.ROLE_AUDIT},
        )
    try:
        selector_buttons = (
            selector.search_button,
            selector.refresh_button,
            selector.previous_page_button,
            selector.next_page_button,
            selector.history_button,
            selector.bypass_button,
            selector.dismiss_button,
            selector.confirm_button,
        )
        history_buttons = (
            history.search_button,
            history.clear_button,
            history.use_button,
            history.open_sheet_button,
            history.open_receipt_button,
            history.refresh_button,
            history.close_button,
        )
        for button in selector_buttons + history_buttons:
            assert not button.icon().isNull(), button.text()
            assert button.iconSize() == QSize(18, 18)
        assert history.coverage_combo.currentData() == "TODOS"
        assert history._page_size == 50
    finally:
        selector.close()
        history.close()


def test_icons_have_no_runtime_file_dependency(monkeypatch, tmp_path):
    _qt_app()
    monkeypatch.chdir(tmp_path)
    button = QPushButton("Buscar")
    key = APP_ICONS.decorate_button(button)
    assert key == "search"
    assert not button.icon().isNull()
    assert AppIcons.icon("history", button).isNull() is False
