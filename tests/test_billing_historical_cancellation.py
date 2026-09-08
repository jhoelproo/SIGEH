"""Administrative cancellation against an isolated PostgreSQL, plus Qt permissions."""

from types import SimpleNamespace

import pytest
import psycopg2

import CALCULOS_QT as app
import billing_historical_cancellation as cancellation
from tests.test_billing_consistency_postgres import (
    GLOBAL,
    database as database,
    server as server,
)


@pytest.fixture
def runtime(database):
    with database() as con:
        con.execute(
            "ALTER TABLE admission_shift_inheritances ADD COLUMN receipt_id BIGINT"
        )
        con.execute("UPDATE admission_attention_projection SET turn_id=3948")
        con.execute(
            "INSERT INTO admission_shift_inheritances VALUES('ORIGIN',329,3948,'PENDIENTE',NULL)"
        )
    return SimpleNamespace(
        current_user={"role": "administrador", "username": "admin.test", "id": 7},
        offline=False,
        host=SimpleNamespace(connection_factory=database),
        operational_session=SimpleNamespace(operational_session_id="test"),
        device_id="TEST",
    )


def install_canonical_fake(monkeypatch, *, fail=False, confirmed=True):
    calls = []

    def cancel(repository, global_id, **kwargs):
        calls.append((global_id, kwargs))
        with repository.connection_factory() as con:
            con.execute(
                "UPDATE admission_attention_projection SET is_deleted=TRUE,source_status='ANULADA' WHERE global_attention_id=%s",
                (global_id,),
            )
        if fail:
            raise RuntimeError("synthetic event failure")
        return {"is_deleted": confirmed, "global_attention_id": global_id}

    monkeypatch.setattr(
        cancellation.AdmissionCloudRepository, "cancel_attention", cancel
    )
    return calls


def test_cancellation_uses_canonical_service_and_preserves_identity(
    runtime, database, monkeypatch
):
    calls = install_canonical_fake(monkeypatch)
    result = cancellation.cancel_pending_inheritance(
        runtime, GLOBAL, "Registro duplicado"
    )
    assert result["is_deleted"]
    assert calls[0][1]["current_user"] == runtime.current_user
    assert calls[0][1]["reason"] == "Registro duplicado"
    with database() as con:
        row = con.execute("SELECT * FROM admission_attention_projection").fetchone()
        assert row["attention_id"] == 329
        assert str(row["global_attention_id"]) == GLOBAL
        assert row["source_status"] == "ANULADA"
    with pytest.raises(ValueError, match="ya está anulada"):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, "Registro duplicado")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "sql,message",
    [
        ("UPDATE admission_attention_projection SET turn_id=3949", "turnos anteriores"),
        (
            "UPDATE admission_attention_projection SET operational_source_id='99999999-9999-4999-8999-999999999999'",
            "otro origen",
        ),
        ("DELETE FROM admission_attention_projection", "no está disponible"),
        (
            "UPDATE admission_attention_projection SET is_deleted=TRUE",
            "ya está anulada",
        ),
        (
            "UPDATE admission_attention_projection SET source_status='ANULADA'",
            "ya está anulada",
        ),
        ("DELETE FROM admission_shift_inheritances", "no es una heredada"),
        (
            "INSERT INTO recibos(id,admission_atencion_id,admission_source_instance_id,is_deleted) VALUES(1,329,'ORIGIN',1)",
            "facturación vinculada",
        ),
        (
            f"INSERT INTO recibos(id,admission_global_attention_id) VALUES(1,'{GLOBAL}')",
            "facturación vinculada",
        ),
        (
            "INSERT INTO admission_billing_claims(source_instance_id,attention_id,expires_at) VALUES('ORIGIN',329,NOW()+INTERVAL '1 hour')",
            "reservada",
        ),
        (
            "INSERT INTO admission_billing_claims(source_instance_id,attention_id,receipt_id) VALUES('ORIGIN',329,88)",
            "reservada",
        ),
    ],
)
def test_ineligible_attention_never_calls_cancellation(
    runtime, database, monkeypatch, sql, message
):
    calls = install_canonical_fake(monkeypatch)
    with database() as con:
        con.execute(sql)
    with pytest.raises(ValueError, match=message):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, "Registro duplicado")
    assert calls == []


