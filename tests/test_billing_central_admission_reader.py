from pathlib import Path
from unittest.mock import patch

import pytest

import CALCULOS_QT as app
import admission_bridge


class _Cursor:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _Connection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=None):
        compact = " ".join(str(sql).split())
        self.calls.append((compact, tuple(params or ())))
        return _Cursor(self.rows)


CENTRAL_CONTEXT = {
    "source_instance_id": "748d96bb-808f-4f66-b3fe-d256326b20f9",
    "operational_source_id": "748d96bb-808f-4f66-b3fe-d256326b20f9",
    "turn_id": 3943,
    "generation": 1,
}


def test_billing_selector_reads_postgresql_without_reconciling_local_sqlite():
    connection = _Connection()
    with (
        patch.object(
            app,
            "repair_admission_projection_before_billing",
            side_effect=AssertionError("Billing must not read or write local SQLite"),
        ),
        patch.object(app, "get_central_operational_context", return_value=CENTRAL_CONTEXT),
        patch.object(app, "db_connect", return_value=connection),
    ):
        result = app.list_projected_current_and_previous_billable_attentions(
            repository=object()
        )

    assert result == []
    assert "admission_attention_projection" in connection.calls[-1][0]


def test_billing_history_reads_postgresql_without_reconciling_local_sqlite():
    connection = _Connection()
    with (
        patch.object(
            app,
            "repair_admission_projection_before_billing",
            side_effect=AssertionError("Billing must not read or write local SQLite"),
        ),
        patch.object(app, "get_central_operational_context", return_value=CENTRAL_CONTEXT),
        patch.object(app, "db_connect", return_value=connection),
    ):
        result = app.list_admission_history(
            current_user={"role": app.ROLE_ADMIN},
            repository=object(),
        )

    assert result["rows"] == []
    assert result["has_more"] is False
    assert result["next_cursor"] is None
    assert result["full_history"] is True
    assert "admission_attention_projection" in connection.calls[-1][0]


def test_privileged_selector_does_not_treat_every_previous_turn_as_inherited():
    connection = _Connection()
    with (
        patch.object(app, "get_central_operational_context", return_value=CENTRAL_CONTEXT),
        patch.object(app, "db_connect", return_value=connection),
        patch.object(app, "repair_admission_projection_before_billing", return_value={}),
    ):
        app.list_projected_current_and_previous_billable_attentions(
            turn_filter="TODOS",
            allow_all_unbilled=True,
            repository=object(),
        )

    sql, _params = connection.calls[-1]
    assert "OR (%s AND p.turn_id<>cs.turn_id)" not in sql
    assert "p.turn_id=cs.turn_id OR inheritance.attention_id IS NOT NULL" in sql


def test_optional_distributed_ids_never_become_literal_none():
    row = {
        "id": 7,
        "paciente_id": 70,
        "turno_id": 3,
        "nombre": "PACIENTE PRUEBA",
        "fecha": "2026-08-28",
        "hora": "10:00:00",
        "nss": "123",
        "nss_clean": "123",
        "cedula": "00100000001",
        "cedula_clean": "00100000001",
        "ars": "FUTURO",
        "tipo_atencion": "EMERGENCIA",
        "estado": "ACTIVA",
        "updated_at": "2026-08-28 10:00:00",
        "created_at": "2026-08-28 10:00:00",
        "hoja": "",
        "numero_autorizacion": None,
        "admission_username": "tester",
        "global_attention_id": None,
        "global_patient_id": None,
        "operational_source_id": None,
        "operational_session_id": None,
        "generation": None,
        "origin_device_id": None,
        "version": None,
    }
    repository = admission_bridge.AdmissionReadOnlyRepository("unused.db")
    repository._source_instance_id = "SOURCE-A"
    repository._source_schema_version = 15

    attention = repository._row_to_attention(row)

    assert attention.global_attention_id == ""
    assert attention.global_patient_id == ""
    assert attention.operational_source_id == ""
    assert attention.operational_session_id == ""
    assert attention.origin_device_id == ""


