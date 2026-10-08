"""Responsive summary visibility follows the existing receipt-history permissions."""

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

import CALCULOS_QT as app


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("height", [680, 1000])
@pytest.mark.parametrize(
    "role,allowed",
    [(app.ROLE_ADMIN, True), (app.ROLE_AUDIT, True), (app.ROLE_AUX, False)],
)
def test_responsive_audit_summary_respects_role_in_all_sizes_and_toggle(
    qt, monkeypatch, height, role, allowed
):
    monkeypatch.setattr(app.ReceiptHistoryDialog, "load_rows", lambda *a, **k: None)
    window = app.ReceiptHistoryDialog(
        SimpleNamespace(
            current_user={"role": role, "username": "qa"}, is_dark_mode=True
        )
    )
    try:
        window.resize(1280 if height < 800 else 1920, height)
        window.show()
        qt.processEvents()
        controller = window.history_workspace.small_screen
        controller.fit()
        compact = height < 800
        assert controller.summary_button.isVisible() == (compact and allowed)
        assert window.metrics_widget.isVisible() == (allowed and not compact)
        assert window.audit_queue_summary.isVisible() == (allowed and not compact)
        controller.summary_button.setChecked(True)
        assert window.metrics_widget.isVisible() == allowed
        assert window.audit_queue_summary.isVisible() == allowed
        controller.summary_button.setChecked(False)
        assert window.metrics_widget.isVisible() == (allowed and not compact)
        assert window.audit_queue_summary.isVisible() == (allowed and not compact)
    finally:
        window.close()


def test_reused_history_cannot_keep_privileged_summary_when_session_becomes_auxiliary(
    qt, monkeypatch
):
    monkeypatch.setattr(app.ReceiptHistoryDialog, "load_rows", lambda *a, **k: None)
    owner = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN, "username": "admin"}, is_dark_mode=True
    )
    window = app.ReceiptHistoryDialog(owner)
    try:
        window.resize(1280, 680)
        window.show()
        qt.processEvents()
        controller = window.history_workspace.small_screen
        controller.summary_button.setChecked(True)
        assert window.metrics_widget.isVisible()
        owner.current_user = {"role": app.ROLE_AUX, "username": "aux"}
        window.refresh_session_context(force=True)
        controller.fit()
        assert not window.metrics_widget.isVisible()
        assert not window.audit_queue_summary.isVisible()
        assert not controller.summary_button.isVisible()
        window.resize(1920, 1000)
        qt.processEvents()
        assert not window.metrics_widget.isVisible()
        assert not window.audit_queue_summary.isVisible()
    finally:
        window.close()
