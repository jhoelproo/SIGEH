import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

from admission_hybrid import (
    ADMISSION_HISTORY_RESET_VERSION,
    OfflineAdmissionStore,
)
from admission_v15_adapter import _HybridAdmissionRuntime


def _database():
    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE app_metadata(clave TEXT PRIMARY KEY,valor TEXT NOT NULL);
        CREATE TABLE atenciones(
            id INTEGER PRIMARY KEY,estado TEXT,is_deleted INTEGER DEFAULT 0,
            deleted_at TEXT,deleted_by_user_id TEXT,delete_reason TEXT,
            anulada_at TEXT,anulada_por TEXT,anulada_motivo TEXT,
            sync_state TEXT DEFAULT 'PENDING'
        );
        CREATE TABLE sync_outbox(
            event_uuid TEXT PRIMARY KEY,entity_type TEXT,sync_status TEXT,last_error TEXT
        );
        CREATE TABLE trigger_calls(value TEXT);
        CREATE TRIGGER trg_admission_sync_attention_update
        AFTER UPDATE ON atenciones BEGIN
            INSERT INTO trigger_calls(value) VALUES('unexpected');
        END;
        INSERT INTO atenciones(id,estado) VALUES(1,'ACTIVA'),(2,'ACTIVA');
        INSERT INTO sync_outbox VALUES('e1','attention','PENDING',NULL);
        INSERT INTO sync_outbox VALUES('p1','patient','PENDING',NULL);
        """
    )
    return connection


def test_history_reset_tombstones_local_attention_without_creating_outbox():
    connection = _database()

    reset_count = OfflineAdmissionStore.apply_authorized_history_reset(connection)

    assert reset_count == 2
    assert connection.execute(
        "SELECT COUNT(*) FROM atenciones WHERE is_deleted=0"
    ).fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM trigger_calls").fetchone()[0] == 0
    assert connection.execute(
        "SELECT sync_status FROM sync_outbox WHERE event_uuid='e1'"
    ).fetchone()[0] == "SUPERSEDED"
    assert connection.execute(
        "SELECT sync_status FROM sync_outbox WHERE event_uuid='p1'"
    ).fetchone()[0] == "PENDING"


def test_history_reset_is_idempotent_and_keeps_future_attentions():
    connection = _database()
    assert OfflineAdmissionStore.apply_authorized_history_reset(connection) == 2
    connection.execute(
        "INSERT INTO atenciones(id,estado,is_deleted) VALUES(3,'ACTIVA',0)"
    )

    assert OfflineAdmissionStore.apply_authorized_history_reset(connection) == 0
    assert connection.execute(
        "SELECT is_deleted FROM atenciones WHERE id=3"
    ).fetchone()[0] == 0
    assert connection.execute(
        "SELECT valor FROM app_metadata WHERE clave=?",
        ("sigeh.admission_history_reset_version",),
    ).fetchone()[0] == ADMISSION_HISTORY_RESET_VERSION


class _CentralConnection:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error

    def execute(self, _sql, _params=()):
        if self.error:
            raise self.error
        return self

    def fetchone(self):
        return self.row


class _LocalStore:
    def __init__(self, reset_count=0):
        self.reset_count = reset_count
        self.installed = 0

    @contextmanager
    def connection(self):
        yield object()

    def apply_authorized_history_reset(self, _connection):
        return self.reset_count

    def _install_attention_outbox_triggers(self, _connection):
        self.installed += 1


def _runtime_for_reset(row=None, error=None, reset_count=0):
    central = _CentralConnection(row=row, error=error)
    return SimpleNamespace(
        store=_LocalStore(reset_count),
        host=SimpleNamespace(connection_factory=lambda: _connection_context(central)),
        logger=SimpleNamespace(debug=lambda *args: None, warning=lambda *args: None),
    )


@contextmanager
def _connection_context(connection):
    yield connection


def test_runtime_applies_local_reset_only_when_central_marker_exists():
    runtime = _runtime_for_reset(row=(1,), reset_count=3)
    assert _HybridAdmissionRuntime._apply_central_history_reset_if_authorized(runtime) == 3
    assert runtime.store.installed == 1


def test_runtime_does_not_reset_without_marker_or_when_central_is_unavailable():
    missing = _runtime_for_reset(row=None, reset_count=3)
    assert _HybridAdmissionRuntime._apply_central_history_reset_if_authorized(missing) == 0
    assert missing.store.installed == 0

    unavailable = _runtime_for_reset(error=RuntimeError("offline"), reset_count=3)
    assert _HybridAdmissionRuntime._apply_central_history_reset_if_authorized(unavailable) == 0
    assert unavailable.store.installed == 0

    no_store = _runtime_for_reset(row=(1,), reset_count=3)
    no_store.store = None
    assert _HybridAdmissionRuntime._apply_central_history_reset_if_authorized(no_store) == 0