def test_safe_failure_log_classifies_invalid_uuid_without_logging_raw_message():
    class InvalidUuidError(RuntimeError):
        pgcode = "22P02"

    error = InvalidUuidError("patient_name=PRIVATE invalid uuid None")
    with patch.object(app, "write_main_app_log") as write_log:
        category = app.log_billing_admission_query_failure(
            error,
            operation="load_admission_history_batch",
            sql_stage="CENTRAL_HISTORY_QUERY",
            current_user={"role": app.ROLE_ADMIN},
            context=CENTRAL_CONTEXT,
            elapsed_ms=12.5,
        )

    assert category == "DATA_ERROR"
    event, = write_log.call_args.args
    kwargs = write_log.call_args.kwargs
    assert event == "BILLING_ADMISSION_QUERY_FAILED"
    assert "exception_type=InvalidUuidError" in kwargs["details"]
    assert "safe_error_message=invalid_projected_metadata" in kwargs["details"]
    assert "PRIVATE" not in kwargs["details"]
    assert kwargs["elapsed_ms"] == 12.5


def test_queue_diagnostic_reports_every_stage_and_exclusion_reason():
    class Reader:
        def current_operational_context(self):
            return CENTRAL_CONTEXT

        def fetch_all(self, _sql, _params, **_kwargs):
            return [
                {
                    "stage_0_central": 97,
                    "stage_1_active": 97,
                    "stage_2_same_source": 68,
                    "stage_3_turn_scope": 68,
                    "stage_4_ready": 68,
                    "stage_5_ars_visible": 36,
                    "stage_6_coverage": 36,
                    "stage_7_emergency": 36,
                    "stage_8_without_receipt": 36,
                    "stage_9_without_foreign_claim": 36,
                    "stage_10_eligible": 36,
                }
            ], {"elapsed_ms": 8.0}

    with patch.object(app, "write_main_app_log") as write_log:
        result = app.diagnose_billing_admission_queue(
            current_user={"role": app.ROLE_ADMIN},
            central_reader=Reader(),
        )

    assert result["existing"] == 97
    assert result["eligible"] == 36
    assert result["not_eligible"] == 61
    assert result["exclusions"]["WRONG_OPERATIONAL_SOURCE"] == 29
    assert result["exclusions"]["ARS_EXCLUDED"] == 32
    assert result["exclusions"]["NOT_READY"] == 0
    assert write_log.call_args.args == ("BILLING_ADMISSION_QUEUE_STAGES",)


def test_full_history_role_still_cannot_bill_uninherited_historical_attention():
    access = app.evaluate_admission_billing_access(
        {"turn_id": 12, "explicitly_inherited": False},
        {"role": app.ROLE_ADMIN},
        {"turn_id": 13},
    )

    assert access["turn_scope"] == "HISTORICAL"
    assert access["can_use_for_billing"] is False
    assert access["reason_code"] == "HISTORICAL_ROLE_DENIED"


def test_matching_turn_from_another_operational_source_is_not_current():
    access = app.evaluate_admission_billing_access(
        {
            "turn_id": CENTRAL_CONTEXT["turn_id"],
            "operational_source_id": "ANOTHER-CENTRAL-SOURCE",
        },
        {"role": app.ROLE_ADMIN},
        CENTRAL_CONTEXT,
    )

    assert access["turn_scope"] == "HISTORICAL"
    assert access["can_use_for_billing"] is False


def test_inheritance_from_another_operational_source_is_not_billable():
    access = app.evaluate_admission_billing_access(
        {
            "turn_id": CENTRAL_CONTEXT["turn_id"] - 1,
            "operational_source_id": "ANOTHER-CENTRAL-SOURCE",
            "explicitly_inherited": True,
        },
        {"role": app.ROLE_ADMIN},
        CENTRAL_CONTEXT,
    )

    assert access["turn_scope"] == "HISTORICAL"
    assert access["can_use_for_billing"] is False


def test_final_eligibility_rejects_not_ready_and_dismissed_rows():
    base = {
        "source_instance_id": "SOURCE-A",
        "attention_id": 12,
        "global_attention_id": "11111111-1111-1111-1111-111111111111",
        "operational_source_id": CENTRAL_CONTEXT["operational_source_id"],
        "turn_id": CENTRAL_CONTEXT["turn_id"],
        "source_status": "ACTIVA",
        "service_type": "EMERGENCIA",
        "coverage_status": "ASEGURADO_VALIDADO",
        "canonical_ars": "FUTURO",
        "ars_billing_enabled": True,
        "active_operational_source_id": CENTRAL_CONTEXT["operational_source_id"],
        "active_turn_id": CENTRAL_CONTEXT["turn_id"],
    }
    for changes, reason_code in (
        ({"readiness": "PENDIENTE"}, "NOT_READY"),
        (
            {"readiness": app.READINESS_READY, "dismissed_from_quick_list": True},
            "DISMISSED",
        ),
    ):
        row = {**base, **changes}
        connection = _Connection([row])
        with patch.object(app, "db_connect", return_value=connection):
            result = app.evaluate_attention_billing_eligibility(
                12,
                {"role": app.ROLE_ADMIN},
                global_attention_id=row["global_attention_id"],
            )

        assert result["eligible"] is False
        assert result["reason_code"] == reason_code


