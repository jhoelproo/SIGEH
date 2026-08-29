from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from admission_v15_adapter import (
    TurnDatasetResult,
    TurnDatasetStateError,
    _HybridDatabaseProxy,
    _V15BackgroundRefreshCoordinator,
)


SOURCE = "stable-source"
TURN = 3944


def _row(attention_id: int, *, ars: str = "HUMANO") -> dict[str, object]:
    return {
        "id": attention_id,
        "attention_id": attention_id,
        "global_attention_id": f"00000000-0000-4000-8000-{attention_id:012d}",
        "tipo_atencion": "EMERGENCIA",
        "hoja_normalizada": "GENERAL",
        "ars_display": ars,
        "created_at_effective_utc": "2026-08-29T12:00:00+00:00",
        "origin_device_id": "PC-TEST",
        "device_local_sequence": attention_id,
    }


class _Cursor:
    def __init__(self, rows=(), single=None):
        self.rows = list(rows)
        self.single = single

    def fetchall(self):
        return list(self.rows)

    def fetchone(self):
        return self.single


class _CentralState:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.emergency_count = len(self.rows)
        self.failure: Exception | None = None
        self.statements: list[str] = []

    def connection(self):
        return _CentralConnection(self)


class _CentralConnection:
    def __init__(self, state: _CentralState):
        self.state = state

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, _params=()):
        self.state.statements.append(" ".join(str(sql).split()))
        if self.state.failure is not None:
            raise self.state.failure
        if "COUNT(*) FILTER" in sql:
            return _Cursor(single={"emergency_count": self.state.emergency_count})
        return _Cursor(rows=self.state.rows)


def _proxy(rows=()):
    central = _CentralState(rows)
    local = {"rows": [], "pending": [], "deleted": set(), "error": ""}
    runtime = SimpleNamespace(
        offline=False,
        logger=logging.getLogger("test.turn-summary-v108"),
        device_id="PC-TEST",
        host=SimpleNamespace(connection_factory=central.connection),
        operational_session=SimpleNamespace(
            operational_source_id=SOURCE,
            turn_id=TURN,
            generation=17,
            operational_revision=31,
        ),
    )
    proxy = _HybridDatabaseProxy(SimpleNamespace(), runtime)
    object.__setattr__(
        proxy,
        "_load_local_turn_evidence",
        lambda _identity: (
            list(local["rows"]),
            list(local["pending"]),
            set(local["deleted"]),
            str(local["error"]),
        ),
    )
    return proxy, runtime, central, local


def test_100_repeated_refreshes_keep_same_count_and_execute_selects_only():
    proxy, _runtime, central, _local = _proxy([_row(index) for index in range(1, 21)])

    totals = [
        proxy.refresh_turn_summary(reason="stress_refresh")["total"] for _ in range(101)
    ]

    assert totals == [20] * 101
    assert all(statement.startswith("SELECT ") for statement in central.statements)


def test_online_central_twenty_wins_over_temporarily_empty_sqlite():
    proxy, _runtime, _central, local = _proxy([_row(index) for index in range(1, 21)])
    local["rows"] = []

    summary = proxy.refresh_turn_summary(reason="sqlite_hydrating")

    assert summary["total"] == 20
    assert summary["_fuente"] == "CENTRAL"
    assert summary["_local_count"] == 0


def test_missing_identity_and_query_failure_preserve_last_known_good_twenty():
    proxy, runtime, central, _local = _proxy([_row(index) for index in range(1, 21)])
    assert proxy.refresh_turn_summary()["total"] == 20

    runtime.operational_session = None
    missing = proxy.refresh_turn_summary(reason="missing_identity")
    assert missing["total"] == 20
    assert missing["_status"] == "STALE"
    assert missing["_error_code"] == "IDENTITY_UNAVAILABLE"

    runtime.operational_session = SimpleNamespace(
        operational_source_id=SOURCE,
        turn_id=TURN,
        generation=17,
        operational_revision=31,
    )
    central.failure = ConnectionError("network unavailable")
    failed = proxy.refresh_turn_summary(reason="network_error")
    assert failed["total"] == 20
    assert failed["_fuente"] in {"OFFLINE_LOCAL", "LAST_KNOWN_GOOD"}


def test_real_empty_turn_and_confirmed_handoff_can_show_zero():
    empty_proxy, _runtime, _central, _local = _proxy([])
    empty = empty_proxy.refresh_turn_summary(reason="first_valid_query")
    assert empty["total"] == 0
    assert empty["_status"] == "VALID_EMPTY"

    proxy, runtime, central, _local = _proxy([_row(index) for index in range(1, 21)])
    assert proxy.refresh_turn_summary()["total"] == 20
    runtime.operational_session = SimpleNamespace(
        operational_source_id=SOURCE,
        turn_id=TURN + 1,
        generation=18,
        operational_revision=32,
    )
    central.rows = []
    central.emergency_count = 0
    new_turn = proxy.refresh_turn_summary(reason="explicit_handoff")
    assert new_turn["_turn_id"] == TURN + 1
    assert new_turn["total"] == 0
    assert new_turn["_status"] == "VALID_EMPTY"


def test_zero_guard_rejects_false_zero_then_accepts_one_confirmed_recheck():
    proxy, _runtime, central, _local = _proxy([_row(index) for index in range(1, 21)])
    assert proxy.refresh_turn_summary()["total"] == 20

    central.rows = []
    central.emergency_count = 20
    rejected = proxy.refresh_turn_summary(reason="suspected_reset")
    assert rejected["total"] == 20
    assert rejected["_status"] == "STALE"
    assert rejected["_error_code"] == "ZERO_RECHECK_NONZERO"

    central.emergency_count = 0
    confirmed = proxy.refresh_turn_summary(reason="confirmed_voids")
    assert confirmed["total"] == 0
    assert confirmed["_status"] == "VALID_EMPTY"
    assert sum("COUNT(*) FILTER" in statement for statement in central.statements) == 2


