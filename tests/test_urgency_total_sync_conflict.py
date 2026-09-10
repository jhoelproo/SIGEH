from datetime import datetime
from types import SimpleNamespace

from admission_statistical_reports import (
    ARS_ALL,
    COVERAGE_ALL,
    SPECIALTY_ALL,
    AdmissionReportFilters,
    build_admission_report_dataset,
)
from admission_v15_adapter import _HybridDatabaseProxy
from tests import test_admission_v15_unified_history as fixtures


def _database_with_conflicted_urgency():
    database = fixtures._LocalDatabase()
    database.connection.execute("UPDATE sync_outbox SET sync_status='CONFLICT'")
    original_list = database.listar_atenciones

    def list_with_urgency(*args, **kwargs):
        rows = original_list(*args, **kwargs)
        rows[0]["tipo_atencion"] = "URGENCIA"
        return rows

    database.listar_atenciones = list_with_urgency
    return database


def _runtime(connection_factory):
    return SimpleNamespace(
        offline=False,
        host=SimpleNamespace(connection_factory=connection_factory),
        operational_session=SimpleNamespace(
            turn_id=316,
            operational_source_id="44444444-4444-4444-8444-444444444444",
        ),
    )


def test_turn_summary_includes_local_urgency_with_sync_conflict():
    database = _database_with_conflicted_urgency()
    runtime = _runtime(lambda: fixtures._CloudConnection())

    summary = _HybridDatabaseProxy(database, runtime).refresh_turn_summary()

    assert summary["total"] == 2
    assert summary["GENERAL"] == 1
    assert summary["URGENCIAS"] == 1


def test_sync_conflict_does_not_replace_matching_central_classification():
    class MatchingCentralConnection(fixtures._CloudConnection):
        def execute(self, sql, params=()):
            cursor = super().execute(sql, params)
            cursor.rows[0]["global_attention_id"] = "pending-uuid"
            return cursor

    database = _database_with_conflicted_urgency()
    runtime = _runtime(lambda: MatchingCentralConnection())

    summary = _HybridDatabaseProxy(database, runtime).refresh_turn_summary()

    assert summary["total"] == 1
    assert summary["GENERAL"] == 1
    assert summary["URGENCIAS"] == 0


def test_exact_turn_report_includes_local_urgency_pending_central_sync():
    database = _database_with_conflicted_urgency()
    proxy = _HybridDatabaseProxy(
        database, _runtime(lambda: fixtures._CloudConnection())
    )
    central = fixtures._CloudConnection().execute("", ()).fetchall()
    central[0]["operational_source_id"] = "44444444-4444-4444-8444-444444444444"
    central[0]["turn_id"] = 316

    records = proxy._merge_statistical_report_pending(
        central,
        operational_source_id="44444444-4444-4444-8444-444444444444",
        turn_id=316,
    )

    counts = proxy._calculate_turn_counts(records)
    report = build_admission_report_dataset(
        records,
        AdmissionReportFilters(
            start_at=datetime(2026, 8, 12, 8),
            end_at=datetime(2026, 8, 13, 8),
            period_label="Turno sintético",
            turn_label="Turno actual",
            operational_source_id="44444444-4444-4444-8444-444444444444",
            turn_id=316,
            specialty=SPECIALTY_ALL,
            coverage=COVERAGE_ALL,
            ars_mode=ARS_ALL,
            selected_ars=(),
        ),
    )
    assert counts["total"] == 2
    assert counts["GENERAL"] == 1
    assert counts["URGENCIAS"] == 1
    assert report.summary["total_general"] == 2
    assert report.summary["cantidad_urgencias"] == 1