def test_history_ordering_does_not_cast_untrusted_text_dates():
    connection = _Connection()
    with (
        patch.object(app, "get_central_operational_context", return_value=CENTRAL_CONTEXT),
        patch.object(app, "db_connect", return_value=connection),
    ):
        app.list_admission_history(current_user={"role": app.ROLE_ADMIN})

    sql, _params = connection.calls[-1]
    assert "COALESCE(p.created_at_effective_utc,TO_TIMESTAMP(0))" in sql
    assert "p.service_time,''),'00:00:00'))::TIMESTAMPTZ" not in sql
    assert "p.synced_at,'')::TIMESTAMPTZ" not in sql


def test_explicit_current_history_filter_requires_operational_source_and_turn():
    connection = _Connection()
    with (
        patch.object(app, "get_central_operational_context", return_value=CENTRAL_CONTEXT),
        patch.object(app, "db_connect", return_value=connection),
    ):
        app.list_admission_history(
            current_user={"role": app.ROLE_ADMIN},
            turn_filter="ACTUAL",
        )

    sql, _params = connection.calls[-1]
    assert "p.operational_source_id::TEXT=cs.operational_source_id" in sql
    assert "%s='ACTUAL' AND p.turn_id=cs.turn_id" in sql
    assert "OR p.source_instance_id=cs.source_instance_id" not in sql


def test_global_receipt_identity_migration_is_idempotent_and_non_destructive():
    migration = (
        Path(app.APP_DIR)
        / "migrations"
        / "20260828_billing_admission_bridge_identity.sql"
    ).read_text(encoding="utf-8").upper()

    assert "ADD COLUMN IF NOT EXISTS ADMISSION_GLOBAL_ATTENTION_ID UUID" in migration
    assert "CREATE INDEX IF NOT EXISTS" in migration
    assert "IDX_ADMISSION_PROJECTION_BILLING_SCOPE" in migration
    assert "IDX_ADMISSION_PROJECTION_BILLING_HISTORY" in migration
    assert "UPDATE RECIBOS" in migration
    assert "\nDELETE FROM" not in migration
    assert "DROP TABLE" not in migration
    assert "TRUNCATE" not in migration
    build_spec = (Path(app.APP_DIR) / "build_app.spec").read_text(encoding="utf-8")
    assert "20260828_billing_admission_bridge_identity.sql" in build_spec


def test_receipt_matching_prefers_global_attention_identity_with_legacy_fallback():
    sql = " ".join(app.admission_receipt_identity_sql("receipt", "projection").split())

    assert (
        "receipt.admission_global_attention_id= projection.global_attention_id"
        in sql
    )
    assert "receipt.admission_atencion_id=projection.attention_id" in sql
    assert "receipt.admission_source_instance_id" in sql

    connection = _Connection()
    with patch.object(app, "db_connect", return_value=connection):
        app.get_receipt_for_admission_attention(
            7,
            "SOURCE-A",
            "11111111-1111-1111-1111-111111111111",
        )
    direct_sql, _params = connection.calls[-1]
    assert "admission_global_attention_id IS NULL" in direct_sql


