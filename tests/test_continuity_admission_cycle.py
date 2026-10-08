from contextlib import closing
import sqlite3
from unittest.mock import patch

import pytest

from admission_hybrid import AdmissionSyncService
from tests.test_admission_sync_architecture import (
    _MemoryCloud,
    _create_attention,
    _database,
    _store,
)


@pytest.mark.parametrize("failure", ["before_upload", "after_central_commit"])
def test_offline_reopen_and_lost_ack_replay_do_not_duplicate(tmp_path, failure):
    local = tmp_path / "station1.db"
    replica = tmp_path / "station2.db"
    for path in (local, replica):
        _database(path)
    cloud = _MemoryCloud()
    store = _store(local, "PC-1")
    _create_attention(local, 1)
    event_id = store.pending_events()[0].event_uuid
    original = cloud.push_events

    def failed_upload(events):
        if failure == "after_central_commit":
            original(events)
        raise ConnectionError("server closed the connection")

    with patch.object(cloud, "push_events", side_effect=failed_upload):
        result = AdmissionSyncService(store, cloud).push_outbox()
    assert result["retry"] == 1
    assert store.pending_count() == 1
    reopened = _store(local, "PC-1")
    assert reopened.pending_events()[0].event_uuid == event_id
    service = AdmissionSyncService(reopened, cloud)
    service.synchronize_once()
    service.synchronize_once()
    assert reopened.pending_count() == 0
    assert len(cloud.events) == 1
    remote_store = _store(replica, "PC-2")
    remote_service = AdmissionSyncService(remote_store, cloud)
    remote_service.synchronize_once()
    remote_service.synchronize_once()
    with closing(sqlite3.connect(replica)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] == 1
    assert remote_store.pending_count() == 0
