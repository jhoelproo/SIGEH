from decimal import Decimal
from unittest.mock import Mock
import uuid

import pytest
from PySide6.QtWidgets import QWidget

from admission_source.emergency_core.backup import BackupManager
from local_receipts import LocalReceiptStore
from local_receipts_dialog import LocalReceiptsDialog
from receipt_continuity import (
    local_catalog_startup,
    local_receipt_pdf_data,
    local_receipt_root,
    local_receipt_store,
    remember_startup_catalog,
    remember_tariff,
    render_local_receipt,
)
from tests.test_local_receipts import receipt_job


@pytest.fixture
def isolated_local_store(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return local_receipt_store()


def test_startup_cache_preserves_catalog_and_separates_user_preferences(
    isolated_local_store,
):
    remember_startup_catalog(
        {
            "universal": {"Materiales": []},
            "preferences": {"theme": "oscuro"},
            "catalog_favorites": [2],
        },
        "demo",
    )
    remember_tariff("ARS DEMO", {"room": Decimal("500.00")})
    remember_tariff("ARS DEMO", {"room": Decimal("600.00")})
    isolated_local_store.cache("medication_markup", {"percent": 10})
    data = local_catalog_startup("demo")
    assert data["local_tariffs"] == {"ARS DEMO": {"room": "600.00"}}
    assert data["preferences"] == {"theme": "oscuro"}
    assert data["catalog_favorites"] == [2]
    assert data["local_medication_markup"] == {"percent": 10}
    assert local_catalog_startup("other")["catalog_favorites"] == []
    assert local_catalog_startup("other")["preferences"] == {"theme": "claro"}


def test_local_root_falls_back_to_home(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    from pathlib import Path

    assert local_receipt_root().is_relative_to(Path.home())


def test_local_pdf_preserves_charges_nss_and_pending_status(isolated_local_store):
    job = receipt_job()
    job["admission_attention"] = {"nss_clean": "001234567890123456789"}
    job["authorization_number"] = "AUT-DEMO"
    identity = isolated_local_store.enqueue(job)
    data = local_receipt_pdf_data(job, identity)
    assert data["nss"] == "001234567890123456789"
    assert data["numero_autorizacion"] == "AUT-DEMO"
    assert data["total_general"] == 510
    assert data["categorias"][0]["items"][0]["total"] == 10
    assert data["estado_documento"] == "PRELIMINAR"
    assert "PENDIENTE" in data["document_title"]
    renderer = Mock()
    renderer.render_pdf.return_value = "local.pdf"
    assert render_local_receipt(job, identity, renderer) == "local.pdf"
    assert renderer.render_pdf.call_args.args[1].is_relative_to(local_receipt_root())
    job["current_user"]["full_name"] = ""
    job["admission_attention"] = {"nss": "123"}
    assert local_receipt_pdf_data(job, identity)["usuario"] == "demo"
    assert local_receipt_pdf_data(job, identity)["nss"] == "123"
    with pytest.raises(ValueError):
        render_local_receipt(job, "../outside", renderer)


def test_receipt_backup_restores_pending_command_and_cache(isolated_local_store):
    identity = isolated_local_store.enqueue(receipt_job())
    isolated_local_store.cache("tariff", {"room": 500})
    manager = BackupManager(isolated_local_store.path, local_receipt_root() / "backups")
    backup = manager.create("qa")
    isolated_local_store.confirm(identity, 25, 992200)
    manager.restore_database(backup)
    reopened = LocalReceiptStore(isolated_local_store.path)
    assert reopened.rows("demo", pending_only=True)[0]["request_id"] == identity
    assert reopened.cached("tariff") == {"room": 500}


def test_local_dialog_selection_reopen_empty_and_close(isolated_local_store):
    parent = QWidget()
    parent.current_user = {"username": "demo"}
    parent.pdf_worker = Mock()
    dialog = LocalReceiptsDialog(parent)
    dialog.show()
    assert dialog.table.rowCount() == 0
    assert not dialog.open_button.isEnabled()
    dialog.open_selected()
    parent.pdf_worker.submit.assert_not_called()
    identity = isolated_local_store.enqueue(receipt_job())
    dialog.refresh()
    dialog.table.selectRow(0)
    assert dialog.open_button.isEnabled()
    dialog.open_button.click()
    assert (
        parent.pdf_worker.submit.call_args.args[0]["reopen_local_request"] == identity
    )
    dialog.accept()
    assert not dialog.isVisible()
    parent.close()


def test_local_dialog_reports_read_error_without_losing_receipt(
    isolated_local_store, monkeypatch
):
    parent = QWidget()
    parent.current_user = {"username": "demo"}
    parent.pdf_worker = Mock()
    isolated_local_store.enqueue(receipt_job())
    dialog = LocalReceiptsDialog(parent)
    dialog.table.selectRow(0)
    warning = Mock()
    monkeypatch.setattr("local_receipts_dialog.QMessageBox.warning", warning)
    monkeypatch.setattr(
        isolated_local_store, "row_for_user", Mock(side_effect=OSError())
    )
    dialog.open_selected()
    warning.assert_called_once()
    assert len(isolated_local_store.rows("demo")) == 1
    dialog.close()
    parent.close()


def test_local_pdf_rejects_invalid_identifier():
    with pytest.raises(ValueError):
        local_receipt_pdf_data(receipt_job(), "wrong")
    assert uuid.UUID(str(uuid.uuid4()))
