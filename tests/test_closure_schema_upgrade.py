from unittest.mock import Mock, MagicMock

import CALCULOS_QT as app
import pytest
import psycopg2
from datetime import datetime, timezone
from uuid import uuid4
from billing_closure_recovery import (
    closure_from_interval,
    install_closure_dispatch_schema,
)
from tests import test_integral_emergency_to_monthly_list as integration


def test_category_upgrade_preserves_old_snapshots_and_accepts_new_categories():
    from billing_closure_categories import install_closure_categories

    fixture = integration.IntegralEmergencyToMonthlyListTests(
        "test_emergency_to_audit_to_monthly_ars_list"
    )
    fixture.setUp()
    try:
        with app.db_connect() as con:
            con.execute(
                "ALTER TABLE billing_shift_closures DROP COLUMN classification_version"
            )
            con.execute(
                "ALTER TABLE billing_shift_closure_details DROP CONSTRAINT billing_shift_closure_details_classification_check"
            )
            con.execute("""ALTER TABLE billing_shift_closure_details ADD CONSTRAINT billing_shift_closure_details_classification_check
                CHECK(classification IN ('AUTORIZADA','PENDIENTE DE AUTORIZACIÓN','HEREDADA AUTORIZADA','HEREDADA PENDIENTE'))""")
            before = con.execute(
                "SELECT enabled_at FROM billing_closure_dispatch_policy"
            ).fetchone()[0]
            install_closure_categories(con)
            install_closure_categories(con)
            after = con.execute(
                "SELECT enabled_at FROM billing_closure_dispatch_policy"
            ).fetchone()[0]
            constraint = con.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid='billing_shift_closure_details'::regclass
                AND conname='billing_shift_closure_details_classification_check'""").fetchone()[
                0
            ]
            assert "HISTÓRICA AUTORIZADA" in constraint
            assert "HISTÓRICA PENDIENTE" in constraint
            assert before == after
    finally:
        fixture.tearDown()


def test_existing_install_requires_closure_identity_column():
    rows = [
        (table, column)
        for table, columns in app._BOOTSTRAP_REQUIRED_SCHEMA.items()
        for column in columns
        if (table, column) != ("billing_shift_closure_details", "global_attention_id")
    ]
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = rows
    compatible, missing = app.inspect_database_schema_compatibility(connection)
    assert not compatible
    assert "column:billing_shift_closure_details.global_attention_id" in missing


@pytest.mark.parametrize(
    "missing,expected_sql,full_migration",
    [
        (
            ["column:billing_shift_closures.classification_version"],
            "ADD CONSTRAINT billing_shift_closure_details_classification_check",
            False,
        ),
        (
            [
                "column:billing_shift_closure_details.global_attention_id",
                "table:billing_closure_dispatch_policy",
            ],
            "ON CONFLICT(singleton) DO NOTHING",
            False,
        ),
        (
            ["column:admission_operational_sessions.operational_revision"],
            "ADD COLUMN IF NOT EXISTS operational_revision",
            False,
        ),
        (
            ["column:admission_operational_sessions.turn_code"],
            "ADD COLUMN IF NOT EXISTS turn_code",
            False,
        ),
        (["table:users"], "", True),
        ([], "", True),
    ],
)
def test_startup_selects_targeted_migration_for_schema_change(
    monkeypatch,
    missing,
    expected_sql,
    full_migration,
):
    connection = MagicMock()
    connection.__enter__.return_value = connection
    monkeypatch.setattr(app, "db_connect", lambda: connection)
    monkeypatch.setattr(app, "ensure_sigeh_production_schema", Mock())
    monkeypatch.setattr(
        app, "_read_database_schema_state", Mock(return_value=(True, False, missing))
    )
    monkeypatch.setattr(
        app, "inspect_database_schema_compatibility", Mock(return_value=(True, []))
    )
    monkeypatch.setattr(
        app, "validate_database_url", lambda value: "postgresql://synthetic/db"
    )
    monkeypatch.setattr(app.psycopg2, "connect", Mock(return_value=MagicMock()))
    monkeypatch.setattr(app, "write_main_app_log", Mock())
    migration = Mock()
    monkeypatch.setattr(app, "db_init", migration)
    assert app.prepare_database_schema() == "MIGRATED"
    assert migration.call_count == int(full_migration)
    statements = [call.args[0] for call in connection.execute.call_args_list]
    if expected_sql:
        assert any(expected_sql in sql for sql in statements)


@pytest.mark.parametrize(
    "authorization,expected", [(None, 1), ("", 1), ("AUTH-12345", 0)]
)
def test_inherited_records_exclude_authorizations_already_present_at_start(
    monkeypatch, authorization, expected
):
    record = dict(source_instance_id="origin", attention_id=1)
    receipt = dict(
        source_id="origin", admission_atencion_id=1, numero_autorizacion=authorization
    )
    query = Mock(return_value=[receipt])
    monkeypatch.setattr(app, "_receipt_candidates_for_shift", query)
    assert (
        len(app._pending_records_at_start(None, [record], "2026-09-15 08:00:00"))
        == expected
    )
    query.assert_called_once_with(None, [record], "2026-09-15 08:00:00")


def test_legacy_schema_snapshot_failure_is_repaired_without_restarting_policy(
    monkeypatch, tmp_path
):
    fixture = integration.IntegralEmergencyToMonthlyListTests(
        "test_emergency_to_audit_to_monthly_ars_list"
    )
    fixture.setUp()
    try:
        with app.db_connect() as con:
            con.execute(
                "ALTER TABLE billing_shift_closure_details DROP COLUMN global_attention_id"
            )
        event = closure_from_interval(
            dict(
                operational_source_id=str(uuid4()),
                operational_session_id=str(uuid4()),
                turn_id=5,
                started_at=datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
                ended_at=datetime(2026, 9, 16, 12, tzinfo=timezone.utc),
            )
        )
        records = [
            dict(
                attention_id=1,
                turn_id=5,
                canonical_ars="HUMANO",
                attention_type="EMERGENCIA",
                global_attention_id=str(uuid4()),
            )
        ]
        with pytest.raises(psycopg2.errors.UndefinedColumn):
            app.capture_shift_closure_snapshot(event, records)
        assert app.get_shift_closure(event.source_instance_id, event.turn_id) is None
        with app.db_connect() as con:
            before = con.execute(
                "SELECT enabled_at FROM billing_closure_dispatch_policy"
            ).fetchone()[0]
            install_closure_dispatch_schema(con)
            install_closure_dispatch_schema(con)
            after = con.execute(
                "SELECT enabled_at FROM billing_closure_dispatch_policy"
            ).fetchone()[0]
        assert before == after
        snapshot = app.capture_shift_closure_snapshot(event, records)
        assert snapshot["eligible_current"] == 1
        assert snapshot["pending_next"] == 1
        import report_documents
        from pathlib import Path

        monkeypatch.setattr(report_documents, "report_cache_root", lambda: tmp_path)
        application = app.QApplication.instance() or app.QApplication([])
        opened_and_printed = Mock()
        completed, failed = Mock(), Mock()
        worker = app.ShiftClosureReportWorker(
            event.source_instance_id, event.turn_id, "SYNTHETIC", opened_and_printed
        )
        worker.completed.connect(completed)
        worker.failed.connect(failed)
        worker.run()
        failed.assert_not_called()
        completed.assert_called_once()
        assert completed.call_args.args[0]["status"] == "GENERATED"
        opened_and_printed.assert_called_once()
        path = Path(opened_and_printed.call_args.args[1])
        assert path.read_bytes().startswith(b"%PDF-")
        saved = app.get_shift_closure(event.source_instance_id, event.turn_id)
        assert saved["pdf_status"] == "GENERADO"
        assert saved["report_filename"]
        reopened = app.resolve_report_document(
            "billing_shift_closures", f"{event.source_instance_id}|{event.turn_id}"
        )
        assert Path(reopened).is_file()
        application.processEvents()
    finally:
        fixture.tearDown()
