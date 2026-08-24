import os
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog

import CALCULOS_QT as app
from historical_documents import (
    normalize_receipt_record,
    normalize_report_record,
    parse_shift_closure_source_key,
)
from receipt_documents import SnapshotMissingError
from report_documents import ReportDocumentError, ReportSnapshotMissingError


def _qt_app():
    return QApplication.instance() or QApplication([])


def _history_row(
    attention_id=81,
    status=app.BILLING_PENDING,
    receipt_id=5001,
):
    return {
        "source_instance_id": "V15-CENTRAL",
        "attention_id": attention_id,
        "patient_id": 901,
        "patient_name": "PACIENTE CONTROLADO",
        "cedula_snapshot": "",
        "nss_snapshot": "",
        "service_date": "2026-08-08",
        "service_time": "09:00:00",
        "turn_id": 15,
        "processing_turn_id": 15,
        "turn_scope": "TURNO ACTUAL",
        "service_type": "EMERGENCIA",
        "canonical_ars": "ARS CONTROLADA",
        "admission_username": "OPERADOR",
        "has_detail_sheet": True,
        "receipt_id": receipt_id,
        "receipt_number": 900001 if receipt_id else None,
        "estado_facturacion": status,
        "estado_documento": "FINAL",
    }


def test_nominal_historical_contracts_and_composite_source_key():
    receipt = normalize_receipt_record(
        {"id": 8, "numero": 80, "document_storage_mode": "LEGACY_PDF"}
    )
    report = normalize_report_record(
        {
            "record_id": 7,
            "source_table": "billing_shift_closures",
            "source_key": "V15-CENTRAL|7",
        }
    )
    assert receipt.receipt_id == 8
    assert report.source_key == "V15-CENTRAL|7"
    assert parse_shift_closure_source_key(report.source_key) == ("V15-CENTRAL", 7)


def test_history_pending_uses_server_identity_and_complete_opens_linked_receipt():
    _qt_app()

    class _Signal:
        def __init__(self):
            self.callback = None

        def connect(self, callback):
            self.callback = callback

        def emit(self, *args):
            if self.callback:
                self.callback(*args)

    class _EligibilityWorker:
        def __init__(self, row_data, _user, _session_id, _parent):
            self.row_data = dict(row_data)
            self.resolved = _Signal()
            self.failed = _Signal()
            self.finished = _Signal()

        def deleteLater(self):
            return None

        def start(self):
            projection = dict(self.row_data)
            projection.update({
                "readiness": app.READINESS_READY,
                "coverage_status": "ASEGURADO",
                "source_status": "ACTIVA",
            })
            self.resolved.emit({"eligible": True, "_projection": projection}, 0.1)

    with patch.object(app.QTimer, "singleShot", return_value=None):
        dialog = app.AdmissionHistoryDialog(
            current_user={"username": "audit", "role": app.ROLE_AUDIT}
        )
    try:
        dialog._append_row(_history_row(receipt_id=None))
        dialog.table.selectRow(0)
        QApplication.processEvents()
        payload = dialog.table.item(0, 0).data(Qt.UserRole)
        assert payload["attention_id"] == 81
        assert payload["source_instance_id"] == "V15-CENTRAL"
        assert dialog.use_button.isEnabled()
        assert dialog.open_sheet_button.isEnabled()
        assert not dialog.open_receipt_button.isEnabled()
        with patch.object(
            app, "AdmissionHistoryEligibilityWorker", _EligibilityWorker
        ):
            dialog._use_selected_for_billing()
        selected = dialog.selected_for_billing()
        assert selected.attention_id == 81
        assert selected.source_instance_id == "V15-CENTRAL"
    finally:
        dialog.close()

    with patch.object(app.QTimer, "singleShot", return_value=None):
        complete = app.AdmissionHistoryDialog(
            current_user={"username": "audit", "role": app.ROLE_AUDIT}
        )
    try:
        complete._append_row(_history_row(status=app.BILLING_INVOICED))
        complete.table.selectRow(0)
        QApplication.processEvents()
        assert not complete.use_button.isEnabled()
        assert complete.open_receipt_button.isEnabled()
        assert complete.open_receipt_button.text() == "Abrir facturación"
        with patch.object(
            complete, "_confirm_open_complete_receipt", return_value=True
        ), patch.object(app, "get_projected_billable_attention") as lookup:
            complete._open_selected_receipt()
        lookup.assert_not_called()
        assert complete.selected_receipt_for_billing() == 5001
    finally:
        complete.close()