@pytest.mark.parametrize(
    "role",
    ["auxiliar", "facturador de auditoria", "auditoria medica y cuentas", "", None],
)
def test_only_administrator_before_database_access(role):
    runtime = SimpleNamespace(current_user={"role": role})
    with pytest.raises(PermissionError):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, "Registro duplicado")


@pytest.mark.parametrize("reason", [None, "", "1234567", "        "])
def test_reason_boundary(reason):
    runtime = SimpleNamespace(current_user={"role": "admin"})
    with pytest.raises(ValueError, match="8 caracteres"):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, reason)


@pytest.mark.parametrize("offline,session", [(True, object()), (False, None)])
def test_offline_never_enqueues(offline, session):
    runtime = SimpleNamespace(
        current_user={"role": "admin"}, offline=offline, operational_session=session
    )
    with pytest.raises(cancellation.AdmissionWriteBlocked):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, "12345678")


@pytest.mark.parametrize("fail,confirmed", [(True, True), (False, False)])
def test_atomic_rollback(runtime, database, monkeypatch, fail, confirmed):
    install_canonical_fake(monkeypatch, fail=fail, confirmed=confirmed)
    with pytest.raises((RuntimeError, cancellation.AdmissionWriteBlocked)):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, "12345678")
    with database() as con:
        assert (
            con.execute(
                "SELECT is_deleted FROM admission_attention_projection"
            ).fetchone()["is_deleted"]
            is False
        )


def test_simultaneous_billing_write_fails_without_waiting(
    runtime, database, monkeypatch
):
    calls = install_canonical_fake(monkeypatch)
    with database() as billing:
        billing.execute(
            "INSERT INTO admission_billing_claims(source_instance_id,attention_id) VALUES('OTHER',1)"
        )
        with pytest.raises(psycopg2.errors.LockNotAvailable):
            cancellation.cancel_pending_inheritance(runtime, GLOBAL, "12345678")
    assert calls == []


def test_expired_reservation_does_not_block(runtime, database, monkeypatch):
    install_canonical_fake(monkeypatch)
    with database() as con:
        con.execute(
            "INSERT INTO admission_billing_claims(source_instance_id,attention_id,expires_at) VALUES('ORIGIN',329,NOW()-INTERVAL '1 hour')"
        )
    assert cancellation.cancel_pending_inheritance(runtime, GLOBAL, "12345678")[
        "is_deleted"
    ]


def test_changed_operational_session_fails_closed(runtime, database, monkeypatch):
    calls = install_canonical_fake(monkeypatch)
    with database() as con:
        con.execute("UPDATE admission_operational_sessions SET status='CLOSED'")
    with pytest.raises(cancellation.AdmissionWriteBlocked):
        cancellation.cancel_pending_inheritance(runtime, GLOBAL, "12345678")
    assert calls == []


@pytest.mark.parametrize(
    "change,expected",
    [
        ("source_status='ACTIVA'", 1),
        ("source_status='ANULADA'", 0),
        ("is_deleted=TRUE", 0),
        ("service_type='URGENCIA'", 0),
        ("service_type='CONSULTA'", 0),
    ],
)
def test_annulled_and_reclassified_rows_do_not_carry_to_next_shift(
    runtime, database, change, expected
):
    clause = cancellation.inherited_attention_is_active_sql(
        "i.source_instance_id", "i.attention_id"
    )
    with database() as con:
        con.execute(f"UPDATE admission_attention_projection SET {change}")
        row = con.execute(
            f"SELECT COUNT(*) AS count FROM admission_shift_inheritances i WHERE {clause}"
        ).fetchone()
        assert row["count"] == expected


@pytest.mark.parametrize(
    "role,visible",
    [(app.ROLE_ADMIN, True), (app.ROLE_AUDIT, False), (app.ROLE_AUX, False)],
)
@pytest.mark.parametrize(
    "dialog_class", [app.AdmissionHistoryDialog, app.AdmissionValidationDialog]
)
def test_action_visibility_in_both_billing_dialogs(
    monkeypatch, role, visible, dialog_class
):
    qt = app.QApplication.instance() or app.QApplication([])
    monkeypatch.setattr(app.AdmissionHistoryDialog, "search", lambda self: None)
    monkeypatch.setattr(
        app.AdmissionValidationDialog, "refresh", lambda self, *args: None
    )
    dialog = dialog_class(current_user={"role": role, "username": "tester"})
    try:
        assert dialog.cancel_inherited_button.isHidden() is not visible
        dialog._cancellation_worker = object()
        dialog.done(1)
        assert dialog.result() == 0
        from PySide6.QtGui import QCloseEvent

        event = QCloseEvent()
        dialog.closeEvent(event)
        assert not event.isAccepted()
    finally:
        dialog._cancellation_worker = None
        dialog.close()
        qt.processEvents()


