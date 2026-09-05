from uuid import UUID
from unittest.mock import Mock

import pytest
import CALCULOS_QT as app
from receipt_uuid import (
    InvalidOptionalUUID,
    normalize_optional_uuid,
    receipt_admission_uuid,
    receipt_persistence_diagnostics,
    receipt_save_error_message,
)

VALID = "abcdefab-1234-4234-8234-123456789abc"


@pytest.mark.parametrize("value", [None, "", "   ", "\t\r\n"])
def test_absent_uuid(value):
    assert normalize_optional_uuid(value) is None


@pytest.mark.parametrize(
    "value", [VALID, VALID.upper(), "  " + VALID + "  ", VALID.replace("-", "")]
)
def test_canonical_uuid(value):
    assert normalize_optional_uuid(value) == VALID


def test_uuid_object_preserved():
    identifier = UUID(VALID)
    assert normalize_optional_uuid(identifier) is identifier
    assert receipt_admission_uuid(identifier, lambda message: None) == VALID


@pytest.mark.parametrize(
    "value", ["abc123", "0", 0, -1, 1, False, [], {}, b"", VALID[:-1], VALID + "0"]
)
def test_invalid_not_coerced_or_disclosed(value):
    with pytest.raises(InvalidOptionalUUID, match="no es un UUID válido"):
        normalize_optional_uuid(value)


@pytest.mark.parametrize("value", [None, "", "  ", VALID, "SECRET-INVALID"])
def test_safe_parameter_diagnostics(value):
    logs = []
    if value == "SECRET-INVALID":
        with pytest.raises(InvalidOptionalUUID):
            receipt_admission_uuid(value, logs.append)
        assert "uuid_validation_status=INVALID" in logs[0]
    else:
        assert receipt_admission_uuid(value, logs.append) == normalize_optional_uuid(
            value
        )
        assert "uuid_validation_status=VALID" in logs[0]
    assert "postgres_type=uuid" in logs[0]
    assert "SECRET" not in logs[0] and VALID not in logs[0]


def test_invalid_uuid_rejected_before_repository(monkeypatch):
    connection = Mock(side_effect=AssertionError("SQL must not execute"))
    monkeypatch.setattr(app, "db_connect", connection)
    from tests.test_receipt_optional_uuid_postgres import save

    with pytest.raises(InvalidOptionalUUID):
        save(
            admission_attention={
                "attention_id": 1,
                "patient_id": 2,
                "ars": "FUTURO",
                "global_attention_id": "abc123",
            }
        )
    connection.assert_not_called()


def test_diagnostics_rethrow_and_hide_parameters():
    logs = []
    with receipt_persistence_diagnostics(
        logs.append, bypass=True, global_id=None, source_id=None
    ):
        pass
    assert logs == []
    error = RuntimeError("SECRET SQL VALUES")
    with pytest.raises(RuntimeError) as captured:
        with receipt_persistence_diagnostics(
            logs.append, bypass=True, global_id=None, source_id=None
        ):
            raise error
    assert captured.value is error
    assert "exception_type=RuntimeError" in logs[0]
    assert "has_global_attention_id=False" in logs[0]
    assert "SECRET" not in logs[0]


def test_user_message_never_exposes_sql():
    assert "SECRET" not in receipt_save_error_message(RuntimeError("SECRET SQL"))
    assert (
        receipt_save_error_message(InvalidOptionalUUID("Identificador inválido"))
        == "Identificador inválido"
    )
    assert receipt_save_error_message(PermissionError("Sin permiso")) == "Sin permiso"


@pytest.mark.parametrize(
    "error",
    [RuntimeError("SECRET SQL VALUES"), InvalidOptionalUUID("Identificador inválido")],
)
def test_worker_reports_safe_failure_and_no_success(monkeypatch, error):
    worker = app.PDFDatabaseWorker()
    worker.signals = Mock()
    logs = []
    monkeypatch.setattr(app, "write_runtime_log", logs.append)
    monkeypatch.setattr(app, "get_next_recibo_number", lambda: 1)
    monkeypatch.setattr(app, "save_receipt_with_items", Mock(side_effect=error))
    worker.process(
        {
            "patient": "PRIVATE PATIENT",
            "date_str": "2026-09-05",
            "dx_raw": "PRIVATE DX",
            "ars_name": "FUTURO",
            "sala": 0,
            "grouped": [],
            "total_general": 100,
            "editing_id": None,
            "editing_num": None,
            "current_user": {"username": "test"},
            "is_backdated": False,
            "verification_bypass": {"reason": "Prueba controlada"},
        }
    )
    worker.signals.finished_signal.emit.assert_called_once_with(
        False, receipt_save_error_message(error), "", 0
    )
    assert "SECRET" not in str(logs) and "PRIVATE" not in str(logs)