def test_complete_attention_without_receipt_reports_inconsistency():
    _qt_app()
    with patch.object(app.QTimer, "singleShot", return_value=None):
        dialog = app.AdmissionHistoryDialog(
            current_user={"username": "audit", "role": app.ROLE_AUDIT}
        )
    try:
        dialog._append_row(
            _history_row(status=app.BILLING_INVOICED, receipt_id=None)
        )
        dialog.table.selectRow(0)
        QApplication.processEvents()
        assert dialog.open_receipt_button.isEnabled()
        with patch.object(app.QMessageBox, "information") as message:
            dialog._open_selected_receipt()
        assert dialog.selected_receipt_for_billing() is None
        assert "no fue posible localizar" in message.call_args.args[2]
    finally:
        dialog.close()


def test_history_selection_returns_to_existing_validation_claim_flow():
    selected = SimpleNamespace(attention_id=81, source_instance_id="V15-CENTRAL")

    class FakeHistory:
        def __init__(self, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def selected_receipt_for_billing(self):
            return None

        def selected_for_billing(self):
            return selected

    host = SimpleNamespace(
        current_user={"role": app.ROLE_AUDIT},
        _history_attention=None,
        accepted=False,
    )
    host.accept = lambda: setattr(host, "accepted", True)
    with patch.object(app, "AdmissionHistoryDialog", FakeHistory):
        app.AdmissionValidationDialog._open_admission_history(host)
    assert host._history_attention is selected
    assert host.accepted


def test_complete_history_selection_propagates_receipt_to_main_window_flow():
    class FakeHistory:
        def __init__(self, **_kwargs):
            pass

        def exec(self):
            return QDialog.Accepted

        def selected_receipt_for_billing(self):
            return 5001

        def selected_for_billing(self):
            return None

    host = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN},
        _history_attention=None,
        _history_receipt_id=None,
        accepted=False,
    )
    host.accept = lambda: setattr(host, "accepted", True)
    with patch.object(app, "AdmissionHistoryDialog", FakeHistory):
        app.AdmissionValidationDialog._open_admission_history(host)
    assert host._history_receipt_id == 5001
    assert host.accepted


def test_canonical_receipt_opener_selects_read_only_by_role():
    loader = SimpleNamespace(calls=[])

    def load(receipt_id, *, allow_read_only=False):
        loader.calls.append((receipt_id, allow_read_only))
        return True

    admin_host = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN},
        load_recibo_for_editing=load,
    )
    aux_host = SimpleNamespace(
        current_user={"role": app.ROLE_AUX},
        load_recibo_for_editing=load,
    )
    assert app.MainWindow.open_receipt_in_billing(admin_host, 5001)
    assert app.MainWindow.open_receipt_in_billing(aux_host, 5002)
    # Both roles that may consult Admission history open complete receipts in
    # read-only mode; neither call reopens or edits the receipt.
    assert loader.calls == [(5001, True), (5002, True)]


