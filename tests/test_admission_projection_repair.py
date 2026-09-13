import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import CALCULOS_QT as app
from admission_hybrid import (
    AdmissionCloudRepository,
    AdmissionSyncService,
    OfflineAdmissionStore,
    SyncConflict,
)
from admission_v15_adapter import _HybridAdmissionRuntime
from tests.test_admission_sync_architecture import (
    _ProjectionRepairCloud,
    _ProjectionRepairStore,
    _create_attention,
    _database,
    _session,
    _store,
)


class _NoProjectionStore:
    @staticmethod
    def last_cloud_cursor():
        return 0


def test_local_repair_rejects_invalid_uuid_without_touching_outbox(tmp_path):
    path = tmp_path / "invalid-repair.db"
    _database(path)
    store = _store(path, "PC-1")
    _create_attention(path, 1)

    with pytest.raises(ValueError, match="global_attention_id"):
        store.queue_missing_attention_events(global_attention_id="invalid")
    assert store.pending_attention_event("invalid") is None


def test_projection_checks_ignore_invalid_uuid_and_support_legacy_api():
    repository = AdmissionCloudRepository(
        lambda: (_ for _ in ()).throw(AssertionError("database must not open"))
    )
    assert repository.missing_projection_entity_ids(["invalid", ""]) == []

    service = AdmissionSyncService(
        _NoProjectionStore(),
        SimpleNamespace(projection_has_attention=lambda value: value == "present"),
    )
    assert service._missing_projection_ids(["present", "missing"]) == ["missing"]
    without_api = AdmissionSyncService(_NoProjectionStore(), SimpleNamespace())
    assert without_api._missing_projection_ids(["unknown"]) == []
    assert without_api.repair_projection_entities([]) == 0
    assert without_api.ensure_attention_projected("invalid") is False


def test_projection_repository_returns_only_missing_rows():
    missing_uuid = str(uuid.uuid4())
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = [(missing_uuid,)]
    context = Mock()
    context.__enter__ = Mock(return_value=connection)
    context.__exit__ = Mock(return_value=False)
    repository = AdmissionCloudRepository(lambda: context)

    assert repository.missing_projection_entity_ids([missing_uuid, missing_uuid]) == [
        missing_uuid
    ]
    connection.execute.assert_called_once()


def test_recovery_helpers_cover_ordinary_reconciliation_and_store_without_loader():
    entity_uuid = str(uuid.uuid4())
    event_uuid = OfflineAdmissionStore._recovery_event_uuid(
        seed_id="",
        target_id="",
        entity_uuid=entity_uuid,
        version=3,
        operational_session_id="session",
        generation=2,
    )
    assert uuid.UUID(event_uuid)

    connection = Mock()
    assert (
        OfflineAdmissionStore._finalize_recovery_outbox(
            connection,
            seed_id="",
            target_id="",
            entity_uuid=entity_uuid,
            event_uuid=event_uuid,
            inserted=7,
        )
        == 7
    )
    connection.execute.assert_not_called()

    service = AdmissionSyncService(_NoProjectionStore(), SimpleNamespace())
    assert service.repair_recent_projections_once() == 0
    assert service._recent_projection_repair_complete is True


def test_unrecoverable_projection_fails_explicitly_and_blocks_cancellation():
    entity_uuid = str(uuid.uuid4())
    store = _ProjectionRepairStore(entity_uuid, None)
    cloud = _ProjectionRepairCloud(entity_uuid, has_event=False)
    service = AdmissionSyncService(store, cloud)

    with pytest.raises(SyncConflict, match="proyección central"):
        service.repair_projection_entities([entity_uuid])
    assert (
        service.cancel_attention(
            "invalid",
            current_user={"role": "administrador"},
            reason="Registro duplicado",
            operational_session=_session(),
            device_id="PC-1",
            online=True,
        )
        is None
    )


def test_v15_runtime_repairs_only_while_central_service_is_available():
    runtime = _HybridAdmissionRuntime.__new__(_HybridAdmissionRuntime)
    runtime.offline = True
    runtime.sync_service = Mock()
    assert runtime.repair_admission_attention_projection(str(uuid.uuid4())) is False
    runtime.sync_service.ensure_attention_projected.assert_not_called()

    runtime.offline = False
    runtime.sync_service = None
    assert runtime.repair_admission_attention_projection(str(uuid.uuid4())) is False

    repair = Mock(return_value=True)
    runtime.sync_service = SimpleNamespace(ensure_attention_projected=repair)
    entity_uuid = str(uuid.uuid4())
    assert runtime.repair_admission_attention_projection(entity_uuid) is True
    repair.assert_called_once_with(entity_uuid)


class _Signal:
    def connect(self, callback):
        self.callback = callback


class _EligibilityWorker:
    created = None

    def __init__(self, *args, **kwargs):
        type(self).created = (args, kwargs)
        self.resolved = _Signal()
        self.failed = _Signal()
        self.finished = _Signal()

    def deleteLater(self):
        return None

    def start(self):
        self.started = True


def test_history_handoff_passes_integrated_projection_repair(monkeypatch):
    repair = Mock(return_value=True)
    runtime = SimpleNamespace(repair_admission_attention_projection=repair)
    window = SimpleNamespace(
        current_user={"role": app.ROLE_ADMIN},
        _validation_claim_worker=None,
        btn_validate_admission=Mock(),
        session_id="billing-session",
        emergency_workspace=SimpleNamespace(
            full_page=SimpleNamespace(_hybrid_runtime=runtime)
        ),
        _on_history_billing_resolved=Mock(),
        _on_admission_claim_failed=Mock(),
    )
    monkeypatch.setattr(app, "can_access_billing_admission_history", lambda _user: True)
    monkeypatch.setattr(app, "AdmissionHistoryEligibilityWorker", _EligibilityWorker)

    identity = {"global_attention_id": str(uuid.uuid4()), "attention_id": 468}
    app.MainWindow._send_emergency_history_to_billing(window, identity)

    args, kwargs = _EligibilityWorker.created
    assert args[:3] == (identity, window.current_user, "billing-session")
    assert kwargs["projection_repair"] == repair
    window.btn_validate_admission.setEnabled.assert_called_once_with(False)


def test_history_handoff_keeps_role_guard(monkeypatch):
    warning = Mock()
    monkeypatch.setattr(
        app, "can_access_billing_admission_history", lambda _user: False
    )
    monkeypatch.setattr(app.QMessageBox, "warning", warning)
    window = SimpleNamespace(current_user={"role": "consulta"})

    app.MainWindow._send_emergency_history_to_billing(window, {})

    warning.assert_called_once()