def test_offline_create_and_void_change_count_without_intermediate_zero():
    proxy, runtime, _central, local = _proxy([_row(index) for index in range(1, 21)])
    totals = [proxy.refresh_turn_summary()["total"]]
    runtime.offline = True

    local["rows"] = [_row(21)]
    local["pending"] = [_row(21)]
    totals.append(proxy.refresh_turn_summary(reason="attention_created")["total"])

    local["rows"] = []
    local["pending"] = []
    local["deleted"] = {str(_row(21)["global_attention_id"]).replace("-", "").lower()}
    totals.append(proxy.refresh_turn_summary(reason="attention_voided")["total"])

    assert totals == [20, 21, 20]


def test_online_offline_online_reconciliation_never_flashes_zero():
    central_rows = [_row(index) for index in range(1, 21)]
    proxy, runtime, central, local = _proxy(central_rows)
    totals = [proxy.refresh_turn_summary()["total"]]

    runtime.offline = True
    local["rows"] = [_row(21), _row(22)]
    local["pending"] = list(local["rows"])
    totals.append(proxy.refresh_turn_summary(reason="offline_new_attentions")["total"])

    runtime.offline = False
    central.rows = central_rows
    totals.append(proxy.refresh_turn_summary(reason="reconnected")["total"])

    assert totals == [20, 22, 22]
    assert 0 not in totals


def test_initial_gui_uses_loading_placeholder_instead_of_false_zero():
    source = Path("ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py").read_text(
        encoding="utf-8"
    )

    assert 'tk.StringVar(value="Total pacientes: —")' in source


def test_dataset_failure_states_are_explicit_and_never_valid_empty():
    proxy, runtime, central, local = _proxy([])
    identity = (SOURCE, TURN, 17, 31)

    unavailable = proxy.load_turn_dataset_result(identity=None)
    assert unavailable.status == "IDENTITY_UNAVAILABLE"
    assert not unavailable.is_valid

    runtime.offline = True
    local["error"] = "LOCAL_SQLITE_ERROR"
    assert proxy.load_turn_dataset_result(identity=identity).status == "QUERY_ERROR"
    local["error"] = ""
    assert (
        proxy.load_turn_dataset_result(identity=identity).status
        == "LOCAL_REPLICA_BEHIND"
    )

    runtime.offline = False
    central.failure = ValueError("bad row")
    assert proxy.load_turn_dataset_result(identity=identity).status == "QUERY_ERROR"
    runtime._temporary = lambda _exc: True
    assert (
        proxy.load_turn_dataset_result(identity=identity).status
        == "DATABASE_UNAVAILABLE"
    )
    runtime._temporary = lambda _exc: (_ for _ in ()).throw(RuntimeError("classifier"))
    assert proxy._dataset_error_status(ValueError("bad row")) == "QUERY_ERROR"


def test_zero_recheck_and_compatibility_errors_preserve_evidence():
    proxy, runtime, central, _local = _proxy([])
    identity = (SOURCE, TURN, 17, 31)

    runtime.offline = True
    assert proxy._confirm_central_zero(identity) == (False, "ZERO_RECHECK_OFFLINE")
    runtime.offline = False
    central.failure = ConnectionError("down")
    confirmed, code = proxy._confirm_central_zero(identity)
    assert not confirmed
    assert code == "ZERO_RECHECK_DATABASE_UNAVAILABLE"

    central.failure = None
    central.emergency_count = 0
    runtime.operational_session.turn_id = TURN + 1
    assert proxy._confirm_central_zero(identity) == (
        False,
        "ZERO_RECHECK_STALE_IDENTITY",
    )

    invalid = TurnDatasetResult(
        "QUERY_ERROR",
        SOURCE,
        TURN,
        0,
        0,
        (),
        "LAST_KNOWN_GOOD",
        "QUERY_ERROR",
        "2026-08-29T00:00:00+00:00",
    )
    object.__setattr__(proxy, "load_turn_dataset_result", lambda **_kwargs: invalid)
    with pytest.raises(TurnDatasetStateError, match="QUERY_ERROR"):
        proxy.build_turn_dataset(turn_id=TURN, operational_source_id=SOURCE)
    assert not proxy._summary_matches_identity({"_turn_id": "malformed"}, identity)


def test_background_worker_passes_reason_and_discards_invalid_result():
    operations = []
    requested = []
    runtime = SimpleNamespace(logger=logging.getLogger("test.summary-worker"))
    database = SimpleNamespace(
        _runtime=runtime,
        refresh_turn_summary=lambda *, reason: {"reason": reason},
    )
    coordinator = SimpleNamespace(
        submit_background=lambda operation, success, failure: operations.append(
            (operation, success, failure)
        )
    )
    controller = _V15BackgroundRefreshCoordinator.__new__(
        _V15BackgroundRefreshCoordinator
    )
    controller.admission = SimpleNamespace(db=database)
    controller.coordinator = coordinator
    controller._summary_busy = False
    controller._summary_pending = False
    controller._summary_reason = "attention_created"
    controller._summary_started_at = 0.0
    controller.request_summary = lambda reason: requested.append(reason)

    controller._start_summary()
    assert operations[0][0]() == {"reason": "attention_created"}

    controller._summary_pending = True
    assert controller._discard_invalid_summary(
        {"_status": "INVALID_REFRESH", "_error_code": "QUERY_ERROR"}, runtime
    )
    assert requested == ["pending_after_invalid_result"]
    assert not controller._discard_invalid_summary({"_status": "VALID"}, runtime)
