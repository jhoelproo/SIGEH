from types import SimpleNamespace

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
