import os
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import CALCULOS_QT as app


def _qt_app():
    return QApplication.instance() or QApplication([])


def _wait_for_ars_manager_load(qt_app, dialog):
    """Allow the dialog's deliberate background load to deliver its signal."""
    for _ in range(100):
        qt_app.processEvents()
        if dialog._manager_load_worker is None:
            return
        QTest.qWait(10)
    raise AssertionError("La carga asíncrona del perfil ARS no terminó.")


class _MainWindowStub(QWidget):
    def __init__(self):
        super().__init__()
        self.current_user = {"username": "admin", "role": app.ROLE_ADMIN}
        self.is_dark_mode = False


def test_senasa_subsidiado_uses_canonical_billing_exclusion():
    assert app.is_excluded_billing_ars(" SENASA   SUBSIDIADO ")
    assert app.is_excluded_billing_ars("seNaSa-sub")
    assert not app.medication_ars_is_selectable("SENASA SUBSIDIADO")
    assert app.medication_ars_is_selectable("SENASA CONTRIBUTIVO")
    assert app.medication_ars_is_selectable("HUMANO")


def test_trash_dialog_loads_real_rows_without_dead_callback():
    qt_app = _qt_app()
    main = _MainWindowStub()
    deleted = [{
        "id": 77,
        "numero": 990077,
        "nombre": "Paciente prueba",
        "fecha": "2026-07-28",
        "ars": "HUMANO",
        "total": 1250,
        "username": "facturador",
        "estado_facturacion": app.BILLING_PENDING,
        "deleted_at": "2026-07-28 10:00:00",
        "deleted_by": "admin",
        "deleted_reason": "Prueba",
        "document_storage_mode": "SNAPSHOT",
    }]
    with patch.object(app, "list_deleted_receipts", return_value=deleted):
        dialog = app.ReceiptTrashDialog(main, main)
        qt_app.processEvents()
        assert dialog.table.rowCount() == 1
        assert dialog.table.item(0, 0).data(app.Qt.UserRole) == 77
        assert dialog.table.item(0, 7).text() == app.billing_status_label(
            app.BILLING_PENDING
        )
        assert dialog.btn_restore.isEnabled()
        dialog.close()


def test_ars_manager_loads_editable_profile_fields():
    qt_app = _qt_app()
    parent = _MainWindowStub()
    profile = {
        "display_name": "ARS DEMO",
        "ars_rnc": "101-00000-1",
        "ars_address": "Santo Domingo",
        "ars_phone": "809-555-0101",
        "ars_email": "facturacion@example.com",
        "administrative_notes": "Enviar relación mensual.",
    }
    with (
        patch.object(app, "ars_list", return_value=["ARS DEMO"]),
        patch.object(
            app,
            "load_ars_runtime_data",
            return_value={"sala_emergencia": 0, "consulta_price": 0},
        ),
        patch.object(app, "get_ars_billing_profile", return_value=profile),
    ):
        dialog = app.ARSManagerDialog(parent.current_user, parent)
        _wait_for_ars_manager_load(qt_app, dialog)
        assert dialog.profile_display_name.text() == "ARS DEMO"
        assert dialog.profile_rnc.text() == "101-00000-1"
        assert dialog.profile_phone.text() == "809-555-0101"
        assert dialog.profile_email.text() == "facturacion@example.com"
        assert dialog.profile_address.toPlainText() == "Santo Domingo"
        dialog.close()