@pytest.mark.parametrize("outcome", ["success", "replica_failure", "central_failure"])
def test_worker_hydrates_only_committed_cancellations(monkeypatch, outcome):
    qt = app.QApplication.instance() or app.QApplication([])
    hydrated, completed, failed = [], [], []
    event = {"event_uuid": GLOBAL, "operation": "DELETE"}

    def central(*_args):
        if outcome == "central_failure":
            raise ValueError("tiene recibo")
        return {"is_deleted": True, "event": event}

    def hydrate(events):
        if outcome == "replica_failure":
            raise OSError("replica busy")
        hydrated.extend(events)

    monkeypatch.setattr(cancellation, "cancel_pending_inheritance", central)
    runtime = SimpleNamespace(store=SimpleNamespace(hydrate_remote_events=hydrate))
    worker = app.HistoricalAdmissionCancellationWorker(runtime, GLOBAL, "Duplicado")
    worker.completed.connect(completed.append)
    worker.failed.connect(failed.append)
    worker.run()
    qt.processEvents()
    if outcome == "central_failure":
        assert failed == ["tiene recibo"]
        assert completed == hydrated == []
    else:
        assert failed == []
        assert completed[0]["is_deleted"]
        assert bool(completed[0].get("replica_refresh_pending")) == (
            outcome == "replica_failure"
        )
        assert hydrated == ([event] if outcome == "success" else [])


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "cancel_reason",
        "short_reason",
        "decline",
        "missing_runtime",
        "missing_identity",
        "busy",
        "not_admin",
        "no_selection",
    ],
)
def test_admin_action_dialog_flow(monkeypatch, scenario):
    qt = app.QApplication.instance() or app.QApplication([])
    owner = app.QWidget()
    event_bus = app.AdmissionV15EventBus(owner)
    runtime = SimpleNamespace(store=None)
    owner.emergency_workspace = SimpleNamespace(
        full_page=SimpleNamespace(_hybrid_runtime=runtime),
        admission_context=SimpleNamespace(event_bus=event_bus),
    )
    dialog = app.QDialog(owner)
    dialog.current_user = {"role": app.ROLE_ADMIN}
    messages, refreshed, history = [], [], []
    event_bus.history_refresh_requested.connect(lambda: history.append(True))
    monkeypatch.setattr(
        app.QInputDialog,
        "getText",
        lambda *args: (
            "x" if scenario == "short_reason" else "Registro duplicado",
            scenario != "cancel_reason",
        ),
    )
    monkeypatch.setattr(
        app.QMessageBox,
        "question",
        lambda *args: (
            app.QMessageBox.No if scenario == "decline" else app.QMessageBox.Yes
        ),
    )
    monkeypatch.setattr(
        app.QMessageBox, "warning", lambda *args: messages.append(args[-1])
    )
    monkeypatch.setattr(
        app.QMessageBox, "information", lambda *args: messages.append(args[-1])
    )
    monkeypatch.setattr(app, "invalidate_admission_validation_cache", lambda: None)
    monkeypatch.setattr(
        cancellation, "cancel_pending_inheritance", lambda *args: {"is_deleted": True}
    )

    def start(worker):
        worker.run()
        worker.finished.emit()

    monkeypatch.setattr(app.HistoricalAdmissionCancellationWorker, "start", start)
    attention = {"global_attention_id": GLOBAL}
    if scenario == "missing_runtime":
        owner.emergency_workspace.full_page._hybrid_runtime = None
    if scenario == "missing_identity":
        attention = {}
    if scenario == "busy":
        dialog._cancellation_worker = object()
    if scenario == "not_admin":
        dialog.current_user["role"] = app.ROLE_AUDIT
    if scenario == "no_selection":
        attention = None
    try:
        app._cancel_historical_admission_from_dialog(
            dialog, attention, lambda: refreshed.append(True)
        )
        assert bool(refreshed) == (scenario == "success")
        assert history == refreshed
        if scenario == "success":
            assert messages == ["Atención anulada y retirada de pendientes."]
    finally:
        dialog.close()
        owner.close()
        qt.processEvents()
