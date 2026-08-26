from pathlib import Path
from unittest.mock import patch

import pytest

import CALCULOS_QT as app
import admission_bridge


class _InsertConnection:
    def __init__(self):
        self.con = self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def cursor(self):
        return object()


class _ProjectionCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _ProjectionConnection:
    def __init__(self, row):
        self._row = row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, _sql, _params=()):
        return _ProjectionCursor(self._row)


class _Source:
    def history_source_state(self):
        return {
            "source_instance_id": "SOURCE-A",
            "total_count": 2,
            "active_count": 2,
            "max_attention_id": 2,
            "max_updated_at": "2026-08-26 13:17:54",
        }

    def list_history_projection_batch(self, *, after_attention_id, limit):
        if after_attention_id:
            return []
        return [type("AttentionRef", (), {"attention_id": 2})()]


def _attention_payload(*, session_id="SESSION-A", turn_id=4):
    return {
        "source_instance_id": "SOURCE-A",
        "attention_id": 1,
        "patient_id": 10,
        "turn_id": turn_id,
        "service_date": "2026-08-26",
        "service_time": "13:17:00",
        "name": "PACIENTE DE PRUEBA",
        "coverage_status": "ASEGURADO_VALIDADO",
        "canonical_ars": "FUTURO",
        "nss_clean": "000000001",
        "cedula_clean": "00000000001",
        "attention_type": "EMERGENCIA",
        "billing_readiness": app.READINESS_READY,
        "source_status": "ACTIVA",
        "operational_session_id": session_id,
        "operational_source_id": "OPERATIONAL-SOURCE",
        "generation": 3,
    }


def test_repository_resolves_the_active_distribution_database_at_construction(
    monkeypatch, tmp_path: Path
):
    data_root = tmp_path / "private-admission-data"
    monkeypatch.delenv("ADMISSION_DB_PATH", raising=False)
    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(data_root))

    repository = admission_bridge.AdmissionReadOnlyRepository()

    assert repository.db_path == data_root.resolve() / "pacientes.db"


def test_repository_honors_explicit_admission_database_path(monkeypatch, tmp_path: Path):
    explicit_database = tmp_path / "selected" / "pacientes.db"
    monkeypatch.setenv("ADMISSION_DB_PATH", str(explicit_database))

    repository = admission_bridge.AdmissionReadOnlyRepository()

    assert repository.db_path == explicit_database.resolve()


def test_frozen_repository_uses_the_same_private_data_folder_as_v15(
    monkeypatch, tmp_path: Path
):
    executable = tmp_path / "SIGEH" / "SIGEH.exe"
    monkeypatch.delenv("ADMISSION_DB_PATH", raising=False)
    monkeypatch.delenv("EMERGENCIAS_DATA_DIR", raising=False)
    monkeypatch.setattr(admission_bridge.sys, "frozen", True, raising=False)
    monkeypatch.setattr(admission_bridge.sys, "executable", str(executable))

    repository = admission_bridge.AdmissionReadOnlyRepository()

    assert repository.db_path == executable.parent / "_internal" / "data" / "pacientes.db"


def test_projection_uses_central_turn_identity_instead_of_local_mirror_id():
    captured = {}

    def capture_values(_cursor, _sql, rows, page_size):
        captured["rows"] = rows
        captured["page_size"] = page_size

    with patch.object(
        app,
        "_central_turn_ids_by_operational_session",
        return_value={"SESSION-A": 3942},
    ), patch.object(
        app, "db_connect", return_value=_InsertConnection()
    ), patch.object(app.psycopg2.extras, "execute_values", side_effect=capture_values):
        inserted = app.sync_admission_projection([_attention_payload()])

    assert inserted == 1
    assert captured["rows"][0][3] == 3942
    assert captured["rows"][0][28] == "SESSION-A"


def test_central_turn_mapping_is_loaded_once_for_all_operational_sessions():
    class Cursor:
        def fetchall(self):
            return [
                {"operational_session_id": "SESSION-A", "turn_id": 3942},
                {"operational_session_id": "SESSION-B", "turn_id": None},
            ]

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def execute(self, sql, params):
            assert "admission_operational_sessions" in sql
            assert params == (["SESSION-A", "SESSION-B"],)
            return Cursor()

    with patch.object(app, "db_connect", return_value=Connection()):
        mapping = app._central_turn_ids_by_operational_session([
            {"operational_session_id": "SESSION-B"},
            {"operational_session_id": "SESSION-A"},
            {"operational_session_id": ""},
        ])

    assert mapping == {"SESSION-A": 3942}
    assert app._central_turn_ids_by_operational_session([]) == {}


def test_projection_keeps_legacy_turn_when_no_central_session_mapping_exists():
    captured = {}

    def capture_values(_cursor, _sql, rows, page_size):
        captured["rows"] = rows

    with patch.object(
        app, "_central_turn_ids_by_operational_session", return_value={}
    ), patch.object(
        app, "db_connect", return_value=_InsertConnection()
    ), patch.object(app.psycopg2.extras, "execute_values", side_effect=capture_values):
        app.sync_admission_projection([
            _attention_payload(session_id="", turn_id=17)
        ])

    assert captured["rows"][0][3] == 17


def test_reconcile_revalidates_central_projection_on_every_open():
    projected = {
        "total_count": 2,
        "active_count": 2,
        "max_attention_id": 2,
        "max_updated_at": "2026-08-26 13:17:54",
        "turn_mismatch_count": 0,
    }
    calls = []

    def connect():
        calls.append(True)
        return _ProjectionConnection(projected)

    with patch.object(app, "db_connect", side_effect=connect):
        first = app.ensure_admission_history_projection(_Source())
        second = app.ensure_admission_history_projection(_Source())

    assert first["already_current"] is True
    assert second["already_current"] is True
    assert len(calls) == 2


def test_reconcile_verifies_materialized_rows_before_reporting_success():
    missing = {
        "total_count": 0,
        "active_count": 0,
        "max_attention_id": 0,
        "max_updated_at": "",
        "turn_mismatch_count": 0,
    }
    verified = {
        "total_count": 2,
        "active_count": 2,
        "max_attention_id": 2,
        "max_updated_at": "2026-08-26 13:17:54",
        "turn_mismatch_count": 0,
    }
    connections = iter([
        _ProjectionConnection(missing),
        _ProjectionConnection(verified),
    ])
    with patch.object(app, "db_connect", side_effect=lambda: next(connections)), patch.object(
        app, "sync_admission_projection", return_value=1
    ):
        result = app.ensure_admission_history_projection(_Source())

    assert result == {
        "synced": 1,
        "source": _Source().history_source_state(),
        "already_current": False,
    }


def test_reconcile_rejects_a_projection_with_wrong_central_turn_after_sync():
    missing = {
        "total_count": 0,
        "active_count": 0,
        "max_attention_id": 0,
        "max_updated_at": "",
        "turn_mismatch_count": 0,
    }
    wrong_turn = {
        "total_count": 2,
        "active_count": 2,
        "max_attention_id": 2,
        "max_updated_at": "2026-08-26 13:17:54",
        "turn_mismatch_count": 1,
    }
    connections = iter([
        _ProjectionConnection(missing),
        _ProjectionConnection(wrong_turn),
    ])
    with patch.object(app, "db_connect", side_effect=lambda: next(connections)), patch.object(
        app, "sync_admission_projection", return_value=1
    ):
        with pytest.raises(app.AdmissionBridgeError, match="no coincide"):
            app.ensure_admission_history_projection(_Source())