def test_receipt_resolver_uses_stored_blob_when_snapshot_and_file_are_absent(tmp_path):
    row = {
        "id": 26,
        "numero": 100,
        "document_storage_mode": app.STORAGE_SNAPSHOT,
        "pdf_filename": "receipt_100.pdf",
    }

    class Cursor:
        def fetchone(self):
            return row

    class Connection:
        def execute(self, *_args, **_kwargs):
            return Cursor()

    @contextmanager
    def connect():
        yield Connection()

    expected = str(tmp_path / "legacy.pdf")
    with (
        patch.object(app, "db_connect", connect),
        patch.object(app, "load_current_receipt_snapshot", side_effect=SnapshotMissingError("missing")),
        patch.object(app, "load_latest_receipt_snapshot", side_effect=SnapshotMissingError("missing")),
        patch.object(app, "_legacy_receipt_pdf_path", side_effect=FileNotFoundError()),
        patch.object(app, "_stored_pdf_blob_path", return_value=expected),
        patch.object(app, "write_runtime_log"),
    ):
        assert app.resolve_receipt_document(26, "open") == expected


def test_shift_closure_legacy_key_never_leaks_unpack_error():
    class Cursor:
        def __init__(self, rows=()):
            self.rows = list(rows)

        def fetchall(self):
            return self.rows

    class Connection:
        def execute(self, _sql, params=()):
            if tuple(params) == (7,):
                return Cursor(
                    [{
                        "id": 7,
                        "filename": "closure.pdf",
                        "document_storage_mode": "LEGACY_PDF",
                        "source_instance_id": "V15-CENTRAL",
                        "source_key": "V15-CENTRAL|7",
                    }]
                )
            return Cursor()

    row = app._report_source_row(Connection(), "billing_shift_closures", "7")
    assert row["source_key"] == "V15-CENTRAL|7"
    try:
        app._report_source_row(Connection(), "billing_shift_closures", "invalid")
    except ReportDocumentError as exc:
        assert "unpack" not in str(exc).lower()
    else:
        raise AssertionError("An invalid historical key must produce a controlled error")


def test_report_history_preserves_composite_key_in_hidden_row_contract():
    _qt_app()
    row = {
        "record_id": 7,
        "source_key": "V15-CENTRAL|7",
        "source_table": "billing_shift_closures",
        "report_type": "Cierre automático de turno",
        "start_date": "2026-08-08",
        "end_date": "2026-08-08",
        "generated_at": "2026-08-08 10:00:00",
        "generated_by": "SISTEMA",
        "filepath": "closure.pdf",
        "totals_json": "{}",
        "document_storage_mode": "LEGACY_PDF",
    }
    with (
        patch.object(app, "ars_list", return_value=[]),
        patch.object(app, "list_usernames", return_value=[]),
        patch.object(app, "list_report_history", return_value=[row]),
    ):
        dialog = app.LegacyReportsDialog(
            {"username": "audit", "role": app.ROLE_AUDIT}
        )
    try:
        assert dialog.table.item(0, 7).text() == "7"
        assert dialog.table.item(0, 7).data(Qt.UserRole) == "V15-CENTRAL|7"
    finally:
        dialog.close()


def test_report_resolver_uses_one_canonical_blob_fallback(tmp_path):
    class Connection:
        pass

    @contextmanager
    def connect():
        yield Connection()

    source = {
        "id": 7,
        "filename": "closure.pdf",
        "document_storage_mode": "LEGACY_PDF",
        "source_key": "V15-CENTRAL|7",
    }
    expected = str(tmp_path / "closure.pdf")
    with (
        patch.object(app, "db_connect", connect),
        patch.object(app, "_report_source_row", return_value=source),
        patch.object(app, "load_current_report_snapshot", side_effect=ReportSnapshotMissingError("missing")),
        patch.object(app, "load_latest_report_snapshot", side_effect=ReportSnapshotMissingError("missing")),
        patch.object(app, "find_external_document", side_effect=ReportDocumentError("missing")),
        patch.object(app, "_stored_pdf_blob_path", return_value=expected),
        patch.object(app, "write_runtime_log"),
    ):
        assert (
            app.resolve_report_document(
                "billing_shift_closures", "V15-CENTRAL|7", "preview_print"
            )
            == expected
        )
