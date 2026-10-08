"""Reported receipt search and billed-document regressions, using synthetic data."""

import os
from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QToolButton

import CALCULOS_QT as app
from billing_admission_edit import apply_owned_receipt_context
from pdf_engine import ReceiptPDFRenderer

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def history(qt, monkeypatch):
    monkeypatch.setattr(app.ReceiptHistoryDialog, "load_rows", lambda *a, **k: None)
    dialog = app.ReceiptHistoryDialog(
        SimpleNamespace(
            current_user={"role": app.ROLE_ADMIN, "username": "qa"},
            is_dark_mode=True,
        )
    )
    dialog.show()
    qt.processEvents()
    assert QTest.qWaitForWindowActive(dialog)
    yield dialog
    dialog.close()


@pytest.mark.parametrize("control", ["ars_combo", "status_combo", "assignment_combo"])
def test_filter_selection_does_not_override_deliberate_keyboard_focus(
    history, qt, control
):
    combo = getattr(history, control)
    combo.setFocus()
    combo.activated.emit(combo.currentIndex())
    qt.processEvents()
    assert combo.hasFocus()


def test_query_finishing_does_not_steal_search_cursor_or_focus(history, qt):
    history.search_edit.setText("990430")
    history.search_edit.setCursorPosition(2)
    history._set_history_query_busy(True)
    history.status_combo.setFocus()
    history._set_history_query_busy(False)
    qt.processEvents()
    assert history.status_combo.hasFocus()
    history.search_edit.setFocus()
    assert history.search_edit.cursorPosition() == 2
    QTest.keyClicks(history.search_edit, "12")
    assert history.search_edit.text() == "99120430"


def test_search_clear_icon_is_clickable_then_manual_typing_works(history, qt):
    history.search_edit.setText("990430")
    history.status_combo.setFocus()
    history.status_combo.activated.emit(0)
    history._set_history_query_busy(True)
    history._set_history_query_busy(False)
    qt.processEvents()
    clear = history.search_edit.findChild(QToolButton)
    assert clear is not None and clear.isVisible()
    QTest.mouseClick(clear, Qt.LeftButton)
    assert history.search_edit.text() == ""
    QTest.mouseClick(history.search_edit, Qt.LeftButton)
    QTest.keyClicks(history.search_edit, "990513")
    assert history.search_edit.text() == "990513"
    assert history.status_combo.currentIndex() == 0


def test_clear_filters_restores_search_focus_even_during_new_query(history, qt):
    history.status_combo.setFocus()
    history.search_edit.setText("990430")
    history.btn_clear_filters.click()
    qt.processEvents()
    assert history.search_edit.hasFocus()
    QTest.keyClicks(history.search_edit, "990513")
    assert history.search_edit.text() == "990513"


def test_search_shortcut_and_modal_do_not_interrupt_other_windows(history, qt):
    modal = QDialog(history)
    modal.setModal(True)
    modal.show()
    qt.processEvents()
    history.history_workspace.search_focus.restore()
    qt.processEvents()
    assert not history.search_edit.hasFocus()
    modal.close()
    history.activateWindow()
    qt.processEvents()
    history.status_combo.setFocus()
    QTest.keyClick(history, Qt.Key_F, Qt.ControlModifier)
    qt.processEvents()
    assert history.search_edit.hasFocus()


@pytest.mark.parametrize("bypass", [False, True])
def test_billed_receipt_metadata_cannot_be_downgraded_to_preliminary(bypass):
    state, _, _ = app._receipt_metadata_policy(
        {"estado_facturacion": "FACTURADO", "verification_bypassed": bypass}, ""
    )
    assert state == "FINAL"


@pytest.mark.parametrize("local", [False, True])
@pytest.mark.parametrize("payment", ["", "PENDIENTE_PAGO", "PAGADO"])
def test_renderer_billed_receipt_is_complete_unless_still_local(local, payment):
    html = ReceiptPDFRenderer().render_html(
        {
            "numero": 990513,
            "paciente": "PACIENTE SINTETICO",
            "fecha": "2026-10-06",
            "estado_documento": "PRELIMINAR",
            "estado_facturacion": "FACTURADO",
            "local_request_id": "local-test" if local else "",
            "payment_status": payment,
            "total_general": 100,
        }
    )
    assert ("VERSIÓN PRELIMINAR" in html) == local
    assert ("PENDIENTE DE SINCRONIZACIÓN" in html) == local


@pytest.mark.parametrize(
    "receipt_id,status,document,allowed",
    [
        (1, "PENDIENTE", "PRELIMINAR", True),
        (1, "SIN_CLASIFICAR", "LISTO_AUDITORIA", True),
        (2, "PENDIENTE", "PRELIMINAR", False),
        (None, "PENDIENTE", "PRELIMINAR", False),
        (1, "FACTURADO", "FINAL", False),
        (1, "PENDIENTE", "FINAL", False),
        (1, "NO_FACTURADO", "PRELIMINAR", False),
        (1, "", "PRELIMINAR", False),
    ],
)
def test_readiness_correction_exception_requires_existing_editable_receipt(
    receipt_id, status, document, allowed
):
    row = {
        "linked_receipt_id": 1,
        "linked_billing_status": status,
        "linked_document_status": document,
        "readiness": "INCOMPLETA",
    }
    result = apply_owned_receipt_context(row, receipt_id)
    assert result.get("allow_pending_receipt_correction", False) is allowed
    assert row["linked_receipt_id"] == 1