@pytest.mark.parametrize(
    ("row", "user", "access", "reason_code"),
    (
        (
            {"readiness": app.READINESS_READY, "canonical_ars": "FUTURO"},
            {"role": app.ROLE_ADMIN},
            {"reason_code": "HISTORICAL_ROLE_DENIED"},
            "HISTORICAL_ROLE_DENIED",
        ),
        (
            {"readiness": "PENDIENTE", "canonical_ars": "FUTURO"},
            {"role": app.ROLE_ADMIN},
            {"reason_code": "CURRENT_PENDING_ALLOWED"},
            "NOT_READY",
        ),
        (
            {"readiness": app.READINESS_READY, "canonical_ars": "SENASA SUBSIDIADO"},
            {"role": app.ROLE_ADMIN},
            {"reason_code": "CURRENT_PENDING_ALLOWED"},
            "SUBSIDIZED_EXCLUDED",
        ),
        (
            {"readiness": app.READINESS_READY, "canonical_ars": "UNIVERSAL"},
            {"role": app.ROLE_ADMIN},
            {"reason_code": "CURRENT_PENDING_ALLOWED"},
            "ARS_EXCLUDED",
        ),
        (
            {
                "readiness": app.READINESS_READY,
                "canonical_ars": "SIN SEGURO",
                "coverage_status": app.COVERAGE_UNINSURED_DECLARED,
            },
            {"role": app.ROLE_AUX},
            {"reason_code": "CURRENT_PENDING_ALLOWED"},
            "UNINSURED_NOT_ALLOWED",
        ),
        (
            {"readiness": app.READINESS_READY, "canonical_ars": "FUTURO"},
            {"role": app.ROLE_ADMIN},
            {"reason_code": "ALREADY_BILLED"},
            "ALREADY_BILLED",
        ),
    ),
)
def test_final_projection_denials_are_explicit(row, user, access, reason_code):
    denial = app._billing_projection_denial(row, user, access)

    assert denial is not None
    assert denial[0] == reason_code


def test_projection_postchecks_report_pending_receipt_and_foreign_claim():
    allowed_row = {
        "readiness": app.READINESS_READY,
        "canonical_ars": "FUTURO",
        "coverage_status": "ASEGURADO_VALIDADO",
    }
    receipt_result = {"eligible": True}
    app._apply_billing_projection_outcome(
        receipt_result,
        allowed_row,
        {"role": app.ROLE_ADMIN},
        {"reason_code": "CURRENT_PENDING_ALLOWED", "can_continue_receipt": True},
    )
    assert receipt_result["reason_code"] == "RECEIPT_PENDING"

    claim_result = {"eligible": True}
    app._apply_billing_projection_outcome(
        claim_result,
        {**allowed_row, "claimed_elsewhere": True},
        {"role": app.ROLE_ADMIN},
        {"reason_code": "CURRENT_PENDING_ALLOWED", "can_continue_receipt": False},
    )
    assert claim_result["reason_code"] == "CLAIMED_OTHER_SESSION"


def test_invalid_attention_and_sql_alias_are_rejected_safely():
    invalid = app.evaluate_attention_billing_eligibility(
        "not-an-id", {"role": app.ROLE_ADMIN}
    )
    assert invalid["reason_code"] == "INVALID_ATTENTION"
    with pytest.raises(ValueError, match="Invalid SQL alias"):
        app.admission_receipt_identity_sql("receipt;DROP", "projection")


@pytest.mark.parametrize(
    ("sqlstate", "error_type", "category"),
    (
        ("08006", RuntimeError, "CONNECTION_ERROR"),
        ("42P01", RuntimeError, "SCHEMA_ERROR"),
        ("42501", RuntimeError, "PERMISSION_ERROR"),
        ("", PermissionError, "PERMISSION_ERROR"),
        ("XX000", RuntimeError, "QUERY_ERROR"),
    ),
)
def test_query_error_categories_cover_operational_failures(
    sqlstate, error_type, category
):
    error = error_type("private raw error")
    error.pgcode = sqlstate
    assert app._classify_billing_admission_error(error) == category


def test_failure_log_tolerates_exception_that_rejects_attributes():
    class ImmutableError(RuntimeError):
        def __setattr__(self, _name, _value):
            raise TypeError("immutable")

    with patch.object(app, "write_main_app_log") as write_log:
        category = app.log_billing_admission_query_failure(
            ImmutableError("private"),
            operation="test",
            sql_stage="QUERY",
        )

    assert category == "QUERY_ERROR"
    write_log.assert_called_once()


def test_central_reader_logs_missing_context_and_query_failure():
    reader = app.CentralAdmissionReader()
    with (
        patch.object(app, "get_central_operational_context", return_value={}),
        patch.object(app, "log_billing_admission_query_failure") as log_failure,
        pytest.raises(app.AdmissionBridgeError),
    ):
        reader.current_operational_context()
    log_failure.assert_called_once()

    class FailingConnection:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, *_args, **_kwargs):
            raise RuntimeError("private query failure")

    with (
        patch.object(app, "db_connect", return_value=FailingConnection()),
        patch.object(app, "log_billing_admission_query_failure") as log_failure,
        pytest.raises(RuntimeError, match="private query failure"),
    ):
        reader.fetch_all(
            "SELECT 1",
            operation="test",
            sql_stage="QUERY",
            statement_timeout_ms=1,
        )
    log_failure.assert_called_once()


