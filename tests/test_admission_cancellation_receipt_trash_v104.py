import inspect

import CALCULOS_QT as app


class _MigrationConnection:
    def __init__(self):
        self.executed = []
        self.scripts = []

    def execute(self, sql, params=()):
        self.executed.append((" ".join(str(sql).split()), tuple(params or ())))
        return self

    def executescript(self, sql):
        self.scripts.append(str(sql))
        return self


def test_cancellation_migration_is_idempotent_and_links_by_stable_identity():
    connection = _MigrationConnection()

    app._apply_admission_cancellation_receipt_trash_migration(connection)
    app._apply_admission_cancellation_receipt_trash_migration(connection)

    assert len(connection.scripts) == 2
    assert connection.executed == [
        (
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("admission-cancellation-receipt-trash-v1",),
        ),
        (
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            ("admission-cancellation-receipt-trash-v1",),
        ),
    ]
    sql = connection.scripts[0]
    assert "CREATE OR REPLACE FUNCTION trash_receipt_for_cancelled_admission" in sql
    assert "DROP TRIGGER IF EXISTS trg_admission_cancellation_receipt_trash" in sql
    assert "COALESCE(NEW.is_deleted,FALSE)" in sql
    assert "NEW.source_status" in sql
    assert "admission_atencion_id=NEW.attention_id" in sql
    assert "admission_source_instance_id" in sql
    assert "DELETE FROM admission_billing_claims" in sql
    assert "UPDATE recibos" in sql
    assert "is_deleted=1" in sql
    assert "RECEIPT_TRASHED_BY_ADMISSION_CANCELLATION" in sql


def test_all_billing_admission_entry_points_reject_tombstones():
    candidates = inspect.getsource(
        app.BillingAdmissionQueryService.get_operational_candidates
    )
    history = inspect.getsource(
        app.BillingAdmissionQueryService.load_admission_history_batch
    )
    projected = inspect.getsource(app.get_projected_billable_attention)
    claim = inspect.getsource(app.claim_projected_billable_attention)
    receipt_link = inspect.getsource(app._lock_and_validate_admission_processing)

    for source in (candidates, history, projected, claim, receipt_link):
        assert "COALESCE(p.is_deleted,FALSE)=FALSE" in source


def test_schema_initialization_installs_cancellation_trigger_after_columns():
    source = inspect.getsource(app.db_init)

    billing_columns = source.index("billing_columns =")
    projection_columns = source.index("projection_definitions =")
    trigger_migration = source.index(
        "_apply_admission_data_lifecycle_migrations(con)"
    )
    assert billing_columns < projection_columns < trigger_migration


def test_data_lifecycle_installs_trigger_without_automatic_history_reset(monkeypatch):
    calls = []
    monkeypatch.setattr(
        app,
        "_apply_admission_cancellation_receipt_trash_migration",
        lambda connection: calls.append(("trigger", connection)),
    )
    connection = object()

    app._apply_admission_data_lifecycle_migrations(connection)

    assert calls == [("trigger", connection)]


def test_cancellation_trigger_links_receipts_by_global_attention_id():
    connection = _MigrationConnection()

    app._apply_admission_cancellation_receipt_trash_migration(connection)

    sql = connection.scripts[0]
    assert "admission_global_attention_id" in sql
    assert "NEW.global_attention_id" in sql
