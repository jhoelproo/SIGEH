import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import CALCULOS_QT as app
from PySide6.QtWidgets import QApplication, QWidget


ADMIN = {"username": "admin.test", "role": app.ROLE_ADMIN}


class _Cursor:
    def __init__(self, *, row=None, rows=None, rowcount=1):
        self._row = row
        self._rows = list(rows or [])
        self.rowcount = rowcount

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _FakeConnection:
    def __init__(self, *, receipt=None, dependencies=None, fail_delete=False):
        self.receipt = receipt or {
            "id": 71,
            "numero": 990071,
            "estado_facturacion": app.BILLING_PENDING,
            "is_deleted": 1,
            "pdf_filename": "recibo.pdf",
        }
        self.dependencies = dependencies or {
            "was_invoiced": False,
            "has_billing_batch": False,
            "has_document_versions": False,
            "has_document_migration": False,
        }
        self.fail_delete = fail_delete
        self.queries = []
        self.committed = False
        self.rolled_back = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.committed = exc_type is None
        self.rolled_back = exc_type is not None
        return False

    def execute(self, query, params=None):
        compact = " ".join(str(query).split())
        self.queries.append((compact, params))
        if compact.startswith("SELECT id, numero, estado_facturacion"):
            return _Cursor(row=self.receipt)
        if compact.startswith("SELECT EXISTS("):
            return _Cursor(row=self.dependencies)
        if compact.startswith("DELETE FROM recibos"):
            if self.fail_delete:
                raise RuntimeError("fallo controlado")
            return _Cursor(rowcount=1)
        if compact.startswith("INSERT INTO action_history"):
            return _Cursor(rowcount=1)
        raise AssertionError(f"Consulta inesperada: {compact}")


def _qt_app():
    return QApplication.instance() or QApplication([])


def test_document_versions_are_protected_before_delete():
    fake = _FakeConnection(dependencies={
        "was_invoiced": False,
        "has_billing_batch": False,
        "has_document_versions": True,
        "has_document_migration": False,
    })
    with patch.object(app, "db_connect", return_value=fake):
        with pytest.raises(app.ReceiptProtectedError):
            app.purge_receipt_permanently(71, ADMIN)

    assert fake.rolled_back
    assert not any(query.startswith("DELETE FROM recibos") for query, _ in fake.queries)


@pytest.mark.parametrize(
    "dependency",
    ["was_invoiced", "has_billing_batch", "has_document_migration"],
)
def test_each_protected_dependency_blocks_purge(dependency):
    dependencies = {
        "was_invoiced": False,
        "has_billing_batch": False,
        "has_document_versions": False,
        "has_document_migration": False,
    }
    dependencies[dependency] = True
    fake = _FakeConnection(dependencies=dependencies)
    with patch.object(app, "db_connect", return_value=fake):
        with pytest.raises(app.ReceiptProtectedError):
            app.purge_receipt_permanently(71, ADMIN)
    assert fake.rolled_back


def test_eligible_purge_uses_fk_policies_and_commits_audit():
    fake = _FakeConnection()
    with patch.object(app, "db_connect", return_value=fake):
        deleted = app.purge_receipt_permanently(71, ADMIN)

    assert deleted["id"] == 71
    assert fake.committed
    assert any(query.startswith("DELETE FROM recibos") for query, _ in fake.queries)
    assert any(query.startswith("INSERT INTO action_history") for query, _ in fake.queries)
    assert not any("DELETE FROM recibo_items" in query for query, _ in fake.queries)
    assert not any("DELETE FROM recibo_document" in query for query, _ in fake.queries)


def test_failure_rolls_back_entire_single_receipt_purge():
    fake = _FakeConnection(fail_delete=True)
    with patch.object(app, "db_connect", return_value=fake):
        with pytest.raises(RuntimeError, match="fallo controlado"):
            app.purge_receipt_permanently(71, ADMIN)
    assert fake.rolled_back
    assert not fake.committed


def test_mixed_cleanup_continues_after_protected_and_error():
    preview = {
        "total": 3,
        "eligible": 2,
        "protected": 1,
        "errors": 0,
        "target_ids": (1, 2, 3),
    }
    side_effect = [
        {"id": 1},
        app.ReceiptProtectedError("protegido"),
        RuntimeError("fallo controlado"),
    ]
    with (
        patch.object(app, "preview_receipt_trash_cleanup", return_value=preview),
        patch.object(app, "purge_receipt_permanently", side_effect=side_effect),
        patch.object(app, "write_runtime_log") as log,
    ):
        summary = app.purge_receipts_safely(receipt_ids=(1, 2, 3), actor=ADMIN)

    assert summary["deleted"] == 1
    assert summary["protected"] == 1
    assert summary["errors"] == 1
    log.assert_called_once()


def test_cleanup_confirmation_defaults_to_cancel():
    qt_app = _qt_app()

    class _Main(QWidget):
        current_user = ADMIN
        is_dark_mode = False

    class _FakeMessageBox:
        Warning = object()
        DestructiveRole = object()
        RejectRole = object()
        last = None

        def __init__(self, parent=None):
            self.default_button = None
            self.escape_button = None
            self.clicked = None
            _FakeMessageBox.last = self

        def setIcon(self, value):
            pass

        def setWindowTitle(self, value):
            pass

        def setText(self, value):
            pass

        def addButton(self, text, role):
            button = SimpleNamespace(text=text, role=role)
            if text == "Cancelar":
                self.clicked = button
            return button

        def setDefaultButton(self, button):
            self.default_button = button

        def setEscapeButton(self, button):
            self.escape_button = button

        def exec(self):
            return 0

        def clickedButton(self):
            return self.clicked

    with patch.object(app, "list_deleted_receipts", return_value=[]):
        dialog = app.ReceiptTrashDialog(_Main())
    with patch.object(app, "QMessageBox", _FakeMessageBox):
        accepted = dialog._confirm_cleanup(
            {"eligible": 1, "protected": 2}, "Limpiar",
        )
    qt_app.processEvents()
    dialog.close()

    assert not accepted
    assert _FakeMessageBox.last.default_button.text == "Cancelar"
    assert _FakeMessageBox.last.escape_button.text == "Cancelar"