def test_projection_mapping_failure_is_logged_without_becoming_an_empty_queue():
    class Reader:
        def current_operational_context(self):
            return CENTRAL_CONTEXT

        def fetch_all(self, *_args, **_kwargs):
            return [object()], {"elapsed_ms": 1.0}

    service = app.BillingAdmissionQueryService(central_reader=Reader())
    with (
        patch.object(app, "log_billing_admission_query_failure") as log_failure,
        pytest.raises(TypeError),
    ):
        service.get_operational_candidates(current_user={"role": app.ROLE_ADMIN})
    assert log_failure.call_args.kwargs["sql_stage"] == "PROJECTION_MAPPING"


def test_bridge_migration_supports_source_frozen_and_missing_paths(
    monkeypatch, tmp_path
):
    class MigrationConnection:
        def __init__(self):
            self.scripts = []

        def execute(self, *_args):
            return _Cursor()

        def executescript(self, script):
            self.scripts.append(script)

    bundle = tmp_path / "bundle"
    migration_dir = bundle / "migrations"
    migration_dir.mkdir(parents=True)
    migration_file = migration_dir / "20260828_billing_admission_bridge_identity.sql"
    migration_file.write_text("SELECT 1;", encoding="utf-8")
    connection = MigrationConnection()
    monkeypatch.setattr(app.sys, "frozen", True, raising=False)
    monkeypatch.setattr(app, "BUNDLE_DIR", str(bundle))
    app._apply_billing_admission_bridge_identity_migration(connection)
    assert connection.scripts == ["SELECT 1;"]

    monkeypatch.setattr(app.sys, "frozen", False, raising=False)
    monkeypatch.setattr(app, "APP_DIR", str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="migración de identidad"):
        app._apply_billing_admission_bridge_identity_migration(connection)


def test_billing_workers_emit_safe_categories_on_failures():
    received = []
    with (
        patch.object(
            app,
            "load_admission_validation_attentions",
            side_effect=RuntimeError("private"),
        ),
        patch.object(
            app,
            "log_billing_admission_query_failure",
            return_value="QUERY_ERROR",
        ),
    ):
        worker = app.AdmissionValidationLoadWorker(
            current_user={"role": app.ROLE_ADMIN}
        )
        worker.failed.connect(received.append)
        worker.run()
    assert received == ["QUERY_ERROR"]

    claim_failures = []
    with (
        patch.object(
            app,
            "claim_projected_billable_attention",
            side_effect=RuntimeError("private"),
        ),
        patch.object(
            app,
            "log_billing_admission_query_failure",
            return_value="QUERY_ERROR",
        ),
    ):
        worker = app.AdmissionValidationClaimWorker(
            1,
            "SOURCE-A",
            username="tester",
            session_id="session",
            current_user={"role": app.ROLE_ADMIN},
        )
        worker.failed.connect(lambda category, _elapsed: claim_failures.append(category))
        worker.run()
    assert claim_failures == ["QUERY_ERROR"]

    history_failures = []
    with (
        patch.object(app, "list_admission_history", side_effect=RuntimeError("private")),
        patch.object(
            app,
            "log_billing_admission_query_failure",
            return_value="QUERY_ERROR",
        ),
    ):
        worker = app.AdmissionHistoryLoadWorker(
            {}, {"role": app.ROLE_ADMIN}, generation=7
        )
        worker.failed.connect(history_failures.append)
        worker.run()
    assert history_failures[0]["message"] == "QUERY_ERROR"

    eligibility_failures = []
    with (
        patch.object(
            app,
            "evaluate_attention_billing_eligibility",
            side_effect=RuntimeError("private"),
        ),
        patch.object(
            app,
            "log_billing_admission_query_failure",
            return_value="QUERY_ERROR",
        ),
    ):
        worker = app.AdmissionHistoryEligibilityWorker(
            {"attention_id": 1}, {"role": app.ROLE_ADMIN}
        )
        worker.failed.connect(
            lambda category, _elapsed: eligibility_failures.append(category)
        )
        worker.run()
    assert eligibility_failures == ["QUERY_ERROR"]
