"""Adaptador en proceso entre el contexto principal y Admisión PySide6 V15.

Este módulo no autentica, no crea sesiones y no resuelve otra conexión.
Su única responsabilidad es construir el AdmissionWidget V15 certificado con
las dependencias que ya pertenecen a la aplicación principal.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import sys
import threading
import traceback
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from time import perf_counter, sleep
from types import MethodType
from typing import Any, ClassVar

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import QLabel

from admission_bridge import (
    AdmissionEventRef,
    AdmissionReadOnlyRepository,
    ShiftEventRef,
)
from app_resources import get_app_logo_path

_V15_PACKAGE = "ADMISION_PYSIDE6_V15"
_EXPECTED_V15_SOURCE_BUILD_ID = "20260830_V1010_REMOTE_PRIMARY_V1"


def _default_v15_root() -> Path:
    """Carga únicamente la V15 que pertenece a esta misma versión.

    Nunca usa una distribución histórica externa. Si el paquete V15 no fue
    incluido por PyInstaller, la integración debe fallar de forma explícita
    en lugar de ejecutar código viejo silenciosamente.
    """
    bundle_root = getattr(sys, "_MEIPASS", None)
    if bundle_root:
        return Path(bundle_root) / _V15_PACKAGE

    return Path(__file__).resolve().parent / _V15_PACKAGE


def _v15_runtime_is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _module_location(module: Any) -> str:
    return str(getattr(module, "__file__", "<frozen-module>"))


def _discard_noncanonical_v15_modules(root: Path) -> None:
    """Ensure source runs cannot reuse a package imported from another tree."""
    if _v15_runtime_is_frozen():
        return
    package = sys.modules.get(_V15_PACKAGE)
    package_file = getattr(package, "__file__", "") if package else ""
    if package_file:
        try:
            if Path(package_file).resolve().parent == root:
                return
        except OSError:
            pass
    for name in tuple(sys.modules):
        if name == _V15_PACKAGE or name.startswith(f"{_V15_PACKAGE}."):
            sys.modules.pop(name, None)


DEFAULT_V15_ROOT = _default_v15_root()
_V15_CAPABILITIES = frozenset(
    {
        "reports.view",
        "excel.open",
        "records.edit",
        "records.void",
        "configuration.manage",
    }
)
_V15_READ_CAPABILITIES = frozenset({"reports.view", "excel.open"})
_V15_OPERATIONAL_CAPABILITIES = frozenset({"records.edit", "records.void"})


def v15_capabilities_for_role(user: Any) -> frozenset[str]:
    """Expose controls according to the authenticated main-system role."""
    from admission_hybrid import (
        ADMISSION_ROLE_ADMINISTRATOR,
        ADMISSION_ROLE_AUXILIARY,
        canonical_role,
    )

    role = canonical_role(user)
    if role == ADMISSION_ROLE_ADMINISTRATOR:
        return _V15_CAPABILITIES
    if role == ADMISSION_ROLE_AUXILIARY:
        return _V15_READ_CAPABILITIES | _V15_OPERATIONAL_CAPABILITIES
    return _V15_READ_CAPABILITIES


class AdmissionV15EventBus(QObject):
    """Bus en proceso para V15; no depende de la integración PySide6 anterior."""

    attention_created = Signal(object)
    attention_updated = Signal(object)
    attention_cancelled = Signal(object)
    detail_sheet_generated = Signal(object)
    shift_changed = Signal(object)
    shift_closed = Signal(object)
    history_refresh_requested = Signal()


class AdmissionV15IntegrationError(RuntimeError):
    """V15 no pudo cargarse o construirse dentro de la aplicación principal."""


class ReportReadError(RuntimeError):
    """Safe, categorized failure while loading one statistical snapshot."""

    def __init__(self, code: str, safe_message: str):
        super().__init__(safe_message)
        self.code = str(code)
        self.safe_message = str(safe_message)


class TurnDatasetStateError(RuntimeError):
    """The operational identity was not valid enough to query a turn."""

    def __init__(self, code: str):
        self.code = str(code or "INVALID_DATASET_STATE")
        super().__init__(self.code)


TURN_DATASET_VALID_STATUSES = frozenset({"VALID", "VALID_EMPTY"})


@dataclass(frozen=True)
class TurnDatasetResult:
    """Rows plus the evidence needed to distinguish an empty turn from a failure."""

    status: str
    operational_source_id: str
    turn_id: int | None
    generation: int
    operational_revision: int
    rows: tuple[Mapping[str, Any], ...]
    source: str
    error_code: str
    generated_at: str
    central_count: int = 0
    local_count: int = 0
    pending_count: int = 0

    @property
    def is_valid(self) -> bool:
        return self.status in TURN_DATASET_VALID_STATUSES

    @property
    def display_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class TurnSummarySnapshot:
    """Versioned counts derived from one confirmed operational identity."""

    operational_source_id: str
    turn_id: int | None
    generation: int
    operational_revision: int
    counts: Mapping[str, int]
    refreshed_at: str
    status: str
    error_code: str = ""

    def to_mapping(self) -> dict[str, Any]:
        return {
            **{key: int(value or 0) for key, value in self.counts.items()},
            "_operational_source_id": self.operational_source_id,
            "_turn_id": self.turn_id,
            "_generation": self.generation,
            "_operational_revision": self.operational_revision,
            "_refreshed_at": self.refreshed_at,
            "_status": self.status,
            "_error_code": self.error_code,
        }


def _operational_identity_tuple(session: Any) -> tuple[str, int, int, int] | None:
    source_id = str(getattr(session, "operational_source_id", "") or "").strip()
    try:
        turn_id = int(getattr(session, "turn_id", 0) or 0)
        generation = int(getattr(session, "generation", 0) or 0)
        revision = int(getattr(session, "operational_revision", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not source_id or turn_id <= 0:
        return None
    return source_id, turn_id, generation, revision


def summary_result_matches_runtime_identity(
    result: Mapping[str, Any], runtime: Any
) -> bool:
    """Reject a worker result when any operational identity component changed."""
    current = _operational_identity_tuple(getattr(runtime, "operational_session", None))
    if current is None:
        return False
    try:
        incoming = (
            str(result.get("_operational_source_id") or "").strip(),
            int(result.get("_turn_id") or 0),
            int(result.get("_generation") or 0),
            int(result.get("_operational_revision") or 0),
        )
    except (TypeError, ValueError):
        return False
    return incoming == current


class _BackgroundTaskSignals(QObject):
    succeeded = Signal(object)
    failed = Signal(str)


class _BackgroundTask(QRunnable):
    """Runs blocking I/O in a controlled Qt worker pool."""

    def __init__(self, operation: Callable[[], Any], signals: _BackgroundTaskSignals):
        super().__init__()
        self.operation = operation
        self.signals = signals

    def run(self) -> None:
        try:
            self.signals.succeeded.emit(self.operation())
        except Exception as exc:  # noqa: BLE001 - worker boundary
            self.signals.failed.emit(type(exc).__name__)


class _HybridAdmissionRuntime:
    """Estado mutable compartido por V15, su guardia y el sincronizador."""

    def __init__(self, host: Any):
        from admission_hybrid import (
            AdmissionSyncService,
            AdmissionWriteGuard,
            ConnectionSupervisor,
            DatabaseConfigurationMissing,
            DatabaseTemporarilyOffline,
            OfflineAdmissionStore,
            OperationalSessionService,
            StationRole,
            is_temporary_connection_error,
        )

        self.host = host
        self.logger = getattr(host, "logger", None) or logging.getLogger(
            "hospital.admission.hybrid"
        )
        self.StationRole = StationRole
        self._temporary_errors = (
            DatabaseConfigurationMissing,
            DatabaseTemporarilyOffline,
        )
        self._is_temporary_connection_error = is_temporary_connection_error
        self.session_service = OperationalSessionService(host.connection_factory)
        self.guard = AdmissionWriteGuard()
        self.store: OfflineAdmissionStore | None = None
        self.sync_service: AdmissionSyncService | None = None
        self.patient_directory = None
        self.attachment = None
        self._attachment_from_cache = False
        self._operational_state = None
        self.offline = False
        self.offline_lease_valid = False
        self.status_message = "Verificando sesión operativa…"
        self._lock = threading.RLock()
        self._shutdown_started = False
        self._force_logout_emitted = False
        self._pending_transition_id = ""
        self._last_transition_result = None
        self._pending_sync_count = 0
        self._pending_mirror_state = None
        self._last_mirrored_generation = 0
        self._last_mirrored_operational_revision = 0
        self._last_heartbeat_at = 0.0
        self._last_patient_pull_at = 0.0
        self.connection_supervisor = ConnectionSupervisor(
            self._probe_operational_snapshot,
            reset_pool=self._reset_host_database_pool,
            log=self.logger.info,
        )
        configuration = dict(getattr(host, "configuration", {}) or {})
        self._central_schema_ready = bool(
            configuration.get("central_schema_ready")
        )
        self._seed_bootstrap_operational_snapshot(
            dict(getattr(host, "bootstrap_operational_snapshot", {}) or {})
        )

    def _reset_host_database_pool(self) -> None:
        """Drops broken PostgreSQL connections after a verified network loss."""
        module_name = getattr(self.host.connection_factory, "__module__", "")
        module = sys.modules.get(str(module_name))
        reset = getattr(module, "reset_database_pool", None)
        if callable(reset):
            reset()

    def _probe_operational_snapshot(self):
        """Small central probe used only from the coordinator worker."""
        return self.session_service.get_operational_session()

    def _seed_bootstrap_operational_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        """Adopt the already-fetched central state before reading the V15 mirror."""
        if not snapshot or not snapshot.get("operational_session_id"):
            return
        from admission_hybrid import (
            ConnectivityState,
            OperationalSession,
            OperationalState,
            StationRole,
            same_user,
        )

        primary_device_id = str(snapshot.get("primary_device_id") or "")
        role = (
            StationRole.PRIMARY
            if primary_device_id and primary_device_id == self.device_id
            else StationRole.SECONDARY
        )
        active_user_id = str(snapshot.get("active_user_id") or "")
        active_username = str(snapshot.get("active_username") or "")
        user_matches = same_user(
            {
                "active_user_id": active_user_id,
                "active_username": active_username,
            },
            self.current_user,
        )
        session = OperationalSession(
            operational_session_id=str(snapshot.get("operational_session_id") or ""),
            active_username=active_username,
            active_user_id=active_user_id,
            active_user_display_name=str(
                snapshot.get("active_user_display_name") or active_username
            ),
            primary_device_id=primary_device_id,
            primary_login_session_id=str(
                snapshot.get("primary_login_session_id") or ""
            ),
            turn_id=snapshot.get("turn_id"),
            turn_code=str(snapshot.get("turn_code") or ""),
            operational_source_id=str(
                snapshot.get("operational_source_id") or ""
            ),
            status=str(snapshot.get("status") or "ACTIVE"),
            generation=int(snapshot.get("generation") or 0),
            operational_revision=int(snapshot.get("operational_revision") or 1),
            lease_generation=int(snapshot.get("lease_generation") or 0),
            updated_at=snapshot.get("updated_at"),
            turn_started_at=snapshot.get("turn_started_at"),
            turn_ends_at=snapshot.get("turn_ends_at"),
        )
        state = OperationalState(
            operational_session_id=session.operational_session_id,
            generation=session.generation,
            active_user_id=session.active_user_id,
            active_username=session.active_username,
            active_user_display_name=session.active_user_display_name,
            turn_id=session.turn_id,
            turn_code=session.turn_code,
            primary_device_id=session.primary_device_id,
            primary_login_session_id=session.primary_login_session_id,
            local_device_id=self.device_id,
            local_login_session_id=self.login_session_id,
            device_role=role,
            device_attached=False,
            user_matches_operational=user_matches,
            write_allowed=False,
            connection_state=ConnectivityState.CONNECTED,
            sync_state="RECONCILING",
            reason_code="BOOTSTRAP_CENTRAL_SNAPSHOT",
            message="Conectado · actualizando estado operacional...",
            operational_source_id=session.operational_source_id,
            status=session.status,
            updated_at=session.updated_at,
            turn_started_at=session.turn_started_at,
            turn_ends_at=session.turn_ends_at,
            lease_generation=session.lease_generation,
            operational_revision=session.operational_revision,
        )
        # The bootstrap snapshot is authoritative for UI state, but it is not
        # a device attachment.  Keep the pending marker so the coordinator
        # performs the one atomic attach/rebind before enabling writes.
        self._attachment_from_cache = True
        self.apply_operational_snapshot(state, source="bootstrap_central")
        self._pending_mirror_state = state
        self.offline = False
        self.status_message = state.message

    def _attach_remote_if_needed(self) -> None:
        """Initial central attachment is performed only from a pool worker."""
        if not self._central_schema_ready:
            self.session_service.ensure_schema()
            self._central_schema_ready = True
        if not self.app_user_can_operate_admission:
            session = self.session_service.get_operational_session()
            self._set_readonly_operational_state(session)
            self._attachment_from_cache = False
            return
        if self.attachment is not None and not self._attachment_from_cache:
            return
        started = perf_counter()
        try:
            self.attachment = self.session_service.attach_device(
                login_username=self.username,
                login_user_id=self.user_id,
                device_id=self.device_id,
                login_session_id=self.login_session_id,
                device_name=str(getattr(self.host, "device_name", "")),
                turn_id=(getattr(self.host, "current_shift", {}) or {}).get("turn_id"),
                login_display_name=self.display_name,
                login_role=self.current_user.get("role"),
            )
        except Exception:
            self.logger.exception(
                "PRIMARY_ATTACH_ERROR device_id=%s elapsed_ms=%.1f",
                self.device_id,
                (perf_counter() - started) * 1000.0,
            )
            raise
        self._refresh_authoritative_state(reason="initial_attach")
        self._attachment_from_cache = False
        self.status_message = self.attachment.message or "Conectado."

    @property
    def current_user(self) -> dict[str, Any]:
        return dict(getattr(self.host, "user", {}) or {})

    @property
    def app_user_can_operate_admission(self) -> bool:
        from admission_hybrid import user_can_operate_admission

        return user_can_operate_admission(self.current_user)

    @property
    def username(self) -> str:
        return str(getattr(self.host, "user", {}).get("username") or "").strip()

    @property
    def user_id(self) -> Any:
        user = getattr(self.host, "user", {})
        return user.get("id", user.get("user_id"))

    @property
    def display_name(self) -> str:
        user = getattr(self.host, "user", {}) or {}
        return str(
            user.get("full_name")
            or user.get("display_name")
            or user.get("nombre")
            or self.username
        ).strip()

    @property
    def device_id(self) -> str:
        return str(getattr(self.host, "device_id", "") or "")

    @property
    def login_session_id(self) -> str:
        return str(getattr(self.host, "session_id", "") or "")

    @property
    def role(self):
        if self._operational_state is not None:
            return self._operational_state.device_role
        return self.attachment.role if self.attachment else self.StationRole.NONE

    @property
    def operational_session(self):
        return self.attachment.operational_session if self.attachment else None

    @property
    def writable(self) -> bool:
        if self._operational_state is not None:
            return bool(self._operational_state.write_allowed)
        return bool(self.attachment and self.attachment.writable)

    def _temporary(self, exc: BaseException) -> bool:
        no_connection_provider = isinstance(exc, TypeError) and (
            "context manager" in str(exc).casefold()
        )
        return (
            isinstance(exc, self._temporary_errors)
            or self._is_temporary_connection_error(exc)
            or no_connection_provider
        )

    def _set_readonly_operational_state(self, session: Any) -> None:
        from admission_hybrid import (
            ConnectivityState,
            DeviceAttachment,
            OperationalState,
            StationRole,
            evaluate_admission_access,
        )

        if session is None:
            self.attachment = None
            self._operational_state = OperationalState(
                operational_session_id="",
                generation=0,
                active_user_id="",
                active_username="",
                active_user_display_name="",
                turn_id=None,
                primary_device_id="",
                primary_login_session_id="",
                local_device_id=self.device_id,
                local_login_session_id=self.login_session_id,
                device_role=StationRole.NONE,
                device_attached=False,
                user_matches_operational=False,
                write_allowed=False,
                connection_state=ConnectivityState.CONNECTED,
                sync_state="ONLINE_SYNCED",
                reason_code="WAITING_ADMISSION_OPERATOR",
                message=(
                    "Admisión está esperando que un usuario autorizado inicie "
                    "la sesión operativa desde la computadora principal."
                ),
            )
        else:
            role = (
                StationRole.PRIMARY
                if session.primary_device_id == self.device_id
                else StationRole.SECONDARY
            )
            access = evaluate_admission_access(
                self.current_user,
                {
                    "base_write_allowed": False,
                    "device_role": role,
                    "connection_state": ConnectivityState.CONNECTED,
                    "status": session.status,
                },
            )
            self.attachment = DeviceAttachment(
                session,
                role,
                False,
                "Admisión disponible en modo de consulta; usuario operativo: "
                + (session.active_user_display_name or session.active_username)
                + ".",
            )
            self._operational_state = OperationalState(
                operational_session_id=session.operational_session_id,
                generation=session.generation,
                active_user_id=session.active_user_id,
                active_username=session.active_username,
                active_user_display_name=session.active_user_display_name,
                turn_id=session.turn_id,
                turn_code=session.turn_code,
                primary_device_id=session.primary_device_id,
                primary_login_session_id=session.primary_login_session_id,
                local_device_id=self.device_id,
                local_login_session_id=self.login_session_id,
                device_role=role,
                device_attached=role == StationRole.PRIMARY,
                user_matches_operational=False,
                write_allowed=False,
                connection_state=ConnectivityState.CONNECTED,
                sync_state="ONLINE_SYNCED",
                reason_code=access.reason_code,
                message=self.attachment.message,
                operational_source_id=session.operational_source_id,
                status=session.status,
                updated_at=session.updated_at,
                turn_started_at=session.turn_started_at,
                turn_ends_at=session.turn_ends_at,
                lease_generation=session.lease_generation,
                operational_revision=session.operational_revision,
                view_allowed=access.view_allowed,
                can_manage_primary=access.can_manage_primary,
                can_change_turn=access.can_change_turn,
                can_generate_attention=access.can_generate_attention,
            )
        self.offline = False
        self.offline_lease_valid = False
        self.status_message = self._operational_state.message
        if session is not None:
            self.apply_operational_snapshot(
                self._operational_state,
                source="central_readonly_snapshot",
            )

    # Compatibility for older coordinator code kept in packaged installations.
    _set_auxiliary_operational_state = _set_readonly_operational_state

    @staticmethod
    def _session_from_state(state: Any):
        from admission_hybrid import OperationalSession

        if not state.operational_session_id:
            return None
        return OperationalSession(
            operational_session_id=state.operational_session_id,
            active_username=state.active_username,
            active_user_id=state.active_user_id,
            active_user_display_name=state.active_user_display_name,
            primary_device_id=state.primary_device_id,
            primary_login_session_id=state.primary_login_session_id,
            turn_id=state.turn_id,
            turn_code=getattr(state, "turn_code", ""),
            operational_source_id=state.operational_source_id,
            status=state.status,
            generation=state.generation,
            operational_revision=getattr(state, "operational_revision", 1),
            updated_at=state.updated_at,
            turn_started_at=state.turn_started_at,
            turn_ends_at=state.turn_ends_at,
            lease_generation=state.lease_generation,
        )

    def _mirror_v15_turn_config(self, session: Any) -> None:
        """Mantiene el turno V15 local como espejo del estado central.

        Esta operación se ejecuta desde el coordinador de espejo en segundo
        plano. Nunca usa el proxy híbrido: crear la fila local es necesario
        para materializar atenciones remotas, pero no constituye una
        transición central de turno.
        """
        database = getattr(self, "_bound_database", None)
        module = sys.modules.get(database.__class__.__module__) if database else None
        if module is None:
            return
        load_config = getattr(module, "cargar_turno_config", None)
        save_config = getattr(module, "guardar_turno_config", None)
        create_local_turn = getattr(database, "obtener_o_crear_turno", None)
        is_current_turn_config = getattr(module, "turno_config_es_vigente", None)
        if not callable(load_config) or not callable(save_config):
            return
        config = load_config(permitir_vencido=True)
        config = dict(config) if isinstance(config, Mapping) else {}
        representative = (
            session.active_user_display_name or session.active_username
        ).strip()
        turn_code = str(getattr(session, "turn_code", "") or "").strip()
        if not turn_code and session.primary_device_id == self.device_id:
            legacy_code = str(config.get("turno_codigo") or "").strip()
            if legacy_code:
                repaired = self.session_service.backfill_missing_turn_code(
                    operational_session_id=session.operational_session_id,
                    primary_device_id=self.device_id,
                    primary_login_session_id=self.login_session_id,
                    expected_generation=session.generation,
                    turn_code=legacy_code,
                    changed_by=self.username,
                )
                repaired_code = str(
                    getattr(repaired, "turn_code", "") or ""
                ).strip()
                if repaired_code:
                    turn_code = repaired_code
                    self.logger.info(
                        "OP_STATE_TURN_CODE_BACKFILLED central_turn=%s code=%s",
                        session.turn_id,
                        turn_code,
                    )
        # The local file is a mirror. It must never invent a default turn or
        # representative while central configuration is incomplete.
        if not representative or not turn_code:
            return
        mirror_key = (
            str(getattr(session, "operational_session_id", "") or ""),
            str(getattr(session, "turn_id", "") or ""),
            int(getattr(session, "generation", 0) or 0),
            int(getattr(session, "operational_revision", 0) or 0),
            representative,
            turn_code,
            str(getattr(session, "turn_started_at", "") or ""),
        )
        if getattr(self, "_last_v15_local_turn_mirror_key", None) == mirror_key:
            return
        started_at = getattr(session, "turn_started_at", None)
        if isinstance(started_at, str):
            try:
                started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            except ValueError:
                started_at = None
        now = datetime.now(timezone.utc).astimezone().replace(tzinfo=None)
        started_local = (
            started_at.astimezone().replace(tzinfo=None)
            if isinstance(started_at, datetime) and started_at.tzinfo is not None
            else started_at.replace(tzinfo=None)
            if isinstance(started_at, datetime)
            else now
        )
        # The authoritative start date is the central turn start.  Falling
        # back to the V15 operational-day convention is only for incomplete
        # legacy snapshots.
        operational_date = started_local.date() if started_at else now.date() - (
            timedelta(days=1) if now.time() < time(8, 0) else timedelta()
        )
        turn_config = {
            "representante": representative,
            "turno_codigo": turn_code,
            "fecha_base": operational_date,
            "inicio_real_dt": started_local,
        }
        administrative_override = bool(
            callable(is_current_turn_config)
            and not is_current_turn_config(turn_config, momento=now)
        )
        turn_config["administrative_override"] = administrative_override
        turn_config["override_reason"] = (
            "Espejo operativo central" if administrative_override else ""
        )
        already_current = (
            config.get("representante") == representative
            and config.get("turno_codigo") == turn_code
            and config.get("fecha_base") == operational_date
            and config.get("inicio_real_dt") == started_local
            and bool(config.get("administrative_override")) == administrative_override
        )
        if not already_current:
            try:
                saved = save_config(
                    representative,
                    turn_code,
                    operational_date,
                    started_local,
                    administrative_override=administrative_override,
                    override_reason=turn_config["override_reason"],
                )
            except TypeError as exc:
                # Compatibility with a certified V15 module that predates the
                # optional override arguments.  Current V15 receives the full
                # mirror metadata; this fallback only preserves its older
                # four-argument persistence contract.
                if "unexpected keyword" not in str(exc):
                    raise
                saved = save_config(
                    representative,
                    turn_code,
                    operational_date,
                    started_local,
                )
            if not saved:
                raise RuntimeError("No se pudo actualizar el archivo espejo del turno V15.")

        # The row is strictly a local SQLite mirror.  Calling the underlying
        # V15 manager directly avoids _HybridDatabaseProxy and therefore
        # cannot invoke require_primary_transition(), create a central turn,
        # change the operational representative, or generate a report.
        if not callable(create_local_turn):
            self.logger.warning("OP_STATE_LOCAL_TURN_MIRROR_UNAVAILABLE")
            return
        local_turn_id = create_local_turn(turn_config, administrative_override=True)
        if not local_turn_id:
            raise RuntimeError("No se pudo materializar el turno local espejo V15.")
        self.logger.info(
            "OP_STATE_LOCAL_TURN_MIRROR central_turn=%s local_turn=%s generation=%s",
            session.turn_id,
            local_turn_id,
            session.generation,
        )
        self._last_v15_local_turn_mirror_key = mirror_key

    @staticmethod
    def _snapshot_revision_key(state: Any) -> tuple[int, int, int]:
        """Orden de versión para una única sesión operacional central."""
        return (
            int(getattr(state, "operational_revision", 0) or 0),
            int(getattr(state, "generation", 0) or 0),
            int(getattr(state, "lease_generation", 0) or 0),
        )

    def _snapshot_is_stale(self, state: Any) -> bool:
        previous = self._operational_state
        if previous is None:
            return False
        previous_session_id = str(
            getattr(previous, "operational_session_id", "") or ""
        )
        incoming_session_id = str(
            getattr(state, "operational_session_id", "") or ""
        )
        if not previous_session_id or previous_session_id != incoming_session_id:
            return False
        return self._snapshot_revision_key(state) < self._snapshot_revision_key(previous)

    def apply_operational_snapshot(
        self,
        state: Any,
        *,
        source: str = "central",
    ) -> bool:
        """Apply one coherent central snapshot to all runtime state consumers.

        ``OperationalState`` is the in-memory canonical representation.  V15
        JSON/SQLite mirrors may be updated later, but never decide or replace
        representative, turn, or lease values while connected.
        """
        from admission_hybrid import DeviceAttachment

        session = self._session_from_state(state)
        if session is None:
            return False
        if self._snapshot_is_stale(state):
            previous = self._operational_state
            self.logger.info(
                "OP_SNAPSHOT_STALE_IGNORED source=%s old_revision=%s new_revision=%s "
                "old_generation=%s new_generation=%s",
                source,
                getattr(previous, "operational_revision", 0),
                getattr(state, "operational_revision", 0),
                getattr(previous, "generation", 0),
                getattr(state, "generation", 0),
            )
            return False
        previous = self.operational_session
        previous_identity = (
            getattr(previous, "active_user_id", ""),
            getattr(previous, "active_username", ""),
            getattr(previous, "active_user_display_name", ""),
            getattr(previous, "turn_id", None),
            getattr(previous, "turn_code", ""),
            getattr(previous, "generation", None),
            getattr(previous, "operational_revision", None),
            getattr(previous, "lease_generation", None),
        )
        incoming_identity = (
            session.active_user_id,
            session.active_username,
            session.active_user_display_name,
            session.turn_id,
            session.turn_code,
            session.generation,
            session.operational_revision,
            session.lease_generation,
        )
        self.attachment = DeviceAttachment(
            session, state.device_role, state.write_allowed, state.message
        )
        self._operational_state = state
        self.offline = False
        self.status_message = state.message
        shift = dict(getattr(self.host, "current_shift", {}) or {})
        shift.update(
            {
                "turn_id": session.turn_id,
                "turn_code": session.turn_code,
                "owner_user_id": session.active_user_id,
                "owner_username": session.active_username,
                "representative_display_name": (
                    session.active_user_display_name or session.active_username
                ),
                "generation": session.generation,
                "operational_revision": session.operational_revision,
                "operational_session_id": session.operational_session_id,
                "operational_source_id": session.operational_source_id,
                "turn_started_at": session.turn_started_at,
                "turn_ends_at": session.turn_ends_at,
            }
        )
        self.host.current_shift = shift
        self.logger.info(
            "OP_SNAPSHOT_APPLY source=%s old_revision=%s new_revision=%s "
            "turn_id=%s representative_id=%s role=%s changed=%s",
            source,
            getattr(previous, "operational_revision", 0),
            session.operational_revision,
            session.turn_id,
            session.active_user_id,
            state.device_role.value,
            str(previous_identity != incoming_identity).lower(),
        )
        self.logger.info(
            "OP_SNAPSHOT_UI_APPLIED turn_id=%s representative_id=%s",
            session.turn_id,
            session.active_user_id,
        )
        return True

    # Compatibility name used by already-loaded V15 integrations.
    def adopt_central_operational_state(self, state: Any) -> None:
        self.apply_operational_snapshot(state, source="central_adoption")

    def apply_operational_mirror_to_v15(self, state: Any | None = None) -> dict[str, Any]:
        """Actualiza el espejo local en background; nunca decide el estado online."""
        from dataclasses import replace

        with self._lock:
            target = state or self._pending_mirror_state
            session = self._session_from_state(target)
            if session is None:
                return self.state()
            if self.store is not None:
                self.store.apply_remote_operational_state(
                    session,
                    target.device_role,
                    device_id=self.device_id,
                    actor_user_id=self.user_id,
                    actor_username=self.username,
                )
            self._mirror_v15_turn_config(session)
            self._last_mirrored_generation = session.generation
            self._last_mirrored_operational_revision = session.operational_revision
            if self._pending_mirror_state is target:
                self._pending_mirror_state = None
            synced = replace(
                target,
                sync_state="ONLINE_SYNCED",
                message=(
                    target.message
                    if target.message and "actualizando" not in target.message.casefold()
                    else "Conectado · Sincronizado"
                ),
            )
            self.apply_operational_snapshot(synced, source="local_mirror")
            self.logger.info(
                "OP_SNAPSHOT_MIRROR_UPDATED revision=%s turn_id=%s representative_id=%s",
                session.operational_revision,
                session.turn_id,
                session.active_user_id,
            )
            self.offline_lease_valid = bool(
                synced.write_allowed and self.app_user_can_operate_admission
            )
            return self.state()

    def mark_operational_mirror_pending(self) -> dict[str, Any]:
        """Conserva autoridad central online cuando el espejo local debe reintentarse."""
        from dataclasses import replace

        with self._lock:
            target = self._pending_mirror_state or self._operational_state
            if target is None:
                return self.state()
            pending = replace(
                target,
                sync_state="LOCAL_MIRROR_PENDING",
                message="Conectado · actualizando estado local...",
            )
            self._pending_mirror_state = pending
            self.apply_operational_snapshot(pending, source="local_mirror_pending")
            return self.state()

    def apply_remote_operational_state(self, state: Any) -> None:
        """Compatibilidad: adopta central y luego refleja localmente."""
        self.apply_operational_snapshot(state, source="remote_compatibility")
        self.apply_operational_mirror_to_v15(state)

    def _refresh_authoritative_state(
        self, *, reason: str = "", enforce_generation: bool = True,
    ):
        started = perf_counter()
        self.logger.info(
            "OP_SNAPSHOT_FETCH_START reason=%s device_id=%s",
            reason,
            self.device_id,
        )
        local_generation = None
        if enforce_generation and self.attachment is not None:
            local_generation = self.attachment.operational_session.generation
        state = self.session_service.get_central_admission_operational_state(
            current_user=self.current_user,
            current_session_id=self.login_session_id,
            current_device_id=self.device_id,
            local_generation=local_generation,
        )
        self.logger.info(
            "OP_SNAPSHOT_FETCH_DONE reason=%s elapsed_ms=%.1f revision=%s turn_id=%s "
            "representative_id=%s primary_device_id=%s device_role=%s",
            reason,
            (perf_counter() - started) * 1000.0,
            state.operational_revision,
            state.turn_id,
            state.active_user_id,
            state.primary_device_id,
            state.device_role.value,
        )
        if (
            state.reason_code in {"STALE_GENERATION", "READONLY_STALE_GENERATION"}
            and state.device_attached
            and state.user_matches_operational
        ):
            # El cambio de turno del mismo usuario conserva los attachments;
            # el heartbeat adopta el nuevo token central antes de sincronizar.
            state = self.session_service.get_central_admission_operational_state(
                current_user=self.current_user,
                current_session_id=self.login_session_id,
                current_device_id=self.device_id,
                local_generation=None,
            )
        if not self.apply_operational_snapshot(state, source=reason or "central_fetch"):
            if self._operational_state is not None:
                return self._operational_state
        self.logger.debug(
            "Estado operativo refrescado reason=%s device=%s role=%s generation=%s write=%s code=%s",
            reason,self.device_id,state.device_role.value,state.generation,
            state.write_allowed,state.reason_code,
        )
        return state

    def bind_database(self, database: Any) -> None:
        from admission_hybrid import (
            AdmissionCloudRepository,
            AdmissionSyncService,
            OfflineAdmissionStore,
        )
        from patient_directory import PatientDirectoryService

        with self._lock:
            self._bound_database = database
            self.store = OfflineAdmissionStore(database.db_name)
            self.store.initialize()
            # V15 uses the same local writer/outbox as the sync worker.  The
            # reference is local-only and does not make SQLite authoritative.
            database.hybrid_store = self.store
            if self.attachment is None:
                cached = self.store.cached_attachment()
                if cached is not None:
                    from admission_hybrid import (
                        ADMISSION_ROLE_ADMINISTRATOR,
                        ADMISSION_ROLE_AUXILIARY,
                        DeviceAttachment,
                        canonical_role,
                        same_user,
                    )
                    auth_role = canonical_role(self.current_user)
                    identity_matches = same_user(
                        cached.operational_session, self.current_user
                    )
                    cached_write = bool(
                        cached.writable
                        and (
                            auth_role == ADMISSION_ROLE_ADMINISTRATOR
                            or (
                                auth_role == ADMISSION_ROLE_AUXILIARY
                                and identity_matches
                            )
                        )
                    )
                    self.attachment = DeviceAttachment(
                        cached.operational_session,
                        cached.role,
                        cached_write,
                        (
                            "Sin conexión · trabajando localmente"
                            if cached_write
                            else "Sin conexión · Admisión en solo lectura"
                        ),
                    )
                    self._attachment_from_cache = True
                    self.offline_lease_valid = cached_write
                    if cached_write:
                        # Actualiza el actor del nuevo login sin cambiar al
                        # representante operacional guardado en la réplica.
                        self.store.configure_runtime_context(
                            cached.operational_session,
                            device_id=self.device_id,
                            actor_user_id=self.user_id,
                            actor_username=self.username,
                        )
                self.offline = True
                self.status_message = (
                    self.attachment.message
                    if self.attachment is not None
                    else "Sin conexión · trabajando localmente"
                )
            if self.attachment is not None and self._operational_state is not None:
                # Central state is already authoritative in memory. The local
                # V15 mirror is deliberately applied by the coordinator worker.
                self._pending_mirror_state = self._operational_state
            self.sync_service = AdmissionSyncService(
                self.store, AdmissionCloudRepository(self.host.connection_factory)
            )
            self.patient_directory = PatientDirectoryService(
                database.db_name,
                self.host.connection_factory,
                is_online=lambda: not self.offline,
            )

    def search_patient_directory(self, *, cedula: str = "", nss: str = "") -> dict[str, Any] | None:
        """Exact local-first lookup; cloud hydration runs in the V15 worker."""
        if self.patient_directory is None:
            return None
        if cedula:
            return self.patient_directory.find_by_cedula(cedula)
        if nss:
            return self.patient_directory.find_by_nss(nss)
        return None

    def verify_patient_with_cloud(
        self, *, cedula: str = "", nss: str = "", timeout_ms: int = 1500
    ) -> dict[str, Any] | None:
        """Remote verification entry point; callers must invoke it in a worker."""
        if self.patient_directory is None or self.offline:
            return None
        return self.patient_directory.verify_with_cloud(
            cedula=cedula, nss=nss, timeout_ms=timeout_ms
        )

    def require_write(self, *, primary_only: bool = False):
        attachment = self.attachment
        session = attachment.operational_session if attachment else None
        kwargs = {
            "login_user": self.username,
            "login_user_id": self.user_id,
            "login_role": self.current_user.get("role"),
            "device_id": self.device_id,
            "session": session,
            "generation": session.generation if session else None,
            "role": attachment.role if attachment else self.StationRole.NONE,
            "offline": self.offline,
            "offline_lease_valid": self.offline_lease_valid or not self.offline,
        }
        if primary_only:
            return self.guard.require_primary_turn_change(**kwargs)
        return self.guard.require_write(**kwargs)

    def require_primary_transition(self):
        attachment = self.attachment
        session = attachment.operational_session if attachment else None
        return self.guard.require_primary_transition(
            device_id=self.device_id,
            session=session,
            generation=session.generation if session else None,
            role=attachment.role if attachment else self.StationRole.NONE,
            offline=self.offline,
            offline_lease_valid=self.offline_lease_valid or not self.offline,
            current_user=self.current_user,
        )

    def can_change_admission_turn(self) -> bool:
        """Evaluate the central PRIMARY turn policy from the adopted snapshot."""
        from admission_hybrid import can_change_admission_turn

        state = self._operational_state
        allowed = bool(
            state is not None
            and not self.offline
            and can_change_admission_turn(
                self.current_user, state, state.device_role
            )
        )
        logger = getattr(self, "logger", None)
        if state is not None and logger is not None:
            logger.info(
                "TURN_PERMISSION_EVALUATED device_role=%s authenticated_user_id=%s "
                "representative_user_id=%s allowed=%s",
                getattr(state, "device_role", ""),
                self.user_id,
                getattr(state, "active_user_id", ""),
                allowed,
            )
        return allowed

    def is_primary_shift_handover(self) -> bool:
        """Whether the next normal turn change is an explicit operator handover."""
        from admission_hybrid import is_primary_shift_handover

        state = self._operational_state
        return bool(
            state is not None
            and not self.offline
            and is_primary_shift_handover(
                self.current_user,
                state,
                state.device_role,
            )
        )

    def require_primary_turn_change(self):
        """Guard the normal turn command without treating it as a user transition."""
        attachment = self.attachment
        session = attachment.operational_session if attachment else None
        if not self.can_change_admission_turn():
            from admission_hybrid import AdmissionWriteBlocked

            raise AdmissionWriteBlocked(
                "Solo un usuario operativo autorizado en la estación PRIMARY "
                "puede cambiar el turno de Admisión."
            )
        return self.guard.require_primary_turn_change(
            login_user=self.username,
            login_user_id=self.user_id,
            login_role=self.current_user.get("role"),
            device_id=self.device_id,
            session=session,
            generation=session.generation if session else None,
            role=attachment.role if attachment else self.StationRole.NONE,
            offline=self.offline,
            offline_lease_valid=self.offline_lease_valid or not self.offline,
        )

    def refresh_operational_state(self, *, force_remote: bool = True) -> dict[str, Any]:
        """Obtiene la autoridad central; el espejo V15 queda para otro worker."""
        if not self.app_user_can_operate_admission:
            session = self.session_service.get_operational_session()
            self._set_readonly_operational_state(session)
            state = self._operational_state
        else:
            state = self._refresh_authoritative_state(
                reason="force_remote" if force_remote else "refresh",
                enforce_generation=not force_remote,
            )
            state = self._reattach_matching_device(state)
        session = self.operational_session
        if session is not None and state is not None:
            from dataclasses import replace

            visible_state = replace(
                state,
                sync_state="RECONCILING",
                message="Conectado · Secundaria · Sincronizando datos"
                if state.device_role == self.StationRole.SECONDARY
                else "Conectado · Sincronizando datos",
            )
            self._pending_mirror_state = visible_state
            self.apply_operational_snapshot(visible_state, source="bootstrap_reconciling")
            return {**self.state(), "local_mirror_pending": True}
        return self.state()

    def perform_explicit_turn_handoff(
        self,
        turn_id: int | None = None,
        *,
        shift_metadata: Mapping[str, Any] | None = None,
    ):
        """The only normal-operation boundary allowed to allocate a central turn."""
        from admission_hybrid import (
            AdmissionWriteBlocked,
            ConnectivityState,
            SAME_USER_HANDOFF_MESSAGE,
            TRIGGER_USER_REQUESTED_HANDOFF,
            evaluate_admission_access,
            same_user,
        )

        self.require_primary_turn_change()
        session = self.operational_session
        if session is None:
            raise AdmissionWriteBlocked("No existe una sesión operativa activa.")
        from dataclasses import replace
        from admission_hybrid import DeviceAttachment

        metadata = dict(shift_metadata or {})
        administrative_override = bool(metadata.get("administrative_override"))
        override_reason = str(metadata.get("override_reason") or "").strip()
        formal_handover = self.is_primary_shift_handover() and not administrative_override
        if not administrative_override and not formal_handover:
            raise AdmissionWriteBlocked(SAME_USER_HANDOFF_MESSAGE)
        if not self._pending_transition_id:
            import uuid
            self._pending_transition_id = str(uuid.uuid4())

        if formal_handover:
            self.logger.info(
                "PRIMARY_SHIFT_HANDOVER_START device_id=%s revision=%s",
                self.device_id,
                session.generation,
            )
            result = self.session_service.transition_primary_user(
                operational_session_id=session.operational_session_id,
                primary_device_id=self.device_id,
                new_login_session_id=self.login_session_id,
                new_user=self.current_user,
                new_turn_id=turn_id,
                allocate_central_turn_id=True,
                new_turn_code=str(metadata.get("turno_codigo") or ""),
                expected_generation=session.generation,
                transition_id=self._pending_transition_id,
                changed_by=self.username,
                reason="Relevo formal de turno PRIMARY V15",
                invalidate_secondaries=True,
                invalidate_only_previous_user_secondaries=True,
                trigger=TRIGGER_USER_REQUESTED_HANDOFF,
            )
            self.logger.info(
                "PRIMARY_SHIFT_HANDOVER_COMMIT device_id=%s revision=%s invalidated=%s",
                self.device_id,
                result.new_generation,
                len(result.invalidated_login_session_ids),
            )
        else:
            # Extraordinary Admin turn correction remains explicitly separate
            # from the normal representative+turn handoff.
            result = self.session_service.admin_set_admission_turn(
                actor_user=self.current_user,
                operational_session_id=session.operational_session_id,
                primary_device_id=self.device_id,
                new_turn_id=turn_id,
                allocate_central_turn_id=True,
                new_turn_code=str(metadata.get("turno_codigo") or ""),
                expected_generation=session.generation,
                transition_id=self._pending_transition_id,
                reason=(
                    override_reason
                    if administrative_override and override_reason
                    else "Cambio de turno principal V15"
                ),
                administrative_override=administrative_override,
            )
        changed = result.operational_session
        if result.committed:
            self._pending_transition_id = ""
        self._last_transition_result = result
        identity_matches = same_user(changed, self.current_user)
        access = evaluate_admission_access(
            self.current_user,
            {
                "base_write_allowed": True,
                "device_role": self.StationRole.PRIMARY,
                "connection_state": ConnectivityState.CONNECTED,
                "status": changed.status,
                "reason_code": "TURN_CHANGE_COMMITTED",
                "active_user_id": changed.active_user_id,
                "active_username": changed.active_username,
            },
        )
        message = "Conectado · Sincronizando réplica..."
        if self._operational_state is not None:
            confirmed_state = replace(
                self._operational_state,
                active_user_id=changed.active_user_id,
                active_username=changed.active_username,
                active_user_display_name=changed.active_user_display_name,
                turn_id=changed.turn_id,
                turn_code=changed.turn_code,
                turn_started_at=changed.turn_started_at,
                turn_ends_at=changed.turn_ends_at,
                primary_device_id=changed.primary_device_id,
                primary_login_session_id=changed.primary_login_session_id,
                generation=changed.generation,
                operational_revision=changed.operational_revision,
                lease_generation=changed.lease_generation,
                device_role=self.StationRole.PRIMARY,
                device_attached=True,
                user_matches_operational=identity_matches,
                write_allowed=bool(access.write_allowed),
                connection_state=ConnectivityState.CONNECTED,
                sync_state="LOCAL_MIRROR_PENDING",
                reason_code="TURN_CHANGE_COMMITTED",
                message=message,
                invalidated_reason="",
                view_allowed=access.view_allowed,
                can_manage_primary=access.can_manage_primary,
                can_change_turn=access.can_change_turn,
                can_generate_attention=access.can_generate_attention,
            )
            self.apply_operational_snapshot(
                confirmed_state,
                source="turn_change_commit",
            )
        else:
            self.attachment = DeviceAttachment(
                changed,
                self.StationRole.PRIMARY,
                bool(access.write_allowed),
                message,
            )
        self.status_message = message
        self.offline = False
        self.offline_lease_valid = bool(access.write_allowed)
        self._force_logout_emitted = False
        self._pending_mirror_state = self._operational_state
        return result

    def change_primary_turn(
        self,
        turn_id: int | None,
        *,
        shift_metadata: Mapping[str, Any] | None = None,
    ):
        """Compatibility alias requiring the same explicit handoff boundary."""
        return self.perform_explicit_turn_handoff(
            turn_id,
            shift_metadata=shift_metadata,
        )


    def admin_correct_current_turn_representative(
        self,
        target: Any,
        *,
        authorizing_admin: Any,
    ):
        """Central-first representative correction; never moves PRIMARY/turn."""
        from dataclasses import replace
        from admission_hybrid import (
            AdmissionWriteBlocked,
            ConnectivityState,
            DeviceAttachment,
            StationRole,
            evaluate_admission_access,
            same_user,
        )

        session = self.operational_session
        if session is None:
            raise AdmissionWriteBlocked("No existe una sesión operativa activa.")

        target_user = {
            "user_id": getattr(target, "user_id", None)
            or getattr(target, "id", None)
            or "",
            "username": getattr(target, "username", ""),
            "full_name": getattr(target, "full_name", "")
            or getattr(target, "display_name", "")
            or getattr(target, "username", ""),
            "role": getattr(target, "role", ""),
        }
        authorizing_admin_user = {
            "user_id": getattr(authorizing_admin, "user_id", None)
            or getattr(authorizing_admin, "id", None)
            or "",
            "username": getattr(authorizing_admin, "username", ""),
            "full_name": getattr(authorizing_admin, "full_name", "")
            or getattr(authorizing_admin, "display_name", "")
            or getattr(authorizing_admin, "username", ""),
            "role": getattr(authorizing_admin, "role", ""),
        }

        # 1) Commit central atómico. Ésta es la única autoridad del cambio.
        changed = self.session_service.admin_set_admission_representative(
            authorizing_admin_user_id=authorizing_admin_user["user_id"],
            authorizing_admin_username=authorizing_admin_user["username"],
            authorizing_admin_role=authorizing_admin_user["role"],
            requesting_user_id=self.user_id,
            requesting_username=self.username,
            requesting_login_session_id=self.login_session_id,
            requesting_device_id=self.device_id,
            target_user=target_user,
            reason="Corrección administrativa desde Configuración interna",
        )

        # 2) Adoptar inmediatamente el resultado confirmado SIN un segundo
        # round-trip PostgreSQL. El coordinador verificará el snapshot en su
        # próximo ciclo de background.
        role = self.role
        attached = role in {StationRole.PRIMARY, StationRole.SECONDARY}
        identity_matches = same_user(changed, self.current_user)
        access = evaluate_admission_access(
            self.current_user,
            {
                "base_write_allowed": bool(attached),
                "device_role": role,
                "connection_state": ConnectivityState.CONNECTED,
                "status": changed.status,
                "reason_code": "ADMIN_REPRESENTATIVE_CORRECTION_COMMITTED",
                "active_user_id": changed.active_user_id,
                "active_username": changed.active_username,
            },
        )
        representative = (
            changed.active_user_display_name or changed.active_username
        )
        message = (
            "Conectado · Administrador · representante operativo: "
            + representative
            + "."
        )
        if self._operational_state is not None:
            confirmed_state = replace(
                self._operational_state,
                active_user_id=changed.active_user_id,
                active_username=changed.active_username,
                active_user_display_name=changed.active_user_display_name,
                turn_id=changed.turn_id,
                turn_code=changed.turn_code,
                primary_device_id=changed.primary_device_id,
                primary_login_session_id=changed.primary_login_session_id,
                generation=changed.generation,
                operational_revision=changed.operational_revision,
                user_matches_operational=identity_matches,
                device_attached=attached,
                write_allowed=bool(access.write_allowed),
                connection_state=ConnectivityState.CONNECTED,
                sync_state="ONLINE_SYNCED",
                reason_code="ADMIN_REPRESENTATIVE_CORRECTION_COMMITTED",
                message=message,
                invalidated_reason="",
                view_allowed=access.view_allowed,
                can_manage_primary=access.can_manage_primary,
                can_change_turn=access.can_change_turn,
                can_generate_attention=access.can_generate_attention,
            )
            self.apply_operational_snapshot(
                confirmed_state,
                source="representative_change_commit",
            )
        else:
            self.attachment = DeviceAttachment(
                changed, role, bool(access.write_allowed), message
            )
        self.status_message = message
        self.offline = False
        self.offline_lease_valid = bool(access.write_allowed)
        self._force_logout_emitted = False
        self._pending_mirror_state = self._operational_state

        # 3) Reflejo SQLite/local después del commit. Si falla, el estado central
        # sigue siendo válido y el worker lo reintentará.
        if self.store is not None:
            try:
                self.store.apply_remote_operational_state(
                    changed,
                    role,
                    device_id=self.device_id,
                    actor_user_id=self.user_id,
                    actor_username=self.username,
                )
                self._mirror_v15_turn_config(changed)
                self._last_mirrored_generation = changed.generation
                self._last_mirrored_operational_revision = (
                    changed.operational_revision
                )
                self._pending_mirror_state = None
            except Exception:
                self.logger.exception(
                    "Representante confirmado centralmente; espejo local pendiente"
                )

        return changed

    def list_primary_transfer_candidates(self) -> list[dict[str, Any]]:
        """Load centrally connected stations eligible for a PRIMARY transfer."""
        from admission_hybrid import AdmissionWriteBlocked, canonical_role

        if canonical_role(self.current_user) != "administrador":
            raise AdmissionWriteBlocked(
                "Solo un Administrador puede consultar estaciones conectadas."
            )
        session = self.operational_session
        if session is None:
            raise AdmissionWriteBlocked("No existe una sesión operativa activa.")
        return self.session_service.list_primary_transfer_candidates(
            operational_session_id=session.operational_session_id
        )

    def force_transfer_admission_primary(
        self,
        *,
        target_device_id: str,
        target_login_session_id: str,
        reason: str,
        expected_operational_revision: int | None = None,
    ):
        """Transfer the central lease without mutating turn/user/generation."""
        from admission_hybrid import (
            AdmissionWriteBlocked,
            DeviceAttachment,
            canonical_role,
        )

        if canonical_role(self.current_user) != "administrador":
            raise AdmissionWriteBlocked(
                "Solo un Administrador puede transferir la sesión principal."
            )
        session = self.operational_session
        if session is None:
            raise AdmissionWriteBlocked("No existe una sesión operativa activa.")
        changed = self.session_service.force_transfer_admission_primary(
            operational_session_id=session.operational_session_id,
            target_device_id=target_device_id,
            target_login_session_id=target_login_session_id,
            actor_device_id=self.device_id,
            actor_login_session_id=self.login_session_id,
            expected_operational_revision=(
                session.operational_revision
                if expected_operational_revision is None
                else int(expected_operational_revision)
            ),
            admin_user_id=self.user_id,
            admin_username=self.username,
            admin_role=self.current_user.get("role"),
            reason=reason,
        )
        # El commit central ya confirmó la transferencia. Adoptamos el resultado
        # inmediatamente en memoria y dejamos el refresco remoto periódico al
        # coordinador. Esto evita round-trips extra después del commit y hace
        # que la UI deje de mostrar "Transfiriendo..." de inmediato.
        is_local_primary = changed.primary_device_id == self.device_id
        local_role = (
            self.StationRole.PRIMARY
            if is_local_primary
            else self.StationRole.SECONDARY
        )
        message = (
            "Conectado · Principal · Sincronizado"
            if is_local_primary
            else "Conectado · Secundaria · Sincronizado"
        )
        self.attachment = DeviceAttachment(
            changed, local_role, True, message
        )
        if self._operational_state is not None:
            from dataclasses import replace

            committed_state = replace(
                self._operational_state,
                primary_device_id=changed.primary_device_id,
                primary_login_session_id=changed.primary_login_session_id,
                device_role=local_role,
                device_attached=True,
                write_allowed=True,
                lease_generation=changed.lease_generation,
                operational_revision=changed.operational_revision,
                reason_code="PRIMARY_TRANSFER_COMMITTED",
                message=message,
                invalidated_reason="",
                sync_state="ONLINE_SYNCED",
            )
            self._operational_state = committed_state
            self._pending_mirror_state = committed_state
        self.offline = False
        self.offline_lease_valid = True
        self.status_message = message
        return changed

    def force_transfer_primary(
        self,
        *,
        target_device_id: str,
        target_login_session_id: str,
        reason: str,
        expected_operational_revision: int | None = None,
    ):
        """Compatibility alias for the former UI entry point."""
        return self.force_transfer_admission_primary(
            target_device_id=target_device_id,
            target_login_session_id=target_login_session_id,
            reason=reason,
            expected_operational_revision=expected_operational_revision,
        )


    def _reattach_matching_device(self, state: Any):
        if state.invalidated_reason == "PRIMARY_TRANSFERRED_ADMINISTRATIVELY":
            return state
        from admission_hybrid import canonical_role, ADMISSION_ROLE_ADMINISTRATOR

        is_admin = canonical_role(self.current_user) == ADMISSION_ROLE_ADMINISTRATOR
        if (
            not self.app_user_can_operate_admission
            or not state.operational_session_id
            or (not state.user_matches_operational and not is_admin)
        ):
            return state

        primary_device_match = state.primary_device_id == self.device_id
        login_rebind_required = bool(
            state.reason_code
            in {"LOGIN_SESSION_MISMATCH", "READONLY_LOGIN_SESSION_STALE"}
            or (
                primary_device_match
                and state.primary_login_session_id != self.login_session_id
            )
        )
        needs_attachment = (
            state.device_role in {self.StationRole.NONE, self.StationRole.DETACHED}
            or not state.device_attached
            or bool(state.invalidated_reason)
            or login_rebind_required
        )
        if not needs_attachment:
            return state

        self.logger.info(
            "OP_REATTACH_CHECK device_id=%s primary_device_match=%s "
            "local_user_id=%s operational_user_id=%s same_user=%s role=%s",
            self.device_id,
            str(primary_device_match).lower(),
            self.user_id,
            state.active_user_id,
            str(bool(state.user_matches_operational)).lower(),
            self.current_user.get("role"),
        )
        self.attachment = self.session_service.rebind_login_session_to_operational_state(
            current_user=self.current_user,
            device_id=self.device_id,
            login_session_id=self.login_session_id,
            device_name=str(getattr(self.host, "device_name", "")),
        )
        self._attachment_from_cache = False
        rebound = self._refresh_authoritative_state(
            reason="automatic_login_reattach",
            enforce_generation=False,
        )
        self.logger.info(
            "OP_REATTACH_SUCCESS role=%s turn_id=%s generation=%s operational_revision=%s",
            rebound.device_role.value,
            rebound.turn_id,
            rebound.generation,
            getattr(rebound, "operational_revision", 1),
        )
        return rebound

    def _heartbeat_if_due(self, attachment: Any, state: Any) -> None:
        from admission_hybrid import HEARTBEAT_INTERVAL_SECONDS

        heartbeat_now = perf_counter()
        if (
            attachment is None
            or not state.device_attached
            or heartbeat_now - self._last_heartbeat_at < HEARTBEAT_INTERVAL_SECONDS
        ):
            return
        self.session_service.heartbeat(
            operational_session_id=(
                attachment.operational_session.operational_session_id
            ),
            device_id=self.device_id,
        )
        self._last_heartbeat_at = heartbeat_now

    def _pull_patient_directory_if_due(self) -> int:
        from admission_hybrid import PATIENT_DIRECTORY_POLL_SECONDS

        patient_poll_now = perf_counter()
        if (
            self.patient_directory is None
            or patient_poll_now - self._last_patient_pull_at
            < PATIENT_DIRECTORY_POLL_SECONDS
        ):
            return 0
        pulled = self.patient_directory.pull_incremental(limit=500)
        self._last_patient_pull_at = patient_poll_now
        if pulled:
            self.logger.info("PATIENT_PULL_BATCH count=%s", pulled)
        return int(pulled or 0)

    def synchronize(self) -> dict[str, Any]:
        with self._lock:
            if self.store is None or self.sync_service is None:
                return self.state()
            try:
                if self.offline:
                    self.connection_supervisor.recover()
                previous_generation = (
                    self.attachment.operational_session.generation
                    if self.attachment is not None
                    else None
                )
                previous_turn = (
                    self.attachment.operational_session.turn_id
                    if self.attachment is not None
                    else None
                )
                previous_operational_revision = (
                    self.attachment.operational_session.operational_revision
                    if self.attachment is not None
                    else None
                )
                self._attach_remote_if_needed()
                if self.app_user_can_operate_admission:
                    latest_state = self._refresh_authoritative_state(
                        reason="heartbeat", enforce_generation=True
                    )
                    latest_state = self._reattach_matching_device(latest_state)
                else:
                    session = self.session_service.get_operational_session()
                    self._set_readonly_operational_state(session)
                    latest_state = self._operational_state
                if latest_state is None:
                    return self.state()
                pull_log = (
                    self.logger.info
                    if previous_generation != latest_state.generation
                    or previous_turn != latest_state.turn_id
                    else self.logger.debug
                )
                pull_log(
                    "OP_STATE_PULL device_role=%s local_generation=%s "
                    "central_generation=%s local_turn=%s central_turn=%s",
                    latest_state.device_role.value,
                    previous_generation,
                    latest_state.generation,
                    previous_turn,
                    latest_state.turn_id,
                )
                authoritative_message = latest_state.message
                central_changed = (
                    previous_generation not in {None, latest_state.generation}
                    or previous_turn not in {None, latest_state.turn_id}
                    or previous_operational_revision
                    not in {None, latest_state.operational_revision}
                )
                mirror_needed = (
                    central_changed
                    or latest_state.generation != self._last_mirrored_generation
                    or latest_state.operational_revision
                       != self._last_mirrored_operational_revision
                    or self._pending_mirror_state is not None
                )
                if mirror_needed:
                    from dataclasses import replace

                    latest_state = replace(
                        latest_state,
                        sync_state="LOCAL_MIRROR_PENDING",
                        message="Conectado · actualizando estado local...",
                    )
                    self._pending_mirror_state = latest_state
                    self.apply_operational_snapshot(latest_state, source="sync_mirror_pending")
                    self.offline_lease_valid = bool(
                        latest_state.write_allowed
                        and self.app_user_can_operate_admission
                    )
                    return {**self.state(), "local_mirror_pending": True}
                self.apply_operational_snapshot(latest_state, source="sync_authoritative")
                if (
                    latest_state.device_role == self.StationRole.DETACHED
                    or latest_state.invalidated_reason
                ):
                    if central_changed:
                        from dataclasses import replace

                        latest_state = replace(
                            latest_state,
                            sync_state="ONLINE_SYNCED",
                            message=authoritative_message,
                        )
                        self._operational_state = latest_state
                        self.status_message = authoritative_message
                    self.offline = False
                    self.offline_lease_valid = False
                    return self.state()
                self._heartbeat_if_due(self.attachment, latest_state)
                result = self.sync_service.synchronize_once(
                    operational_source_id=latest_state.operational_source_id,
                    turn_id=int(latest_state.turn_id or 0),
                    device_id=self.device_id,
                    reason="timer",
                )
                result["reconciled"] = self.sync_service.reconcile_current_turn(
                    operational_source_id=latest_state.operational_source_id,
                    turn_id=latest_state.turn_id,
                    # The incremental stream already materializes new events.
                    # A full current-turn read is needed once per identity, not
                    # after every push/pull or operational revision refresh.
                    force=False,
                )
                patient_pulled = self._pull_patient_directory_if_due()
                result["patient_pulled"] = patient_pulled
                if any(
                    int(result.get(name) or 0) > 0
                    for name in (
                        "pushed",
                        "pulled",
                        "recovered",
                        "replayed",
                        "reconciled",
                    )
                ):
                    self.logger.info(
                        "ADMISSION_SYNC_METRICS SYNC_PUSH_MS=%.1f "
                        "SYNC_PULL_MS=%.1f SYNC_APPLY_MS=%.1f SYNC_TOTAL_MS=%.1f",
                        float(result.get("sync_push_ms") or 0.0),
                        float(result.get("sync_pull_ms") or 0.0),
                        float(result.get("sync_apply_ms") or 0.0),
                        float(result.get("sync_total_ms") or 0.0),
                    )
                self._pending_sync_count = self.store.pending_count()
                self.offline = False
                self.connection_supervisor.mark_synced()
                self.offline_lease_valid = bool(
                    latest_state.write_allowed
                    and self.app_user_can_operate_admission
                )
                if central_changed:
                    from dataclasses import replace

                    latest_state = replace(
                        latest_state,
                        sync_state="ONLINE_SYNCED",
                        message=authoritative_message,
                    )
                    self._operational_state = latest_state
                self.status_message = (
                    authoritative_message
                    if central_changed
                    else (
                        self.attachment.message
                        if self.attachment and self.attachment.message
                        else "Sincronizado."
                    )
                )
                return {**self.state(), **result}
            except Exception as exc:
                if not self._temporary(exc):
                    raise
                self.connection_supervisor.mark_offline(exc)
                cached = self.store.cached_attachment()
                # A replica may lag or even contain an older turn. Once a
                # central snapshot has been adopted, a network failure may
                # mark it stale but must never replace its identity.
                if self.attachment is None and cached is not None:
                    if self.app_user_can_operate_admission:
                        self.attachment = cached
                        self.offline_lease_valid = bool(cached.writable)
                    else:
                        self._set_auxiliary_operational_state(
                            cached.operational_session
                        )
                if self._operational_state is not None:
                    from dataclasses import replace
                    from admission_hybrid import ConnectivityState

                    self._operational_state = replace(
                        self._operational_state,
                        connection_state=ConnectivityState.OFFLINE,
                        sync_state="STALE_OPERATIONAL_SNAPSHOT",
                        reason_code="CENTRAL_TEMPORARILY_UNAVAILABLE",
                        message="Conexión temporalmente no verificada.",
                    )
                self.offline = True
                self.status_message = (
                    "Conexión temporalmente no verificada · Admisión en solo lectura"
                    if not self.app_user_can_operate_admission
                    else "Conexión temporalmente no verificada · trabajando localmente"
                )
                return self.state()

    def get_attention_by_global_id(
        self,
        global_attention_id: str,
        *,
        force_central: bool = False,
        include_deleted: bool = True,
    ) -> dict[str, Any] | None:
        """Reads through cloud on a local miss and hydrates the V15 mirror."""
        if self.sync_service is None:
            return None
        return self.sync_service.get_attention_by_global_id(
            global_attention_id,
            online=not self.offline,
            force_central=force_central,
            include_deleted=include_deleted,
        )

    def cancel_admission_attention(
        self, global_attention_id: str, *, reason: str
    ) -> dict[str, Any] | None:
        """Canonical cancellation entry point used by both station roles."""
        self.require_write()
        session = self.operational_session
        if session is None or self.sync_service is None:
            return None
        result = self.sync_service.cancel_attention(
            global_attention_id,
            current_user=self.current_user,
            reason=reason,
            operational_session=session,
            device_id=self.device_id,
            online=not self.offline,
        )
        if self.store is not None:
            self._pending_sync_count = self.store.pending_count()
        return result


    def state(self) -> dict[str, Any]:
        session = self.operational_session
        result = (
            self._operational_state.as_mapping()
            if self._operational_state is not None
            else {
                "role": self.role.value,
                "writable": self.writable,
                "active_username": session.active_username if session else "",
                "generation": session.generation if session else 0,
                "operational_revision": (
                    session.operational_revision if session else 0
                ),
            }
        )
        invalidated_reason = str(
            getattr(self._operational_state, "invalidated_reason", "") or ""
        )
        # Una corrección manual de representante no expulsa estaciones. A
        # diferencia de ella, un relevo formal de turno sí invalida de forma
        # explícita la secundaria del responsable saliente.
        force_logout = bool(
            self.role == self.StationRole.DETACHED
            and self.login_session_id
            and invalidated_reason in {
                "PRIMARY_TRANSFERRED_ADMINISTRATIVELY",
                "PRIMARY_USER_CHANGED",
            }
        )
        result.update({
            "role": self.role.value,
            "writable": bool(
                self.app_user_can_operate_admission
                and self.writable
                and (not self.offline or self.offline_lease_valid)
            ),
            "offline": self.offline,
            "message": self.status_message,
            "active_username": session.active_username if session else "",
            "active_user_display_name": (
                session.active_user_display_name if session else ""
            ),
            "generation": session.generation if session else 0,
            "operational_revision": (
                session.operational_revision if session else 0
            ),
            "pending_sync_count": self._pending_sync_count,
            "force_logout_required": force_logout,
        })
        return result

    def shutdown(self) -> None:
        """Detach a secondary asynchronously; shutdown never waits on sync/network."""
        if self._shutdown_started:
            return
        self._shutdown_started = True
        session = self.operational_session
        role = self.role
        if session is None or role != self.StationRole.SECONDARY:
            return
        operational_session_id = str(session.operational_session_id or "")
        device_id = str(self.device_id or "")
        service = self.session_service
        logger = self.logger

        def detach_secondary():
            try:
                service.detach_device(
                    operational_session_id=operational_session_id,
                    device_id=device_id,
                )
            except Exception:
                logger.exception("No se pudo separar la estación secundaria")

        threading.Thread(
            target=detach_secondary,
            name="admission-secondary-detach",
            daemon=True,
        ).start()


class _HybridDatabaseProxy:
    # V15 uses an integer SQLite turn identifier in its legacy dialogs. It is
    # not portable across computers, so online history resolves "Este turno"
    # against OperationalState instead of requiring that local ID.
    uses_central_history = True

    _primary_methods: ClassVar[set[str]] = {
        "cerrar_turno_existente",
        "actualizar_representante_turno",
        "notify_shift_changed",
    }
    _write_prefixes = (
        "guardar_", "actualizar_", "borrar_", "eliminar_", "reemplazar_",
        "normalizar_", "resolver_", "restaurar_", "registrar_", "limpiar_",
    )
    _history_methods: ClassVar[set[str]] = {
        "listar_atenciones",
        "listar_atenciones_filtradas",
        "listar_atenciones_sin_seguro",
    }

    def __init__(self, database: Any, runtime: _HybridAdmissionRuntime):
        object.__setattr__(self, "_database", database)
        object.__setattr__(self, "_runtime", runtime)
        object.__setattr__(self, "_last_transition_result", None)
        object.__setattr__(self, "_summary_lock", threading.RLock())
        object.__setattr__(self, "_last_known_good_summary", None)
        object.__setattr__(self, "_last_good_turn_rows", None)
        object.__setattr__(self, "_turn_summary", {
            "total": 0,
            "sin_seguro": 0,
            "GENERAL": 0,
            "PEDIATRIA": 0,
            "GINECOLOGIA": 0,
            "URGENCIAS": 0,
            "CONSULTAS": 0,
            "_status": "INVALID_REFRESH",
            "_error_code": "SUMMARY_NOT_LOADED",
            "_fuente": "LAST_KNOWN_GOOD",
        })

    @property
    def last_transition_result(self):
        return object.__getattribute__(self, "_last_transition_result")

    def get_operational_station_snapshot(self) -> dict[str, Any]:
        """Snapshot ya adoptado en memoria; nunca consulta red ni SQLite."""
        return dict(self._runtime.state())

    def resumen_turno_actual(self) -> dict[str, Any]:
        """Returns the latest snapshot without blocking the Qt event loop."""
        with self._summary_lock:
            return dict(self._turn_summary)

    def refresh_turn_summary(self, reason: str = "background_refresh") -> dict[str, Any]:
        """Apply counts only when a dataset has positive identity/query evidence."""
        started = perf_counter()
        reason = str(reason or "background_refresh")
        identity = _operational_identity_tuple(self._runtime.operational_session)
        logger = getattr(self._runtime, "logger", None)
        with self._summary_lock:
            last_good = dict(self._last_known_good_summary or {})
        if logger is not None:
            logger.info(
                "TURN_SUMMARY_REFRESH_START turn_id=%s operational_source_id=%s "
                "generation=%s operational_revision=%s connection_state=%s "
                "last_valid_total=%s refresh_reason=%s device_id=%s",
                identity[1] if identity else None,
                identity[0] if identity else "-",
                identity[2] if identity else 0,
                identity[3] if identity else 0,
                "OFFLINE" if bool(getattr(self._runtime, "offline", False)) else "ONLINE",
                last_good.get("total", "-"),
                reason,
                str(getattr(self._runtime, "device_id", "") or "-"),
            )
        if identity is None:
            return self._stale_turn_summary(
                error_code="IDENTITY_UNAVAILABLE", started=started, reason=reason
            )

        result = self.load_turn_dataset_result(identity=identity)
        if logger is not None:
            logger.info(
                "TURN_SUMMARY_DATASET_RESULT status=%s rows=%s dataset_source=%s "
                "turn_id=%s operational_source_id=%s generation=%s "
                "operational_revision=%s central_count=%s local_count=%s "
                "pending_count=%s display_count=%s error_code=%s",
                result.status,
                result.display_count,
                result.source,
                result.turn_id,
                result.operational_source_id or "-",
                result.generation,
                result.operational_revision,
                result.central_count,
                result.local_count,
                result.pending_count,
                result.display_count,
                result.error_code or "-",
            )
        if not result.is_valid:
            return self._stale_turn_summary(
                error_code=result.error_code or result.status,
                started=started,
                identity=identity,
                reason=reason,
            )
        if _operational_identity_tuple(self._runtime.operational_session) != identity:
            return self._stale_turn_summary(
                error_code="STALE_OPERATIONAL_SNAPSHOT",
                started=started,
                identity=identity,
                reason=reason,
            )

        counts = self._calculate_turn_counts(result.rows)
        old_total = int(last_good.get("total") or 0)
        same_last_identity = self._summary_matches_identity(last_good, identity)
        if same_last_identity and old_total > 0 and counts["total"] == 0:
            zero_confirmed, zero_error = self._confirm_central_zero(identity)
            if not zero_confirmed:
                return self._stale_turn_summary(
                    error_code=zero_error,
                    started=started,
                    identity=identity,
                    reason=reason,
                )

        summary = TurnSummarySnapshot(
            operational_source_id=identity[0],
            turn_id=identity[1],
            generation=identity[2],
            operational_revision=identity[3],
            counts=counts,
            refreshed_at=result.generated_at,
            status=result.status,
        ).to_mapping()
        summary.update(
            {
                "_fuente": result.source,
                "_central_count": result.central_count,
                "_local_count": result.local_count,
                "_pending_count": result.pending_count,
                "_display_count": result.display_count,
                "_refresh_reason": reason,
            }
        )
        with self._summary_lock:
            object.__setattr__(self, "_last_known_good_summary", dict(summary))
            object.__setattr__(self, "_last_good_turn_rows", (identity, result.rows))
            object.__setattr__(self, "_turn_summary", dict(summary))
        if logger is not None:
            if not same_last_identity or old_total != counts["total"]:
                logger.info(
                    "TURN_SUMMARY_TOTAL_CHANGED old_total=%s new_total=%s turn_id=%s "
                    "operational_source_id=%s generation=%s reason=%s",
                    old_total if same_last_identity else "NEW_TURN",
                    counts["total"],
                    identity[1],
                    identity[0],
                    identity[2],
                    reason,
                )
            logger.info(
                "TURN_SUMMARY_APPLY status=%s count=%s source=%s turn_id=%s "
                "operational_source_id=%s generation=%s operational_revision=%s "
                "elapsed_ms=%.1f",
                result.status,
                counts["total"],
                result.source,
                identity[1],
                identity[0],
                identity[2],
                identity[3],
                (perf_counter() - started) * 1000.0,
            )
        return summary

    @staticmethod
    def _calculate_turn_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        counts = {
            "total": 0,
            "sin_seguro": 0,
            "GENERAL": 0,
            "PEDIATRIA": 0,
            "GINECOLOGIA": 0,
            "URGENCIAS": 0,
            "CONSULTAS": 0,
        }
        for row in rows:
            counts["total"] += 1
            attention_type = str(
                row.get("tipo_atencion") or row.get("service_type") or "EMERGENCIA"
            ).upper()
            if attention_type == "URGENCIA":
                counts["URGENCIAS"] += 1
            elif attention_type == "CONSULTA":
                counts["CONSULTAS"] += 1
            else:
                specialty = str(
                    row.get("hoja_normalizada")
                    or row.get("specialty")
                    or row.get("hoja")
                    or "GENERAL"
                ).upper()
                key = (
                    "PEDIATRIA" if "PED" in specialty
                    else "GINECOLOGIA" if "GINE" in specialty
                    else "GENERAL"
                )
                counts[key] += 1
            ars = str(
                row.get("ars_display") or row.get("canonical_ars") or row.get("ars") or ""
            ).upper()
            if ars in {"", "SIN SEGURO"}:
                counts["sin_seguro"] += 1
        return counts

    @staticmethod
    def _summary_matches_identity(
        summary: Mapping[str, Any], identity: tuple[str, int, int, int]
    ) -> bool:
        try:
            return (
                str(summary.get("_operational_source_id") or ""),
                int(summary.get("_turn_id") or 0),
                int(summary.get("_generation") or 0),
                int(summary.get("_operational_revision") or 0),
            ) == identity
        except (TypeError, ValueError):
            return False

    def _stale_turn_summary(
        self,
        *,
        error_code: str,
        started: float,
        identity: tuple[str, int, int, int] | None = None,
        reason: str = "background_refresh",
    ) -> dict[str, Any]:
        """Keep the last valid counts when an identity/query refresh is invalid."""
        with self._summary_lock:
            previous = dict(self._last_known_good_summary or self._turn_summary)
        had_valid = bool(self._last_known_good_summary)
        # Counts and identity are one immutable snapshot.  If the current
        # turn changed while a refresh was failing, retaining the old counts
        # under the new identity would leak the previous turn's total into
        # the new one.  Keep the whole last-valid snapshot together; the GUI
        # identity guard will discard it when the runtime already moved on.
        fallback_identity = None
        if had_valid and previous.get("_turn_id"):
            fallback_identity = (
                str(previous.get("_operational_source_id") or ""),
                int(previous.get("_turn_id") or 0),
                int(previous.get("_generation") or 0),
                int(previous.get("_operational_revision") or 0),
            )
        elif identity is not None:
            fallback_identity = identity
        source_id, turn_id, generation, revision = fallback_identity or ("", 0, 0, 0)
        previous.update(
            {
                "_operational_source_id": source_id,
                "_turn_id": turn_id or None,
                "_generation": generation,
                "_operational_revision": revision,
                "_refreshed_at": datetime.now(timezone.utc).isoformat(),
                "_status": "STALE" if had_valid else "INVALID_REFRESH",
                "_error_code": str(error_code or "INVALID_DATASET_STATE"),
                "_fuente": "LAST_KNOWN_GOOD",
                "_refresh_reason": str(reason or "background_refresh"),
            }
        )
        with self._summary_lock:
            object.__setattr__(self, "_turn_summary", dict(previous))
        logger = getattr(self._runtime, "logger", None)
        if logger is not None:
            logger.warning(
                "TURN_SUMMARY_REJECTED turn_id=%s operational_source_id=%s "
                "generation=%s operational_revision=%s status=%s count=%s "
                "source=LAST_KNOWN_GOOD error_code=%s refresh_reason=%s elapsed_ms=%.1f",
                turn_id or None,
                source_id or "-",
                generation,
                revision,
                previous["_status"],
                int(previous.get("total") or 0),
                previous["_error_code"],
                str(reason or "background_refresh"),
                (perf_counter() - started) * 1000.0,
            )
        return previous

    @staticmethod
    def _history_sort_key(row: Mapping[str, Any]):
        from admission_hybrid import build_admission_order_key

        data = dict(row)
        effective = str(data.get("created_at_effective_utc") or "")
        if not effective:
            effective = f"{data.get('fecha') or ''}T{data.get('hora') or '00:00:00'}"
        return build_admission_order_key({
            "created_at_effective_utc": effective,
            "origin_device_id": data.get("origin_device_id"),
            "device_local_sequence": data.get("device_local_sequence"),
            "global_attention_id": data.get("global_attention_id"),
        })

    def list_history_cache_local(
        self,
        method_name: str,
        **values: Any,
    ) -> list[dict[str, Any]]:
        """Return the already synchronized SQLite cache without touching PostgreSQL."""
        if method_name not in self._history_methods:
            raise ValueError(f"Método de historial local no permitido: {method_name}")
        mode = str(values.get("modo") or "Todos")
        session = self._runtime.operational_session
        if mode in {"Este turno", "Turno actual", "Por turno"}:
            turn_id = (
                values.get("turno_id")
                if mode == "Por turno"
                else getattr(session, "turn_id", None)
            )
            source_id = str(
                values.get("operational_source_id")
                or getattr(session, "operational_source_id", "")
                or ""
            )
            if turn_id is None or not source_id:
                return []
            rows = self._local_list_rows(
                int(turn_id), source_id, pending_only=False
            )
            query = str(values.get("filtro_texto") or "").strip().casefold()
            if query:
                rows = [
                    row for row in rows
                    if query in " ".join(
                        str(row.get(key) or "")
                        for key in ("nombre", "ars", "nss", "cedula", "id")
                    ).casefold()
                ]
            specialty = str(values.get("especialidad") or "").strip().upper()
            if mode == "Por especialidad" and specialty not in {"", "(TODAS)"}:
                rows = [
                    row for row in rows
                    if str(row.get("hoja") or row.get("specialty") or "").upper()
                    == specialty
                ]
            rows = sorted(rows, key=self._history_sort_key, reverse=True)
            offset = max(0, int(values.get("offset") or 0))
            limit = max(1, min(int(values.get("limite") or 200), 500))
            return rows[offset:offset + limit]

        local_method = getattr(self._database, method_name)
        accepted_names = (
            ("filtro_texto", "limite", "offset")
            if method_name != "listar_atenciones_filtradas"
            else (
                "filtro_texto", "modo", "ars", "especialidad", "fecha_txt",
                "limite", "offset", "turno_id",
            )
        )
        kwargs = {key: values[key] for key in accepted_names if key in values}
        return [dict(row) for row in (local_method(**kwargs) or [])]

    def _local_pending_history(self, rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        local_rows = [dict(row) for row in rows]
        identities = [int(row.get("id") or 0) for row in local_rows if row.get("id")]
        if not identities:
            return []
        placeholders = ",".join("?" for _ in identities)
        with self._database._connect() as con:
            pending = con.execute(
                f"""SELECT a.id,a.global_attention_id,a.origin_device_id,
                           a.device_local_sequence,a.created_at_effective_utc
                    FROM atenciones a
                    WHERE a.id IN ({placeholders})
                      AND EXISTS(
                          SELECT 1 FROM sync_outbox o
                          WHERE o.entity_type='attention'
                            AND o.entity_uuid=a.global_attention_id
                            AND o.sync_status IN ('PENDING','RETRY')
                      )""",
                identities,
            ).fetchall()
        metadata_by_id = {
            int(item[0]): {
                "global_attention_id": str(item[1] or ""),
                "origin_device_id": str(item[2] or ""),
                "device_local_sequence": int(item[3] or 0),
                "created_at_effective_utc": str(item[4] or ""),
            }
            for item in pending
        }
        result = []
        for row in local_rows:
            metadata = metadata_by_id.get(int(row.get("id") or 0))
            if not metadata:
                continue
            row.update(metadata)
            row["sync_state"] = "PENDING"
            result.append(row)
        return result

    def _append_central_turn_filters(
        self,
        values: Mapping[str, Any],
        mode: str,
        where: list[str],
        params: list[Any],
    ) -> None:
        turn_id = values.get("turno_id")
        if mode in {"Este turno", "Turno actual"}:
            session = self._runtime.operational_session
            operational_source_id = str(
                getattr(session, "operational_source_id", "") or ""
            )
            central_turn_id = getattr(session, "turn_id", None)
            operational_session_id = str(
                getattr(session, "operational_session_id", "") or ""
            )
            generation = int(getattr(session, "generation", 0) or 0)
            if operational_source_id and central_turn_id is not None:
                where.extend(("p.operational_source_id::TEXT=%s", "p.turn_id=%s"))
                params.extend((operational_source_id, int(central_turn_id)))
                turn_id = None
            elif operational_session_id and generation:
                where.extend(("p.operational_session_id=%s", "p.generation=%s"))
                params.extend((operational_session_id, generation))
                turn_id = None
            elif not turn_id:
                turn_id = session.turn_id if session else None
        if not turn_id:
            return
        requested_source = str(values.get("operational_source_id") or "").strip()
        if requested_source:
            where.append("p.operational_source_id::TEXT=%s")
            params.append(requested_source)
        where.append("p.turn_id=%s")
        params.append(int(turn_id))

    def _pending_history_rows(
        self,
        method_name: str,
        names: Iterable[str],
        values: Mapping[str, Any],
        limit: int,
        offset: int,
        logger: Any,
    ) -> list[dict[str, Any]]:
        local_values = dict(values)
        local_values["limite"] = limit + offset
        local_values["offset"] = 0
        accepted = set(names) | {"operational_source_id"}
        local_values = {
            key: value for key, value in local_values.items() if key in accepted
        }
        local_method = getattr(self._database, method_name, None)
        try:
            if not callable(local_method):
                return []
            return self._local_pending_history(
                self.list_history_cache_local(method_name, **local_values)
            )
        except Exception:
            if logger is not None:
                logger.exception("HISTORY_LOCAL_PENDING_READ_FAILED")
            return []

    def _legacy_projection_readthrough(
        self, method_name: str, args, kwargs
    ) -> list[dict[str, Any]]:
        started = perf_counter()
        logger = getattr(self._runtime, "logger", None)
        if logger is not None:
            logger.info("HISTORY_QUERY_START method=%s", method_name)
        if self._runtime.offline:
            local_method = getattr(self._database, method_name)
            return sorted(
                local_method(*args, **kwargs) or [],
                key=self._history_sort_key,
                reverse=True,
            )

        values = dict(kwargs or {})
        positional = list(args or ())
        names = (
            ("filtro_texto", "limite", "offset")
            if method_name != "listar_atenciones_filtradas"
            else (
                "filtro_texto", "modo", "ars", "especialidad", "fecha_txt",
                "limite", "offset", "turno_id",
            )
        )
        for name, value in zip(names, positional):
            values.setdefault(name, value)
        limit = max(1, min(int(values.get("limite") or 200), 500))
        offset = max(0, int(values.get("offset") or 0))
        where = [
            "COALESCE(p.is_deleted,FALSE)=FALSE",
            "UPPER(TRIM(COALESCE(p.source_status,'ACTIVA'))) IN ('ACTIVA','PENDIENTE')",
        ]
        params: list[Any] = []
        query = str(values.get("filtro_texto") or "").strip()
        if query:
            digits = re.sub(r"\D", "", query)
            where.append(
                "(p.patient_name ILIKE %s OR p.canonical_ars ILIKE %s "
                "OR p.nss_snapshot ILIKE %s OR p.cedula_snapshot ILIKE %s "
                "OR CAST(p.attention_id AS TEXT)=%s)"
            )
            like = f"%{query}%"
            params.extend((like, like, f"%{digits or query}%", f"%{digits or query}%", digits or query))
        mode = str(values.get("modo") or "Todos")
        if method_name == "listar_atenciones_sin_seguro" or mode == "Sin seguro":
            where.append("p.coverage_status='UNINSURED_DECLARED'")
        if mode == "Hoy":
            where.append("p.service_date=CURRENT_DATE::TEXT")
        if mode == "Por fecha" and values.get("fecha_txt"):
            raw_date = str(values["fecha_txt"])
            parts = re.findall(r"\d+", raw_date)
            if len(parts) == 3:
                where.append("p.service_date=%s")
                params.append(f"{int(parts[2]):04d}-{int(parts[1]):02d}-{int(parts[0]):02d}")
        specialty = str(values.get("especialidad") or "").strip()
        if mode == "Por especialidad" and specialty and specialty != "(TODAS)":
            where.append("UPPER(COALESCE(p.specialty,''))=UPPER(%s)")
            params.append(specialty)
        ars = str(values.get("ars") or "").strip()
        if mode == "Por ARS" and ars and ars.casefold() != "(todas)":
            where.append("p.canonical_ars ILIKE %s")
            params.append(f"%{ars}%")
        self._append_central_turn_filters(values, mode, where, params)

        sql = f"""SELECT p.*,p.attention_id AS origin_attention_id,
                          p.attention_id AS id,p.service_date AS fecha,
                          p.service_time AS hora,p.patient_name AS nombre,
                          CASE WHEN p.has_detail_sheet THEN COALESCE(NULLIF(p.specialty,''),'GENERAL')
                               ELSE '' END AS hoja,
                          p.canonical_ars AS ars,p.nss_snapshot AS nss,
                          p.cedula_snapshot AS cedula,'' AS edad_num,'' AS unidad,
                          p.service_type AS tipo_atencion,p.global_attention_id,
                          p.created_at_effective_utc,p.origin_device_id,
                          p.device_local_sequence,'SYNCHRONIZED' AS sync_state,
                          latest.event_uuid::TEXT AS latest_event_uuid,
                          latest.sequence AS latest_sequence,
                          latest.operation AS latest_operation,
                          latest.payload_json AS latest_payload
                   FROM admission_attention_projection p
                   LEFT JOIN LATERAL (
                       SELECT e.event_uuid,e.sequence,e.operation,e.payload_json
                         FROM admission_sync_events e
                        WHERE e.entity_type='attention'
                          AND e.entity_uuid=p.global_attention_id
                        ORDER BY e.sequence DESC LIMIT 1
                   ) latest ON TRUE
                   WHERE {' AND '.join(where)}
                   ORDER BY COALESCE(
                                p.created_at_effective_utc,
                                NULLIF(p.synced_at,'')::TIMESTAMPTZ
                            ) DESC,
                            COALESCE(p.origin_device_id,'') DESC,
                            COALESCE(p.device_local_sequence,0) DESC,
                            COALESCE(p.global_attention_id::TEXT,p.attention_id::TEXT) DESC
                   LIMIT %s OFFSET %s"""
        params.extend((limit + offset, 0))
        with self._runtime.host.connection_factory() as con:
            cloud_rows = [dict(row) for row in con.execute(sql, tuple(params)).fetchall()]
        if logger is not None:
            logger.info(
                "HISTORY_CENTRAL_READ method=%s rows=%s source=CENTRAL",
                method_name,
                len(cloud_rows),
            )

        # V15 still calls several local-id APIs. Materialize every central row
        # first, then replace the origin PC integer with this PC's exact local ID.
        store = getattr(self._runtime, "store", None)
        if cloud_rows and store is not None:
            from admission_hybrid import AdmissionCloudRepository

            hydration_events = [
                AdmissionCloudRepository._readthrough_event(row)
                for row in cloud_rows
            ]
            try:
                store.hydrate_remote_events(hydration_events)
                local_ids = store.local_attention_ids(
                    row.get("global_attention_id") for row in cloud_rows
                )
                for row in cloud_rows:
                    key = str(row.get("global_attention_id") or "").replace(
                        "-", ""
                    ).lower()
                    local_id = local_ids.get(key)
                    if local_id is None:
                        raise RuntimeError(
                            "No se pudo hidratar la identidad global en la replica local."
                        )
                    row["id"] = local_id
                    row["local_attention_id"] = local_id
            except Exception:
                # A read-through failure (for example, a freshly installed
                # secondary whose local turn mirror is still being created)
                # must not hide the central history.  The mirror coordinator
                # retries independently; the visible cloud rows remain valid.
                if logger is not None:
                    logger.exception(
                        "HISTORY_READTHROUGH_HYDRATION_FAILED rows=%s",
                        len(cloud_rows),
                    )

        # Native V15 data is supplemental and only keeps unacknowledged rows visible.
        pending_rows = self._pending_history_rows(
            method_name, names, values, limit, offset, logger
        )
        if logger is not None:
            logger.info(
                "HISTORY_LOCAL_PENDING_COUNT method=%s rows=%s",
                method_name,
                len(pending_rows),
            )
        by_uuid = {
            str(row.get("global_attention_id") or "").replace("-", "").lower(): row
            for row in cloud_rows
            if row.get("global_attention_id")
        }
        for row in pending_rows:
            key = str(row.get("global_attention_id") or "").replace("-", "").lower()
            if key and key not in by_uuid:
                by_uuid[key] = row
        merged = sorted(by_uuid.values(), key=self._history_sort_key, reverse=True)
        result = merged[offset:offset + limit]
        if logger is not None:
            estimated_bytes = sum(
                len(str(row).encode("utf-8", errors="replace")) for row in cloud_rows
            )
            logger.info(
                "HISTORY_SYNC_DONE method=%s rows=%s cloud_rows=%s "
                "estimated_bytes=%s elapsed_ms=%.1f",
                method_name,
                len(result),
                len(cloud_rows),
                estimated_bytes,
                (perf_counter() - started) * 1000.0,
            )
            logger.info(
                "HISTORY_REFRESH method=%s rows=%s", method_name, len(result)
            )
            logger.info(
                "HISTORY_RESULT_COUNT method=%s rows=%s source=%s",
                method_name,
                len(result),
                "CENTRAL_PLUS_PENDING" if pending_rows else "CENTRAL",
            )
        return result

    def _cloud_history(self, method_name: str, args, kwargs) -> list[dict[str, Any]]:
        """Read PostgreSQL online and use SQLite only as the offline replica."""
        started = perf_counter()
        logger = getattr(self._runtime, "logger", None)
        values = dict(kwargs or {})
        positional = list(args or ())
        names = (
            ("filtro_texto", "limite", "offset")
            if method_name != "listar_atenciones_filtradas"
            else (
                "filtro_texto", "modo", "ars", "especialidad", "fecha_txt",
                "limite", "offset", "turno_id",
            )
        )
        for name, value in zip(names, positional):
            values.setdefault(name, value)
        if not bool(getattr(self._runtime, "offline", False)):
            if logger is not None:
                logger.info("HISTORY_CENTRAL_QUERY method=%s", method_name)
            try:
                return self._legacy_projection_readthrough(
                    method_name, args, kwargs
                )
            except Exception as exc:
                temporary = getattr(self._runtime, "_temporary", lambda _exc: False)
                if not temporary(exc):
                    raise
                supervisor = getattr(
                    self._runtime, "connection_supervisor", None
                )
                if supervisor is not None:
                    supervisor.mark_offline(exc)
                self._runtime.offline = True
                self._runtime.status_message = (
                    "Sin conexión · trabajando con la réplica local"
                )
                if logger is not None:
                    logger.warning(
                        "HISTORY_OFFLINE_FALLBACK method=%s error=%s",
                        method_name,
                        type(exc).__name__,
                    )

        rows = sorted(
            self.list_history_cache_local(method_name, **values),
            key=self._history_sort_key,
            reverse=True,
        )
        if logger is not None:
            logger.info(
                "HISTORY_REFRESH method=%s rows=%s source=OFFLINE_LOCAL "
                "elapsed_ms=%.1f",
                method_name,
                len(rows),
                (perf_counter() - started) * 1000.0,
            )
        return rows

    def _local_list_rows(
        self,
        turn_id: int,
        operational_source_id: str,
        *,
        pending_only: bool,
    ) -> list[dict[str, Any]]:
        """Filas SQLite del turno con la identidad/orden durable del outbox."""
        rows = list(
            self._database.obtener_atenciones_para_rango_real(
                operational_turn_id=int(turn_id),
                operational_source_id=str(operational_source_id),
            ) or []
        )
        if not rows:
            return []
        identities = [int(row.get("id") or 0) for row in rows]
        placeholders = ",".join("?" for _ in identities)
        with self._database._connect() as con:
            metadata = con.execute(
                f"""SELECT a.id,a.global_attention_id,a.origin_device_id,
                           a.device_local_sequence,a.created_at_effective_utc,
                           CASE WHEN EXISTS(
                               SELECT 1 FROM sync_outbox o
                               WHERE o.entity_type='attention'
                                 AND o.entity_uuid=a.global_attention_id
                                 AND o.sync_status IN ('PENDING','RETRY')
                           ) THEN 1 ELSE 0 END AS pending_sync
                    FROM atenciones a WHERE a.id IN ({placeholders})""",
                identities,
            ).fetchall()
        by_id = {
            int(item[0]): {
                "global_attention_id": str(item[1] or ""),
                "origin_device_id": str(item[2] or ""),
                "device_local_sequence": int(item[3] or 0),
                "created_at_effective_utc": str(item[4] or ""),
                "pending_sync": bool(item[5]),
            }
            for item in metadata
        }
        result = []
        for raw in rows:
            row = dict(raw)
            row.update(by_id.get(int(row.get("id") or 0), {}))
            if pending_only and not row.get("pending_sync"):
                continue
            row["attention_id"] = int(row.get("id") or 0)
            row["specialty"] = str(
                row.get("hoja_normalizada") or row.get("hoja") or "GENERAL"
            ).strip().upper()
            row["canonical_ars"] = str(
                row.get("ars_display") or row.get("ars") or "SIN SEGURO"
            ).strip()
            result.append(row)
        return result

    @staticmethod
    def _list_identity(row: Mapping[str, Any]) -> str:
        global_id = str(row.get("global_attention_id") or "").replace("-", "").lower()
        return global_id or f"legacy:{row.get('source_instance_id') or ''}:{row.get('attention_id') or row.get('id')}"

    @staticmethod
    def _merge_turn_rows(
        primary: Iterable[Mapping[str, Any]],
        supplemental: Iterable[Mapping[str, Any]],
        deleted_identities: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        deleted = deleted_identities or set()
        merged: dict[str, dict[str, Any]] = {}
        for raw in (*tuple(primary), *tuple(supplemental)):
            row = dict(raw)
            identity = _HybridDatabaseProxy._list_identity(row)
            if identity and identity not in deleted:
                merged[identity] = row
        return sorted(merged.values(), key=_HybridDatabaseProxy._history_sort_key)

    def _local_pending_delete_identities(
        self, turn_id: int, operational_source_id: str
    ) -> set[str]:
        """Pending tombstones must hide a central row while offline sync catches up."""
        try:
            with self._database._connect() as connection:
                rows = connection.execute(
                    """SELECT entity_uuid FROM sync_outbox
                         WHERE entity_type='attention'
                           AND operation='DELETE'
                           AND sync_status IN ('PENDING','RETRY')
                           AND operational_source_id=? AND turn_id=?""",
                    (str(operational_source_id), int(turn_id)),
                ).fetchall()
        except Exception:  # noqa: BLE001 - old replicas may not expose tombstone columns
            return set()
        return {
            str(row[0] if not isinstance(row, Mapping) else row.get("entity_uuid") or "")
            .replace("-", "")
            .lower()
            for row in rows
            if row
        }

    def _load_central_turn_rows(
        self, turn_id: int, operational_source_id: str
    ) -> list[dict[str, Any]]:
        sql = """SELECT p.*,p.attention_id AS origin_attention_id,
                        p.attention_id AS id,p.service_date AS fecha,
                        p.service_time AS hora,p.patient_name AS nombre,
                        CASE WHEN p.has_detail_sheet
                             THEN COALESCE(NULLIF(p.specialty,''),'GENERAL')
                             ELSE '' END AS hoja,
                        p.specialty AS hoja_normalizada,
                        p.canonical_ars AS ars,p.canonical_ars AS ars_display,
                        p.nss_snapshot AS nss,p.cedula_snapshot AS cedula,
                        p.service_type AS tipo_atencion,
                        'SYNCHRONIZED' AS sync_state
                   FROM admission_attention_projection p
                  WHERE p.operational_source_id::TEXT=%s
                    AND p.turn_id=%s
                    AND COALESCE(p.is_deleted,FALSE)=FALSE
                    AND UPPER(TRIM(COALESCE(p.source_status,'ACTIVA')))
                        IN ('ACTIVA','PENDIENTE')
                  ORDER BY COALESCE(
                               p.created_at_effective_utc,
                               NULLIF(p.synced_at,'')::TIMESTAMPTZ
                           ),
                           COALESCE(p.origin_device_id,''),
                           COALESCE(p.device_local_sequence,0),
                           COALESCE(p.global_attention_id::TEXT,p.attention_id::TEXT)"""
        with self._runtime.host.connection_factory() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    sql, (str(operational_source_id), int(turn_id))
                ).fetchall()
            ]

    def _dataset_error_status(self, exc: Exception) -> str:
        temporary = getattr(self._runtime, "_temporary", None)
        try:
            if callable(temporary) and temporary(exc):
                return "DATABASE_UNAVAILABLE"
        except Exception:  # noqa: BLE001 - classification must never mask the failure
            pass
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return "DATABASE_UNAVAILABLE"
        return "QUERY_ERROR"

    def _load_local_turn_evidence(
        self, identity: tuple[str, int, int, int]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str], str]:
        source_id, turn_id, _generation, _revision = identity
        try:
            local_rows = self._local_list_rows(
                turn_id, source_id, pending_only=False
            )
            pending_rows = [row for row in local_rows if row.get("pending_sync")]
            deleted = self._local_pending_delete_identities(turn_id, source_id)
            return local_rows, pending_rows, deleted, ""
        except Exception as exc:  # noqa: BLE001 - returned as data, never as empty proof
            return [], [], set(), f"LOCAL_{type(exc).__name__.upper()}"

    def load_turn_dataset_result(
        self, *, identity: tuple[str, int, int, int] | None
    ) -> TurnDatasetResult:
        """Read one exact turn without converting connection/replica errors into zero."""
        generated_at = datetime.now(timezone.utc).isoformat()
        if identity is None:
            return TurnDatasetResult(
                status="IDENTITY_UNAVAILABLE",
                operational_source_id="",
                turn_id=None,
                generation=0,
                operational_revision=0,
                rows=(),
                source="LAST_KNOWN_GOOD",
                error_code="IDENTITY_UNAVAILABLE",
                generated_at=generated_at,
            )
        source_id, turn_id, generation, revision = identity
        local_rows, pending_rows, deleted, local_error = self._load_local_turn_evidence(
            identity
        )
        last_rows = self._last_good_turn_rows
        cached_rows: tuple[Mapping[str, Any], ...] = ()
        if last_rows and last_rows[0] == identity:
            cached_rows = last_rows[1]

        if bool(getattr(self._runtime, "offline", False)):
            if local_error and not cached_rows:
                return TurnDatasetResult(
                    "QUERY_ERROR", source_id, turn_id, generation, revision, (),
                    "LAST_KNOWN_GOOD", local_error, generated_at,
                )
            merged = self._merge_turn_rows(cached_rows, local_rows, deleted)
            if not merged and not cached_rows and not local_rows:
                return TurnDatasetResult(
                    "LOCAL_REPLICA_BEHIND", source_id, turn_id, generation, revision,
                    (), "LAST_KNOWN_GOOD", "LOCAL_REPLICA_BEHIND", generated_at,
                    local_count=0, pending_count=len(pending_rows),
                )
            result = TurnDatasetResult(
                "VALID", source_id, turn_id, generation, revision, tuple(merged),
                "OFFLINE_LOCAL", local_error, generated_at,
                local_count=len(local_rows), pending_count=len(pending_rows),
            )
            return result

        try:
            central_rows = self._load_central_turn_rows(turn_id, source_id)
        except Exception as exc:  # noqa: BLE001 - preserve exact-turn local evidence
            error_status = self._dataset_error_status(exc)
            merged = self._merge_turn_rows(cached_rows, local_rows, deleted)
            if not merged:
                return TurnDatasetResult(
                    error_status, source_id, turn_id, generation, revision, (),
                    "LAST_KNOWN_GOOD", error_status, generated_at,
                    local_count=len(local_rows), pending_count=len(pending_rows),
                )
            result = TurnDatasetResult(
                "VALID", source_id, turn_id, generation, revision, tuple(merged),
                "OFFLINE_LOCAL", error_status, generated_at,
                local_count=len(local_rows), pending_count=len(pending_rows),
            )
            return result

        merged = self._merge_turn_rows(central_rows, pending_rows, deleted)
        status = "VALID_EMPTY" if not merged else "VALID"
        source = "CENTRAL_PLUS_PENDING" if pending_rows else "CENTRAL"
        result = TurnDatasetResult(
            status, source_id, turn_id, generation, revision, tuple(merged), source,
            local_error, generated_at, central_count=len(central_rows),
            local_count=len(local_rows), pending_count=len(pending_rows),
        )
        return result

    def _confirm_central_zero(
        self, identity: tuple[str, int, int, int]
    ) -> tuple[bool, str]:
        """One lightweight recheck guards a same-turn N→0 transition."""
        if bool(getattr(self._runtime, "offline", False)):
            return False, "ZERO_RECHECK_OFFLINE"
        source_id, turn_id, _generation, _revision = identity
        sql = """SELECT COUNT(*) FILTER (
                            WHERE UPPER(COALESCE(service_type,'EMERGENCIA'))
                                  NOT IN ('URGENCIA','CONSULTA')
                        ) AS emergency_count
                   FROM admission_attention_projection
                  WHERE operational_source_id::TEXT=%s
                    AND turn_id=%s
                    AND COALESCE(is_deleted,FALSE)=FALSE
                    AND UPPER(TRIM(COALESCE(source_status,'ACTIVA')))
                        IN ('ACTIVA','PENDIENTE')"""
        try:
            with self._runtime.host.connection_factory() as connection:
                row = connection.execute(sql, (source_id, turn_id)).fetchone()
            count = int(
                (row.get("emergency_count") if isinstance(row, Mapping) else row[0])
                or 0
            )
        except Exception as exc:  # noqa: BLE001 - a failed recheck must preserve LKG
            return False, f"ZERO_RECHECK_{self._dataset_error_status(exc)}"
        if _operational_identity_tuple(self._runtime.operational_session) != identity:
            return False, "ZERO_RECHECK_STALE_IDENTITY"
        if count != 0:
            return False, "ZERO_RECHECK_NONZERO"
        return True, ""

    def build_turn_dataset(
        self,
        *,
        turn_id: int | None,
        operational_source_id: str | None,
    ) -> list[dict[str, Any]]:
        """Compatibility API: invalid reads raise instead of looking like empty turns."""
        effective_turn = int(turn_id or 0)
        operational_source = str(operational_source_id or "").strip()
        logger = getattr(self._runtime, "logger", None)
        if effective_turn <= 0:
            if logger is not None:
                logger.warning(
                    "CURRENT_TURN_DATASET turn_id=%s source=%s status=INVALID_DATASET_STATE "
                    "reason=TURN_ID_NOT_AVAILABLE",
                    effective_turn,
                    operational_source or "-",
                )
            raise TurnDatasetStateError("TURN_ID_NOT_AVAILABLE")
        if not operational_source:
            if logger is not None:
                logger.warning(
                    "CURRENT_TURN_DATASET turn_id=%s source=- status=INVALID_DATASET_STATE "
                    "reason=OPERATIONAL_SOURCE_ID_NOT_AVAILABLE",
                    effective_turn,
                )
            raise TurnDatasetStateError("OPERATIONAL_SOURCE_ID_NOT_AVAILABLE")
        runtime_identity = _operational_identity_tuple(self._runtime.operational_session)
        identity = (
            runtime_identity
            if runtime_identity
            and runtime_identity[:2] == (operational_source, effective_turn)
            else (operational_source, effective_turn, 0, 0)
        )
        dataset = self.load_turn_dataset_result(identity=identity)
        if not dataset.is_valid:
            raise TurnDatasetStateError(dataset.error_code or dataset.status)
        result = [dict(row) for row in dataset.rows]
        if logger is not None:
            logger.info(
                "CURRENT_TURN_DATASET turn_id=%s source=%s count=%s "
                "status=%s dataset_source=%s",
                effective_turn, operational_source, len(result), dataset.status,
                dataset.source,
            )
        return result

    @staticmethod
    def _statistical_report_records_query(
        *,
        operational_source_id: str,
        turn_id: int | None,
        start_at: datetime,
        end_at: datetime,
    ) -> tuple[str, tuple[Any, ...]]:
        select_clause = """SELECT p.*,
                          p.attention_id AS id,
                          p.patient_name AS nombre,
                          p.service_date AS fecha,
                          p.service_time AS hora,
                          p.specialty AS hoja,
                          p.canonical_ars AS ars,
                          p.nss_snapshot AS nss,
                          p.cedula_snapshot AS cedula,
                          p.service_type AS tipo_atencion
                     FROM admission_attention_projection p
                    WHERE COALESCE(p.is_deleted,FALSE)=FALSE
                      AND UPPER(TRIM(COALESCE(p.source_status,'ACTIVA')))
                          IN ('ACTIVA','PENDIENTE')
                      AND p.operational_source_id::TEXT=%s"""
        order_clause = """ ORDER BY COALESCE(p.created_at_effective_utc,
                                      NULLIF(p.synced_at,'')::TIMESTAMPTZ),
                             COALESCE(p.origin_device_id,''),
                             COALESCE(p.device_local_sequence,0),
                             COALESCE(p.global_attention_id::TEXT,p.attention_id::TEXT)"""
        if turn_id is not None:
            return (
                select_clause + " AND p.turn_id=%s" + order_clause,
                (operational_source_id, int(turn_id)),
            )
        return (
            select_clause + " AND p.service_date BETWEEN %s AND %s" + order_clause,
            (
                operational_source_id,
                (start_at.date() - timedelta(days=1)).isoformat(),
                end_at.date().isoformat(),
            ),
        )

    @classmethod
    def _query_statistical_report_records(
        cls,
        connection: Any,
        *,
        operational_source_id: str,
        turn_id: int | None,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        sql, params = cls._statistical_report_records_query(
            operational_source_id=operational_source_id,
            turn_id=turn_id,
            start_at=start_at,
            end_at=end_at,
        )
        return [dict(row) for row in connection.execute(sql, params).fetchall()]

    @staticmethod
    def _query_statistical_report_turns(
        connection: Any,
        *,
        operational_source_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        sql = """WITH report_turns AS (
                     SELECT i.operational_session_id,i.generation,i.turn_id,
                            s.operational_source_id::TEXT AS operational_source_id,
                            i.started_at,
                            COALESCE(i.ended_at,i.nominal_ends_at,s.turn_ends_at) AS ends_at,
                            CASE
                              WHEN s.turn_id=i.turn_id AND s.status='ACTIVE' THEN 'CURRENT'
                              WHEN i.ended_at IS NULL THEN 'OPEN'
                              ELSE 'CLOSED'
                            END AS status,
                            i.active_user_id,i.active_username
                       FROM admission_operational_turn_intervals i
                       JOIN admission_operational_sessions s
                         ON s.operational_session_id=i.operational_session_id
                      WHERE s.operational_source_id::TEXT=%s
                        AND i.turn_id IS NOT NULL
                      ORDER BY i.started_at DESC,i.generation DESC
                      LIMIT %s
                   ), representative_evidence AS (
                     SELECT t.turn_id,t.started_at AS event_at,
                            t.active_user_id AS user_id,t.active_username AS username
                       FROM report_turns t
                     UNION ALL
                     SELECT t.turn_id,a.created_at AS event_at,
                            NULLIF(a.details_json->>'new_user_id','') AS user_id,
                            NULLIF(a.details_json->>'new_username','') AS username
                       FROM report_turns t
                       JOIN admission_operational_audit a
                         ON a.operational_session_id=t.operational_session_id
                        AND a.event_type='TURN_REPRESENTATIVE_ADMIN_CORRECTED'
                        AND a.details_json->>'turn_id'=t.turn_id::TEXT
                   )
                   SELECT t.turn_id,t.operational_source_id,t.started_at,t.ends_at,t.status,
                          COALESCE((
                            SELECT jsonb_agg(
                                     jsonb_build_object(
                                       'user_id',COALESCE(e.user_id,''),
                                       'username',COALESCE(e.username,''),
                                       'display_name',COALESCE(
                                         NULLIF(TRIM(u.full_name),''),
                                         NULLIF(TRIM(e.username),''),''
                                       ),
                                       'event_at',e.event_at
                                     ) ORDER BY e.event_at
                                   )
                              FROM representative_evidence e
                              LEFT JOIN users u ON CAST(u.id AS TEXT)=e.user_id
                             WHERE e.turn_id=t.turn_id
                          ),'[]'::jsonb) AS representatives
                     FROM report_turns t
                    ORDER BY t.started_at DESC,t.generation DESC"""
        return [
            dict(row)
            for row in connection.execute(
                sql,
                (
                    operational_source_id,
                    max(1, min(int(limit or 100), 500)),
                ),
            ).fetchall()
        ]

    def _classify_report_read_error(self, exc: BaseException) -> ReportReadError:
        if isinstance(exc, ReportReadError):
            return exc
        text = str(exc or "").casefold()
        temporary_types = getattr(self._runtime, "_temporary_errors", ())
        temporary = isinstance(exc, temporary_types) or bool(
            self._runtime._is_temporary_connection_error(exc)
        )
        if temporary and ("timeout" in text or "timed out" in text):
            return ReportReadError(
                "REPORT_CONNECTION_TIMEOUT",
                "La conexión central tardó demasiado en responder.",
            )
        if temporary:
            return ReportReadError(
                "REPORT_DATABASE_UNAVAILABLE",
                "La base central no está disponible temporalmente.",
            )
        data_tokens = ("invalid input syntax", "invalid uuid", "datatype mismatch")
        if any(token in text for token in data_tokens):
            return ReportReadError(
                "REPORT_DATA_ERROR",
                "El reporte encontró metadata histórica incompatible.",
            )
        return ReportReadError(
            "REPORT_QUERY_ERROR",
            "No fue posible completar la consulta del reporte.",
        )

    def _run_statistical_report_read(
        self, operation: Callable[[], dict[str, Any]]
    ) -> dict[str, Any]:
        started = perf_counter()
        logger = getattr(self._runtime, "logger", None)
        for attempt in (1, 2):
            try:
                result = operation()
                if logger is not None:
                    logger.info(
                        "STATISTICAL_REPORT_READ_SUCCEEDED operation=load_snapshot "
                        "attempt=%s connection_acquisitions=%s turn_rows=%s "
                        "attention_rows=%s elapsed_ms=%.1f",
                        attempt,
                        attempt,
                        len(result.get("turns") or ()),
                        len(result.get("records") or ()),
                        (perf_counter() - started) * 1000.0,
                    )
                return result
            except Exception as exc:  # noqa: BLE001 - categorized DB boundary
                classified = self._classify_report_read_error(exc)
                if logger is not None:
                    logger.error(
                        "STATISTICAL_REPORT_READ_FAILED code=%s operation=load_snapshot "
                        "exception_type=%s safe_error_message=%s attempt=%s elapsed_ms=%.1f",
                        classified.code,
                        type(exc).__name__,
                        classified.safe_message,
                        attempt,
                        (perf_counter() - started) * 1000.0,
                    )
                if classified.code not in {
                    "REPORT_CONNECTION_TIMEOUT",
                    "REPORT_DATABASE_UNAVAILABLE",
                } or attempt == 2:
                    raise classified from exc
                delay = max(
                    0.0,
                    float(getattr(self._runtime, "_report_retry_delay_seconds", 0.2)),
                )
                if delay:
                    sleep(delay)
        raise AssertionError("unreachable")

    @staticmethod
    def _select_statistical_report_turn(
        turns: Iterable[Mapping[str, Any]],
        *,
        turn_scope: str,
        current_turn_id: int,
    ) -> dict[str, Any] | None:
        if turn_scope == "Todos los turnos":
            return None
        candidates = [dict(row) for row in turns]
        if turn_scope == "Turno actual":
            return next(
                (
                    row
                    for row in candidates
                    if int(row.get("turn_id") or 0) == int(current_turn_id or 0)
                ),
                None,
            )
        return next(
            (
                row
                for row in candidates
                if int(row.get("turn_id") or 0) != int(current_turn_id or 0)
            ),
            None,
        )

    def _ensure_statistical_report_turn(
        self,
        turns: list[dict[str, Any]],
        *,
        source_id: str,
        turn_scope: str,
        current_turn_id: int,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        selected = self._select_statistical_report_turn(
            turns,
            turn_scope=turn_scope,
            current_turn_id=current_turn_id,
        )
        if turn_scope == "Todos los turnos" or selected is not None:
            return selected, turns
        if turn_scope == "Turno anterior":
            raise ReportReadError(
                "REPORT_DATA_ERROR",
                "No existe un turno anterior central disponible.",
            )
        session = self._runtime.operational_session
        fallback = {
            "turn_id": int(current_turn_id),
            "operational_source_id": source_id,
            "started_at": getattr(session, "turn_started_at", None),
            "ends_at": getattr(session, "turn_ends_at", None),
            "status": "CURRENT",
            "representatives": (),
        }
        return fallback, [fallback, *turns]

    @staticmethod
    def _statistical_report_source_result(
        *,
        turns: list[dict[str, Any]],
        selected_turn: dict[str, Any] | None,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        turn_id = int(selected_turn["turn_id"]) if selected_turn is not None else None
        return {
            "turn_id": turn_id,
            "selected_turn": selected_turn,
            "turns": turns,
            "records": records,
        }

    def _load_online_statistical_report_source(
        self,
        *,
        source_id: str,
        turn_scope: str,
        current_turn_id: int,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> dict[str, Any]:
        with self._runtime.host.connection_factory() as connection:
            turns = self._query_statistical_report_turns(
                connection,
                operational_source_id=source_id,
                limit=limit,
            )
            selected, turns = self._ensure_statistical_report_turn(
                turns,
                source_id=source_id,
                turn_scope=turn_scope,
                current_turn_id=current_turn_id,
            )
            turn_id = int(selected["turn_id"]) if selected is not None else None
            records = self._query_statistical_report_records(
                connection,
                operational_source_id=source_id,
                turn_id=turn_id,
                start_at=start_at,
                end_at=end_at,
            )
        return self._statistical_report_source_result(
            turns=turns, selected_turn=selected, records=records
        )

    def _load_offline_statistical_report_source(
        self,
        *,
        source_id: str,
        turn_scope: str,
        current_turn_id: int,
        start_at: datetime,
        end_at: datetime,
        limit: int,
    ) -> dict[str, Any]:
        turns = self.list_statistical_report_turns(
            operational_source_id=source_id, limit=limit
        )
        selected, turns = self._ensure_statistical_report_turn(
            turns,
            source_id=source_id,
            turn_scope=turn_scope,
            current_turn_id=current_turn_id,
        )
        turn_id = int(selected["turn_id"]) if selected is not None else None
        records = self.list_statistical_report_records(
            operational_source_id=source_id,
            turn_id=turn_id,
            start_at=start_at,
            end_at=end_at,
        )
        return self._statistical_report_source_result(
            turns=turns, selected_turn=selected, records=records
        )

    def load_statistical_report_source(
        self,
        *,
        operational_source_id: str,
        turn_scope: str,
        current_turn_id: int,
        start_at: datetime,
        end_at: datetime,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Load turn evidence and rows through one pooled connection acquisition."""
        source_id = str(operational_source_id or "").strip()
        if not source_id:
            raise ValueError("El reporte requiere operational_source_id.")
        arguments = {
            "source_id": source_id,
            "turn_scope": turn_scope,
            "current_turn_id": current_turn_id,
            "start_at": start_at,
            "end_at": end_at,
            "limit": limit,
        }
        if bool(getattr(self._runtime, "offline", False)):
            return self._load_offline_statistical_report_source(**arguments)
        return self._run_statistical_report_read(
            lambda: self._load_online_statistical_report_source(**arguments)
        )

    def list_statistical_report_records(
        self,
        *,
        operational_source_id: str,
        turn_id: int | None,
        start_at: datetime,
        end_at: datetime,
    ) -> list[dict[str, Any]]:
        """Read active central attentions for reports without creating a second history.

        Exact-turn reports are scoped by the durable operational identity.  Date
        reports use a deliberately broad ``service_date`` prefilter and leave the
        authoritative half-open 08:00 window to the pure report dataset builder.
        """
        source_id = str(operational_source_id or "").strip()
        if not source_id:
            raise ValueError("El reporte requiere operational_source_id.")
        started = perf_counter()
        logger = getattr(self._runtime, "logger", None)
        if bool(getattr(self._runtime, "offline", False)):
            rows = list(
                self._database.obtener_atenciones_para_rango_real(
                    start_at,
                    end_at,
                    operational_turn_id=int(turn_id) if turn_id is not None else None,
                    operational_source_id=source_id,
                )
                or []
            )
            if logger is not None:
                logger.info(
                    "ADMISSION_STATISTICAL_REPORT_READ source=offline_replica "
                    "turn_id=%s rows=%s elapsed_ms=%.1f",
                    turn_id,
                    len(rows),
                    (perf_counter() - started) * 1000.0,
                )
            return rows

        with self._runtime.host.connection_factory() as connection:
            rows = self._query_statistical_report_records(
                connection,
                operational_source_id=source_id,
                turn_id=turn_id,
                start_at=start_at,
                end_at=end_at,
            )
        if logger is not None:
            logger.info(
                "ADMISSION_STATISTICAL_REPORT_READ source=postgresql turn_id=%s "
                "rows=%s elapsed_ms=%.1f",
                turn_id,
                len(rows),
                (perf_counter() - started) * 1000.0,
            )
        return rows

    def list_statistical_report_turns(
        self,
        *,
        operational_source_id: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List persisted operational turns for the current production source."""
        source_id = str(operational_source_id or "").strip()
        if not source_id:
            return []
        session = self._runtime.operational_session
        if bool(getattr(self._runtime, "offline", False)):
            if session is None:
                return []
            return [
                {
                    "turn_id": int(session.turn_id),
                    "operational_source_id": source_id,
                    "started_at": getattr(session, "turn_started_at", None),
                    "ends_at": getattr(session, "turn_ends_at", None),
                    "status": "CURRENT_OFFLINE",
                }
            ]
        with self._runtime.host.connection_factory() as connection:
            return self._query_statistical_report_turns(
                connection,
                operational_source_id=source_id,
                limit=limit,
            )

    def build_current_admission_list_dataset(
        self,
        turn_id: int | None = None,
        operational_source_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vista única para el turno operativo actual; nunca infiere por fecha."""
        session = self._runtime.operational_session
        if session is not None:
            return self.build_turn_dataset(
                turn_id=session.turn_id,
                operational_source_id=session.operational_source_id,
            )
        return self.build_turn_dataset(
            turn_id=turn_id,
            operational_source_id=operational_source_id,
        )

    def refresh_excel_from_canonical_dataset(self) -> int:
        """Regenera la vista Excel en worker desde el dataset híbrido actual."""
        module = sys.modules.get(self._database.__class__.__module__)
        if module is None:
            raise RuntimeError("El motor V15 no está disponible para exportar Excel.")
        turn_config = module.cargar_turno_config(permitir_vencido=True)
        if not turn_config:
            return 0
        return int(module.reconstruir_excel_turno(self, turn_config) or 0)


    def admin_correct_current_turn_representative(
        self, target: Any, *, authorizing_admin: Any
    ):
        return self._runtime.admin_correct_current_turn_representative(
            target, authorizing_admin=authorizing_admin
        )

    def perform_explicit_turn_handoff(
        self, *, shift_metadata: Mapping[str, Any]
    ):
        """Execute the central transaction before any local turn materialization."""
        transition = self._runtime.perform_explicit_turn_handoff(
            shift_metadata=shift_metadata
        )
        object.__setattr__(self, "_last_transition_result", transition)
        return transition

    def __getattr__(self, name: str):
        if name == "search_patient_directory":
            return self._runtime.search_patient_directory
        value = getattr(self._database, name)
        if name in self._history_methods and callable(value):
            return lambda *args, **kwargs: self._cloud_history(name, args, kwargs)
        if name == "obtener_atencion_por_id" and callable(value):
            def resolve_local_to_global(attention_id: int):
                candidate = value(int(attention_id))
                if not candidate:
                    return None
                global_id = str(candidate.get("global_attention_id") or "").strip()
                if not global_id or self._runtime.store is None:
                    raise RuntimeError(
                        "La atencion local no posee una identidad global verificable."
                    )
                resolved = self._runtime.store.get_attention_by_global_id(
                    global_id, include_deleted=True
                )
                if not resolved or int(resolved.get("id") or 0) != int(attention_id):
                    self._runtime.logger.error(
                        "DOCUMENT_IDENTITY_MISMATCH selected_global_attention_id=%s "
                        "selected_local_attention_id=%s resolved_local_attention_id=%s",
                        global_id,
                        attention_id,
                        (resolved or {}).get("id"),
                    )
                    raise RuntimeError(
                        "La identidad local no coincide con la atencion seleccionada."
                    )
                return resolved

            return resolve_local_to_global
        if name == "get_attention_by_global_id":
            return self._runtime.get_attention_by_global_id
        if name == "borrar_atencion" and callable(value):
            def cancel_by_local_identity(
                attention_id: int, *, motivo: str = "", usuario: str = ""
            ) -> bool:
                del usuario  # actor identity comes from the authenticated context
                self._runtime.require_write()
                selected = self._database.obtener_atencion_por_id(int(attention_id))
                if not selected:
                    return False
                global_id = str(selected.get("global_attention_id") or "").strip()
                if not global_id:
                    raise ValueError(
                        "La atencion no posee identidad global y no puede anularse de forma segura."
                    )
                result = self._runtime.cancel_admission_attention(
                    global_id, reason=str(motivo or "")
                )
                return bool(result and result.get("is_deleted"))

            return cancel_by_local_identity
        if name == "obtener_turnos_historial" and callable(value):
            def central_turns():
                turns = dict(value() or {})
                session = self._runtime.operational_session
                if session is None or session.turn_id is None:
                    return turns
                actual = dict(turns.get("actual") or {})
                actual.update({
                    "id": int(session.turn_id),
                    "representante": session.active_user_display_name or session.active_username,
                    "estado": "ABIERTO",
                })
                turns["actual"] = actual
                return turns
            return central_turns
        guarded = name in self._primary_methods or name.startswith(self._write_prefixes)
        if name == "obtener_o_crear_turno" and callable(value):
            def materialize_local_turn(*args, **kwargs):
                self._runtime.require_write()
                result = value(*args, **kwargs)
                self._runtime.logger.info(
                    "LOCAL_TURN_MIRROR_MATERIALIZED local_turn_id=%s trigger=LOCAL_MIRROR",
                    int(result or 0),
                )
                return result

            return materialize_local_turn
        if not guarded or not callable(value):
            return value

        def call(*args, **kwargs):
            if name in self._primary_methods:
                if self._runtime.role == self._runtime.StationRole.SECONDARY:
                    stack = " > ".join(
                        frame.name for frame in traceback.extract_stack(limit=5)[:-1]
                    )
                    self._runtime.logger.error(
                        "SECONDARY_PRIMARY_TRANSITION_GUARD_TRIGGERED "
                        "method=%s stack=%s",
                        name,
                        stack,
                    )
                if name in {"cerrar_turno_existente", "notify_shift_changed"}:
                    # The turn guard decides whether this is a same-user turn
                    # change or a formal handover by another operator on the
                    # same PRIMARY. The runtime performs the matching command.
                    self._runtime.require_primary_turn_change()
                else:
                    self._runtime.require_primary_transition()
            else:
                self._runtime.require_write()
            result = value(*args, **kwargs)
            return result

        return call

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._database, name, value)


class _HybridCoordinator(QObject):
    state_changed = Signal(object)
    sync_finished = Signal(object)
    failed = Signal(str)
    rerun_requested = Signal()

    def __init__(self, runtime: _HybridAdmissionRuntime, parent=None):
        super().__init__(parent)
        from admission_hybrid import SYNC_TICK_SECONDS

        self.runtime = runtime
        self._busy = False
        self._pending = False
        self._mirror_busy = False
        self._stopped = False
        self._failure_count = 0
        self._retry_not_before = 0.0
        self._last_state_fingerprint = None
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(2)
        self._pool.setExpiryTimeout(0)
        self._timer = QTimer(self)
        self._timer.setInterval(int(SYNC_TICK_SECONDS * 1000))
        self._timer.timeout.connect(self._schedule)

    def start(self) -> None:
        self.state_changed.emit(self.runtime.state())
        self._timer.start()
        self._busy = True
        self.submit_background(
            lambda: self.runtime.refresh_operational_state(force_remote=True),
            self._login_reattach_succeeded,
            self._login_reattach_failed,
        )

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._timer.stop()
        self._pending = False
        self._pool.clear()

    def submit_background(
        self,
        operation: Callable[[], Any],
        on_success: Callable[[Any], None],
        on_failure: Callable[[str], None] | None = None,
    ) -> None:
        """Queues auxiliary I/O in the same controlled Qt pool."""
        if self._stopped:
            return
        signals = _BackgroundTaskSignals(self)

        def deliver_success(result: Any) -> None:
            if not self._stopped:
                on_success(result)

        def deliver_failure(code: str) -> None:
            if not self._stopped:
                (on_failure or self.failed.emit)(code)

        signals.succeeded.connect(deliver_success)
        signals.failed.connect(deliver_failure)
        self._pool.start(_BackgroundTask(operation, signals))

    @Slot()
    def _schedule(self) -> None:
        if self._stopped:
            return
        if perf_counter() < self._retry_not_before:
            return
        if self._busy or self._mirror_busy:
            self._pending = True
            return
        self._busy = True
        self.submit_background(self.runtime.synchronize, self._sync_succeeded, self._sync_failed)

    @staticmethod
    def _state_fingerprint(state: Mapping[str, Any]) -> tuple[Any, ...]:
        return tuple(
            state.get(name)
            for name in (
                "offline", "role", "writable", "turn_id", "generation",
                "operational_revision", "active_user_id", "active_username",
                "active_user_display_name", "turn_code", "turn_started_at",
                "turn_ends_at", "primary_device_id", "lease_generation",
                "pending_sync_count", "force_logout_required", "sync_state",
                "reason_code",
            )
        )

    def _finish_cycle(self) -> None:
        self._busy = False
        if self._pending and not self._stopped:
            self._pending = False
            QTimer.singleShot(0, self._schedule)

    def _record_connection_failure(self) -> None:
        self._failure_count += 1
        delay = bounded_sync_retry_delay(
            self._failure_count,
            str(getattr(self.runtime, "device_id", "")),
        )
        self._retry_not_before = perf_counter() + delay
        self.runtime.logger.warning(
            "SYNC_BACKOFF failures=%s delay_seconds=%.2f",
            self._failure_count,
            delay,
        )

    def _reset_connection_backoff(self) -> None:
        self._failure_count = 0
        self._retry_not_before = 0.0

    @Slot(object)
    def _login_reattach_succeeded(self, result: Any) -> None:
        self._reset_connection_backoff()
        data = dict(result or {})
        fingerprint = self._state_fingerprint(data)
        if fingerprint != self._last_state_fingerprint:
            self._last_state_fingerprint = fingerprint
            self.state_changed.emit(data)
        if data.get("local_mirror_pending"):
            self._busy = False
            self._mirror_busy = True
            self.submit_background(
                self.runtime.apply_operational_mirror_to_v15,
                self._mirror_succeeded,
                self._mirror_failed,
            )
            return
        self._busy = False
        self._pending = False
        QTimer.singleShot(0, self._schedule)

    @Slot(str)
    def _login_reattach_failed(self, code: str) -> None:
        self.failed.emit(code)
        self._record_connection_failure()
        self._busy = False
        self._pending = False

    @Slot(object)
    def _sync_succeeded(self, result: Any) -> None:
        data = dict(result or {})
        if bool(data.get("offline")):
            self._record_connection_failure()
        else:
            self._reset_connection_backoff()
        fingerprint = self._state_fingerprint(data)
        if fingerprint != self._last_state_fingerprint:
            self._last_state_fingerprint = fingerprint
            self.state_changed.emit(data)
        if any(int(data.get(name) or 0) > 0 for name in (
            "pushed", "pulled", "recovered", "replayed", "backfilled", "reconciled",
            "patient_pulled",
        )):
            self.sync_finished.emit(data)
        if data.get("local_mirror_pending"):
            self._mirror_busy = True
            self.submit_background(
                self.runtime.apply_operational_mirror_to_v15,
                self._mirror_succeeded,
                self._mirror_failed,
            )
        self._finish_cycle()

    @Slot(object)
    def _mirror_succeeded(self, result: Any) -> None:
        self._mirror_busy = False
        self._pending = False
        data = dict(result or {})
        fingerprint = self._state_fingerprint(data)
        if fingerprint != self._last_state_fingerprint:
            self._last_state_fingerprint = fingerprint
            self.state_changed.emit(data)
        QTimer.singleShot(0, self._schedule)

    @Slot(str)
    def _mirror_failed(self, code: str) -> None:
        self._mirror_busy = False
        self._pending = False
        self.runtime.logger.error("Espejo local V15 pendiente: %s", code)
        data = self.runtime.mark_operational_mirror_pending()
        fingerprint = self._state_fingerprint(data)
        if fingerprint != self._last_state_fingerprint:
            self._last_state_fingerprint = fingerprint
            self.state_changed.emit(data)
        QTimer.singleShot(1000, self._schedule)

    @Slot(str)
    def _sync_failed(self, code: str) -> None:
        self.failed.emit(code)
        self._record_connection_failure()
        self._finish_cycle()


class _V15BackgroundRefreshCoordinator(QObject):
    """Keeps V15's expensive SQLite reads out of the Qt GUI thread."""

    lookup_completed = Signal(object)

    def __init__(self, admission: Any, coordinator: _HybridCoordinator, parent=None):
        super().__init__(parent)
        self.admission = admission
        self.coordinator = coordinator
        self._summary_busy = False
        self._summary_pending = False
        self._summary_reason = ""
        self._summary_started_at = 0.0
        self._summary_fingerprint: tuple = ()
        self._lookup_generation = 0
        self._summary_debounce = QTimer(self)
        self._summary_debounce.setSingleShot(True)
        self._summary_debounce.setInterval(180)
        self._summary_debounce.timeout.connect(self._start_summary)
        self.lookup_completed.connect(self._consume_lookup)

    def stop(self) -> None:
        self._summary_debounce.stop()
        self._summary_pending = False
        self._lookup_generation += 1

    def request_summary(self, reason: str = "attention_event") -> None:
        runtime = getattr(getattr(self.admission, "db", None), "_runtime", None)
        logger = getattr(runtime, "logger", None)
        self._summary_reason = str(reason or "attention_event")
        if logger is not None:
            logger.info("SUMMARY_REFRESH_REQUEST reason=%s", self._summary_reason)
        if self._summary_busy:
            self._summary_pending = True
            if logger is not None:
                logger.info(
                    "SUMMARY_REFRESH_SKIPPED reason=%s state=BUSY_PENDING",
                    self._summary_reason,
                )
            return
        self._summary_debounce.start()

    @Slot()
    def _start_summary(self) -> None:
        if self._summary_busy:
            self._summary_pending = True
            return
        database = getattr(self.admission, "db", None)
        refresh = getattr(database, "refresh_turn_summary", None)
        if not callable(refresh):
            return
        self._summary_busy = True
        self._summary_started_at = perf_counter()
        runtime = getattr(database, "_runtime", None)
        if runtime is not None:
            runtime.logger.info("SUMMARY_REFRESH_START reason=%s", self._summary_reason)
        reason = self._summary_reason
        self.coordinator.submit_background(
            lambda: refresh(reason=reason),
            self._summary_ready,
            self._summary_failed,
        )

    @Slot(object)
    def _summary_ready(self, _summary: Any) -> None:
        self._summary_busy = False
        runtime = getattr(getattr(self.admission, "db", None), "_runtime", None)
        summary = dict(_summary or {})
        if self._discard_invalid_summary(summary, runtime):
            return
        if runtime is not None and not summary_result_matches_runtime_identity(
            summary, runtime
        ):
            runtime.logger.warning(
                "SUMMARY_REFRESH_DISCARDED reason=%s error=STALE_OPERATIONAL_SNAPSHOT "
                "result_turn_id=%s result_generation=%s result_revision=%s",
                self._summary_reason,
                summary.get("_turn_id"),
                summary.get("_generation"),
                summary.get("_operational_revision"),
            )
            if self._summary_pending:
                self._summary_pending = False
                self.request_summary("pending_after_stale_result")
            return
        fingerprint = tuple(
            (key, int(summary.get(key) or 0))
            for key in (
                "total", "sin_seguro", "GENERAL", "PEDIATRIA",
                "GINECOLOGIA", "URGENCIAS", "CONSULTAS",
            )
        )
        changed = fingerprint != self._summary_fingerprint
        self._summary_fingerprint = fingerprint
        if runtime is not None:
            runtime.logger.info(
                "SUMMARY_REFRESH_DONE reason=%s changed=%s elapsed_ms=%.1f",
                self._summary_reason,
                str(bool(changed)).lower(),
                (perf_counter() - self._summary_started_at) * 1000.0,
            )
        # Deliver the exact dataset result to the sidebar.  Reading the
        # proxy again here used to leave a startup cache visible even though
        # History had already materialized the same current-turn row.
        apply_summary = getattr(self.admission, "apply_turn_summary", None)
        if callable(apply_summary):
            apply_summary(summary, reason=self._summary_reason)
        else:
            refresh = getattr(self.admission, "_actualizar_resumen_turno_panel", None)
            if callable(refresh):
                refresh(forzar=True)
        if runtime is not None:
            runtime.logger.info(
                "SUMMARY_REFRESH_UI_APPLIED reason=%s", self._summary_reason
            )
        if self._summary_pending:
            self._summary_pending = False
            self.request_summary("pending_event")

    def _discard_invalid_summary(
        self, summary: Mapping[str, Any], runtime: Any
    ) -> bool:
        if summary.get("_status") != "INVALID_REFRESH":
            return False
        if runtime is not None:
            runtime.logger.warning(
                "SUMMARY_REFRESH_DISCARDED reason=%s error=%s status=INVALID_REFRESH",
                self._summary_reason,
                summary.get("_error_code") or "INVALID_DATASET_STATE",
            )
        if self._summary_pending:
            self._summary_pending = False
            self.request_summary("pending_after_invalid_result")
        return True

    @Slot(str)
    def _summary_failed(self, _code: str) -> None:
        self._summary_busy = False
        runtime = getattr(getattr(self.admission, "db", None), "_runtime", None)
        if runtime is not None:
            runtime.logger.error(
                "SUMMARY_REFRESH_DONE reason=%s changed=false error=%s elapsed_ms=%.1f",
                self._summary_reason,
                _code,
                (perf_counter() - self._summary_started_at) * 1000.0,
            )
        if self._summary_pending:
            self._summary_pending = False
            self.request_summary("pending_after_error")

    def request_lookup(self, field: str) -> None:
        self._lookup_generation += 1
        generation = self._lookup_generation
        if field == "cedula":
            value = str(self.admission.entry_cedula.get() or "").replace("-", "").strip()

            def operation():
                return dict(
                    self.admission.db.search_patient_directory(cedula=value) or {}
                )
        else:
            value = str(self.admission.entry_nss.get() or "").upper().strip()

            def operation():
                return dict(
                    self.admission.db.search_patient_directory(nss=value) or {}
                )
        if not value:
            return

        def consume(result: Any) -> None:
            self.lookup_completed.emit({
                "field": field,
                "value": value,
                "generation": generation,
                "patient": dict(result or {}),
            })

        self.coordinator.submit_background(operation, consume)

    @Slot(object)
    def _consume_lookup(self, payload: Any) -> None:
        data = dict(payload or {})
        field = str(data.get("field") or "")
        expected = str(data.get("value") or "")
        generation = int(data.get("generation") or 0)
        patient = dict(data.get("patient") or {})
        if generation != self._lookup_generation or not patient:
            return
        current = (
            str(self.admission.entry_cedula.get() or "").replace("-", "").strip()
            if field == "cedula"
            else str(self.admission.entry_nss.get() or "").upper().strip()
        )
        if current != expected:
            return
        self.admission._suspend_autocomplete = True
        try:
            values = {
                "entry_nombre": patient.get("nombre") or "",
                "entry_telefono": patient.get("telefono") or "",
                "entry_direccion": patient.get("direccion") or "",
                "entry_nacionalidad": patient.get("nacionalidad") or "",
                "entry_ars": patient.get("ars") or "",
            }
            if field == "cedula":
                values["entry_nss"] = str(patient.get("nss") or "").upper()
            else:
                values["entry_cedula"] = patient.get("cedula") or ""
            for name, value in values.items():
                entry = getattr(self.admission, name, None)
                if entry is not None:
                    entry.delete(0, "end")
                    entry.insert(0, value)
        finally:
            self.admission._suspend_autocomplete = False
        refresh = getattr(self.admission, "_actualizar_deteccion_seguro", None)
        if callable(refresh):
            refresh()


class _HybridExcelRefreshCoordinator(QObject):
    """Coalescing de cambios cloud; openpyxl/DB siempre quedan fuera del GUI."""

    def __init__(
        self,
        database: Any,
        coordinator: _HybridCoordinator,
        retry_callback: Callable[[], Any] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.database = database
        self.coordinator = coordinator
        self.retry_callback = retry_callback
        self._busy = False
        self._pending = False
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(self._start)

    def stop(self) -> None:
        self._debounce.stop()
        self._pending = False

    def request(self) -> None:
        if self._busy:
            self._pending = True
            return
        self._debounce.start()

    @Slot()
    def _start(self) -> None:
        if self._busy:
            self._pending = True
            return
        self._busy = True
        self.coordinator.submit_background(
            self.database.refresh_excel_from_canonical_dataset,
            self._finished,
            self._failed,
        )

    @Slot(object)
    def _finished(self, _result: Any) -> None:
        self._busy = False
        if callable(self.retry_callback):
            self.retry_callback()
        if self._pending:
            self._pending = False
            self._debounce.start()

    @Slot(str)
    def _failed(self, code: str) -> None:
        self._busy = False
        logging.getLogger("hospital.admission.v15.excel").error(
            "Actualización Excel diferida: %s", code
        )
        if self._pending:
            self._pending = False
            self._debounce.start()


def _bind_hybrid_shutdown(
    widget: Any,
    hybrid: Any,
    coordinator: _HybridCoordinator,
    refresh_controller: _V15BackgroundRefreshCoordinator | None,
    excel_refresh: _HybridExcelRefreshCoordinator | None,
) -> None:
    original_shutdown = widget.shutdown
    shutdown_state = {"complete": False}

    def shutdown_with_hybrid_cleanup(_widget_self) -> None:
        if shutdown_state["complete"]:
            return
        shutdown_state["complete"] = True
        if refresh_controller is not None:
            refresh_controller.stop()
        if excel_refresh is not None:
            excel_refresh.stop()
        coordinator.stop()
        hybrid.shutdown()
        original_shutdown()

    widget.shutdown = MethodType(shutdown_with_hybrid_cleanup, widget)


def _clean(value: Any, maximum: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "").strip())[:maximum]


def bounded_sync_retry_delay(failure_count: int, device_id: str = "") -> float:
    """Exponential retry delay with deterministic per-device jitter."""
    failures = max(1, int(failure_count))
    base = min(60.0, 5.0 * (2 ** min(failures - 1, 4)))
    seed = sum(ord(character) for character in f"{device_id}:{failures}") % 101
    return base + (base * 0.2 * seed / 100.0)


def _online_sync_status(
    role: str, pending_sync_count: int
) -> tuple[str, tuple[str, str, str]]:
    station = "Principal" if str(role).upper() == "PRIMARY" else "Secundaria"
    pending = max(0, int(pending_sync_count or 0))
    if pending:
        return (
            f"Conectado · {station} · Pendiente de sincronización ({pending})",
            ("#FFF4D6", "#7A4E00", "#E5C36A"),
        )
    return (
        f"Conectado · {station} · Sincronizado",
        ("#E8F7EE", "#17633A", "#9AD5B0"),
    )


def _bind_operational_file_logging(logger: logging.Logger, database_class: type) -> None:
    """Route safe operational metrics to V15's existing rotating app log."""
    module = sys.modules.get(str(getattr(database_class, "__module__", "")))
    file_logger = getattr(module, "APP_LOG", None) if module is not None else None
    handlers = tuple(getattr(file_logger, "handlers", ()) or ())
    if not handlers:
        return
    for target in (logger, logging.getLogger("hospital.admission.operational")):
        target.setLevel(logging.INFO)
        for handler in handlers:
            if handler not in target.handlers:
                target.addHandler(handler)
        target.propagate = False


@dataclass(frozen=True, slots=True)
class _V15Modules:
    context_class: type
    widget_class: type
    database_class: type
    gateway_error_class: type[Exception]
    representative_class: type


def _load_v15_modules(v15_root: Path) -> _V15Modules:
    root = Path(v15_root).expanduser().resolve()
    frozen = _v15_runtime_is_frozen()
    logger = logging.getLogger("hospital.admission.v15")
    if not frozen:
        required = (
            root / "__init__.py",
            root / "admission_context.py",
            root / "admission_widget.py",
            root / "facturacion_tabs_pyside6.py",
            root / "project_bootstrap.py",
            root / "qt_compat.py",
        )
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise AdmissionV15IntegrationError(
                "La fuente certificada de Admisión V15 está incompleta: "
                + ", ".join(missing)
            )
        package_parent = str(root.parent)
        if package_parent not in sys.path:
            sys.path.insert(0, package_parent)
        _discard_noncanonical_v15_modules(root)

    # El bootstrap de V15 debe encontrar emergency_core en la raíz vigente,
    # nunca en build/dist ni en otra distribución portable.
    main_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()
    os.environ["HOSPITAL_PROJECT_ROOT"] = str(main_root)
    os.environ["HOSPITAL_ADMISSION_SOURCE"] = str(main_root / "admission_source")

    try:
        context_module = importlib.import_module(f"{_V15_PACKAGE}.admission_context")
        widget_module = importlib.import_module(f"{_V15_PACKAGE}.admission_widget")
        application_module = importlib.import_module(
            f"{_V15_PACKAGE}.facturacion_tabs_pyside6"
        )
        gateway_module = importlib.import_module("emergency_core.main_app_gateway")
    except Exception as exc:
        logger.exception(
            "V15_LOAD_ERROR expected_build_id=%s loaded_build_id=%s "
            "loaded_module_file=%s resolved_v15_root=%s frozen=%s",
            _EXPECTED_V15_SOURCE_BUILD_ID,
            "SIN_MARCADOR",
            "<not-imported>",
            root,
            frozen,
        )
        raise AdmissionV15IntegrationError(
            f"No se pudo importar Admisión V15 ({type(exc).__name__})."
        ) from exc

    if not frozen:
        loaded_package_root = Path(_module_location(context_module)).resolve().parent
        if loaded_package_root != root:
            raise AdmissionV15IntegrationError(
                "Python resolvió una copia distinta de Admisión V15: "
                f"{loaded_package_root}"
            )

    loaded_build_id = str(
        getattr(application_module, "V15_SOURCE_BUILD_ID", "") or ""
    ).strip()
    if loaded_build_id != _EXPECTED_V15_SOURCE_BUILD_ID:
        logger.error(
            "V15_LOAD_ERROR expected_build_id=%s loaded_build_id=%s "
            "loaded_module_file=%s resolved_v15_root=%s frozen=%s",
            _EXPECTED_V15_SOURCE_BUILD_ID,
            loaded_build_id or "SIN_MARCADOR",
            _module_location(application_module),
            root,
            frozen,
        )
        raise AdmissionV15IntegrationError(
            "La compilación cargó una fuente V15 distinta o antigua. "
            f"Esperada={_EXPECTED_V15_SOURCE_BUILD_ID}; cargada={loaded_build_id or 'SIN_MARCADOR'}; "
            f"ruta={_module_location(application_module)}"
        )

    logger.info(
        "V15_LOAD_OK build_id=%s loaded_module_file=%s "
        "resolved_v15_root=%s frozen=%s",
        loaded_build_id,
        _module_location(application_module),
        root,
        frozen,
    )

    # V15 conserva su interfaz y sus SVG originales. Solo se unifica la
    # identidad institucional que V15 usa en hojas y vistas con logo.
    institutional_logo = get_app_logo_path()
    if institutional_logo:
        application_module.LOGO_PATH = institutional_logo

    return _V15Modules(
        context_class=context_module.AdmissionContext,
        widget_class=widget_module.AdmissionWidget,
        database_class=application_module.DatabaseManager,
        gateway_error_class=gateway_module.MainAppGatewayError,
        representative_class=gateway_module.MainAppRepresentative,
    )


def load_v15_application_module(v15_root: Path = DEFAULT_V15_ROOT):
    """Return the certified V15 PDF/resource module without creating a widget."""
    _load_v15_modules(Path(v15_root))
    return importlib.import_module(f"{_V15_PACKAGE}.facturacion_tabs_pyside6")


def _load_legacy_admission_module():
    """Carga la fuente SQLite incluida cuando la copia V15 ya no existe.

    El fallback es deliberadamente interno: no depende de una ruta del equipo
    de desarrollo ni abre un proceso Tk adicional.
    """
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    candidates = (
        bundle_root / "admission_source" / "facturacion_tabs.py",
        Path(__file__).resolve().parent / "admission_source" / "facturacion_tabs.py",
    )
    source = next((item for item in candidates if item.is_file()), None)
    if source is None:
        raise AdmissionV15IntegrationError(
            "No se encontr\u00f3 el motor SQLite incluido de Admisi\u00f3n."
        )
    root = str(source.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    module_name = "hospital_embedded_admission_legacy"
    module = sys.modules.get(module_name)
    if module is not None:
        return module
    spec = importlib.util.spec_from_file_location(module_name, source)
    if spec is None or spec.loader is None:
        raise AdmissionV15IntegrationError("No se pudo preparar el motor SQLite de Admisi\u00f3n.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(module_name, None)
        raise AdmissionV15IntegrationError(
            f"No se pudo iniciar el motor SQLite de Admisi\u00f3n ({type(exc).__name__})."
        ) from exc
    return module


class EmbeddedMainAppGateway:
    """Implementa el contrato de V15 sin pipe, subprocess ni otra sesión."""

    def __init__(
        self,
        *,
        current_user: Mapping[str, Any],
        session_checker: Callable[[], bool],
        users_provider: Callable[[], Iterable[Mapping[str, Any]]],
        credential_verifier: Callable[[str, str], Mapping[str, Any] | None],
        audit_callback: Callable[[str, str], None] | None,
        gateway_error_class: type[Exception],
        representative_class: type,
    ):
        self._current_user = dict(current_user or {})
        self._session_checker = session_checker
        self._users_provider = users_provider
        self._credential_verifier = credential_verifier
        self._audit_callback = audit_callback
        self._gateway_error = gateway_error_class
        self._representative = representative_class
        self._representatives_cache: tuple[Any, ...] = ()
        self._representatives_cache_lock = threading.Lock()

    @property
    def available(self) -> bool:
        return True

    def list_active_system_users(self) -> list[Any]:
        """Read every enabled main-application user without session lookups."""
        result = []
        for raw in self._users_provider() or ():
            data = dict(raw or {})
            enabled = data.get("enabled", data.get("is_active"))
            if str(enabled).strip().casefold() in {"", "0", "false", "no", "none"}:
                continue
            username = _clean(data.get("username"), 80)
            full_name = _clean(
                data.get("display_name")
                or data.get("full_name")
                or data.get("nombre_completo")
                or username,
                160,
            )
            role = _clean(data.get("role"), 80)
            user_id = _clean(data.get("id", data.get("user_id")), 80)
            if username:
                try:
                    result.append(
                        self._representative(username, full_name, role, user_id)
                    )
                except TypeError:
                    # Compatible con el contrato V15 anterior durante el
                    # arranque desde fuente; el adaptador conserva el user_id
                    # cuando la clase certificada ya lo admite.
                    result.append(self._representative(username, full_name, role))
        return sorted(
            result,
            key=lambda item: (item.full_name.casefold(), item.username.casefold()),
        )

    def list_assignable_admission_representatives(self) -> list[Any]:
        """Compatibility name for the all-active-user configuration catalogue."""
        return self.list_active_system_users()

    def list_active_administrators(self) -> list[Any]:
        """Enabled Administrators from the canonical main-system catalogue."""
        from admission_hybrid import ADMISSION_ROLE_ADMINISTRATOR, canonical_role

        return [
            user
            for user in self.list_active_system_users()
            if canonical_role({"role": getattr(user, "role", "")})
            == ADMISSION_ROLE_ADMINISTRATOR
        ]

    # Alias privado conservado para extensiones V15 que aún lo invocan.
    _active_representatives = list_assignable_admission_representatives

    def cached_representatives(self) -> list[Any]:
        """Return the last successful administrative list without I/O."""
        with self._representatives_cache_lock:
            return list(self._representatives_cache)

    def cached_active_administrators(self) -> list[Any]:
        """Administrators already loaded by the UI-first users worker."""
        from admission_hybrid import ADMISSION_ROLE_ADMINISTRATOR, canonical_role

        return [
            user
            for user in self.cached_representatives()
            if canonical_role({"role": getattr(user, "role", "")})
            == ADMISSION_ROLE_ADMINISTRATOR
        ]

    def session_status(self):
        if not self._session_checker():
            raise self._gateway_error("La sesión principal ya no está disponible.")
        username = _clean(self._current_user.get("username"), 80)
        full_name = _clean(
            self._current_user.get("full_name")
            or self._current_user.get("nombre_completo")
            or username,
            160,
        )
        role = _clean(self._current_user.get("role"), 80).casefold()
        if not username:
            raise self._gateway_error("La sesión principal no tiene usuario.")
        return self._representative(username, full_name, role)

    def list_representatives(self):
        started = perf_counter()
        logger = logging.getLogger("hospital.admission.config")
        try:
            logger.info(
                "CONFIG_USERS_QUERY_START thread=%s",
                threading.current_thread().name,
            )
            representatives = self.list_active_system_users()
            with self._representatives_cache_lock:
                self._representatives_cache = tuple(representatives)
            logger.info(
                "CONFIG_USERS_QUERY_DONE elapsed_ms=%.1f count=%s thread=%s",
                (perf_counter() - started) * 1000.0,
                len(representatives),
                threading.current_thread().name,
            )
            return representatives
        except self._gateway_error:
            raise
        except Exception as exc:
            logger.error(
                "CONFIG_USERS_LOAD_ERROR elapsed_ms=%.1f thread=%s type=%s",
                (perf_counter() - started) * 1000.0,
                threading.current_thread().name,
                type(exc).__name__,
                exc_info=True,
            )
            raise self._gateway_error(
                "No se pudo cargar la lista de usuarios del sistema."
            ) from exc


    @staticmethod
    def _user_identity(user: Any) -> dict[str, Any]:
        return {
            "user_id": getattr(user, "user_id", None)
            or getattr(user, "id", None)
            or "",
            "username": getattr(user, "username", ""),
        }

    def authorize_admin_action(
        self,
        *,
        selected_admin_user_id: Any,
        selected_admin_username: str,
        password: str,
        action: str,
        target_user_id: Any = None,
        target_username: str = "",
    ):
        """Validate an explicit enabled Admin; no Admin login is required."""
        from admission_hybrid import (
            ADMISSION_ROLE_ADMINISTRATOR,
            canonical_role,
            same_user,
        )

        selected_admin_id = _clean(selected_admin_user_id, 80)
        selected_admin_username = _clean(selected_admin_username, 80)
        target_user_id = _clean(target_user_id, 80)
        target_username = _clean(target_username, 80)
        secret = str(password or "")[:512]

        if not self._session_checker():
            secret = ""
            raise self._gateway_error("La sesión solicitante ya no está disponible.")

        active_users = self.list_active_system_users()
        selected_identity = {
            "user_id": selected_admin_id,
            "username": selected_admin_username,
        }
        selected_admin = next(
            (
                user for user in active_users
                if same_user(selected_identity, self._user_identity(user))
            ),
            None,
        )
        if selected_admin is None:
            secret = ""
            raise self._gateway_error(
                "El Administrador seleccionado ya no está habilitado."
            )
        if canonical_role({"role": getattr(selected_admin, "role", "")}) \
                != ADMISSION_ROLE_ADMINISTRATOR:
            secret = ""
            raise self._gateway_error(
                "La cuenta seleccionada no tiene rol Administrador."
            )

        try:
            verified = self._credential_verifier(selected_admin.username, secret)
        finally:
            secret = ""

        if not verified:
            raise self._gateway_error("Credenciales de Administrador incorrectas.")

        verified = dict(verified or {})
        verified_enabled = verified.get("enabled", verified.get("is_active"))
        if str(verified_enabled).strip().casefold() in {"", "0", "false", "no", "none"}:
            raise self._gateway_error(
                "El Administrador seleccionado ya no está habilitado."
            )
        if canonical_role(verified) != ADMISSION_ROLE_ADMINISTRATOR:
            raise self._gateway_error(
                "La cuenta seleccionada no tiene rol Administrador."
            )
        if not same_user(
            self._user_identity(selected_admin),
            {"user_id": verified.get("id", verified.get("user_id")),
             "username": verified.get("username") or selected_admin.username},
        ):
            raise self._gateway_error(
                "Credenciales de Administrador incorrectas."
            )

        target = next(
            (
                item for item in active_users
                if same_user(
                    {"user_id": target_user_id, "username": target_username},
                    self._user_identity(item),
                )
            ),
            None,
        )
        if target is None:
            raise self._gateway_error(
                "El representante seleccionado ya no está habilitado en Facturación."
            )

        if self._audit_callback is not None:
            requesting_id = _clean(
                self._current_user.get("id", self._current_user.get("user_id")), 80
            )
            self._audit_callback(
                selected_admin.username,
                "Autorizó corrección de representante de Admisión "
                f"requesting_user_id={requesting_id}; "
                f"authorizing_admin_user_id={getattr(selected_admin, 'user_id', '')}; "
                f"target_representative_user_id={getattr(target, 'user_id', '')}; "
                f"action={_clean(action, 100)}",
            )

        return selected_admin, target

    def authorize_shift_change(
        self, *, username: str, password: str, target_username: str
    ):
        """Compatibility wrapper for older V15 callers."""
        admin, target = self.authorize_admin_action(
            selected_admin_user_id="",
            selected_admin_username=username,
            password=password,
            action="CORRECT_ADMISSION_REPRESENTATIVE",
            target_username=target_username,
        )
        return admin.username, target


class AdmissionV15EventAdapter(QObject):
    """Proyecta referencias V15 y notifica a Facturación sin leer su UI."""

    projection_changed = Signal(object)
    shift_changed = Signal(object)
    shift_closed = Signal(str, int)
    failed = Signal(str)

    def __init__(
        self,
        event_bus: Any,
        *,
        repository: AdmissionReadOnlyRepository,
        projection_sync: Callable[[Iterable[Any]], int],
        logger: logging.Logger | None = None,
        defer_to_sync: bool = False,
        parent=None,
    ):
        super().__init__(parent)
        self.event_bus = event_bus
        self.repository = repository
        self.projection_sync = projection_sync
        self.defer_to_sync = bool(defer_to_sync)
        self.logger = logger or logging.getLogger("hospital.admission.v15.events")
        self._processed_event_uuids: set[str] = set()
        self._connect_bus()

    def _connect_bus(self) -> None:
        bindings = {
            "attention_created": self._attention_created,
            "attention_updated": self._attention_updated,
            "attention_cancelled": self._attention_cancelled,
            "detail_sheet_generated": self._detail_sheet_generated,
            "shift_changed": self._shift_changed,
            "shift_closed": self._shift_closed,
        }
        for signal_name, slot in bindings.items():
            signal = getattr(self.event_bus, signal_name, None)
            if signal is not None and hasattr(signal, "connect"):
                signal.connect(slot)

    @staticmethod
    def _mapping(value: Any) -> dict[str, Any]:
        if isinstance(value, Mapping):
            return dict(value)
        fields = (
            "source_instance_id",
            "attention_id",
            "event_uuid",
            "event_type",
            "turn_id",
            "operational_day_id",
            "shift_type",
            "representative",
            "closed_at",
        )
        return {
            field: getattr(value, field)
            for field in fields
            if hasattr(value, field)
        }

    def _attention_ref(self, value: Any, kind: str) -> AdmissionEventRef:
        data = self._mapping(value)
        reference = AdmissionEventRef(
            source_instance_id=str(data.get("source_instance_id") or "").strip(),
            attention_id=int(data.get("attention_id") or 0),
            event_uuid=str(data.get("event_uuid") or "").strip(),
            event_type=str(data.get("event_type") or kind).strip(),
        )
        if not reference.source_instance_id or reference.attention_id <= 0:
            raise ValueError("V15 emitió una referencia de atención incompleta.")
        return reference

    def _project_attention(self, value: Any, kind: str) -> None:
        try:
            reference = self._attention_ref(value, kind)
            if (
                reference.event_uuid
                and reference.event_uuid in self._processed_event_uuids
            ):
                return
            if reference.event_uuid:
                self._processed_event_uuids.add(reference.event_uuid)
                if len(self._processed_event_uuids) > 2048:
                    self._processed_event_uuids.clear()
                    self._processed_event_uuids.add(reference.event_uuid)
            if self.defer_to_sync:
                # El evento durable ya está en SQLite; el coordinador híbrido
                # realizará la proyección cloud en su worker incremental.
                return
            attention = self.repository.get_attention_by_identity(
                reference.source_instance_id,
                reference.attention_id,
            )
            if attention is None:
                raise LookupError(
                    "La atención indicada por V15 no existe en su fuente central."
                )
            self.projection_sync([attention])
            self.projection_changed.emit(
                {
                    "source_instance_id": reference.source_instance_id,
                    "attention_id": reference.attention_id,
                    "event_uuid": reference.event_uuid,
                    "event_type": kind,
                }
            )
        except Exception as exc:
            self.logger.exception("Falló el contrato V15 %s", kind)
            self.failed.emit(f"{kind}: {type(exc).__name__}")

    @Slot(object)
    def _attention_created(self, value: Any) -> None:
        self._project_attention(value, "attention_created")

    @Slot(object)
    def _attention_updated(self, value: Any) -> None:
        self._project_attention(value, "attention_updated")

    @Slot(object)
    def _attention_cancelled(self, value: Any) -> None:
        self._project_attention(value, "attention_cancelled")

    @Slot(object)
    def _detail_sheet_generated(self, value: Any) -> None:
        self._project_attention(value, "detail_sheet_generated")

    @Slot(object)
    def _shift_changed(self, value: Any) -> None:
        try:
            data = self._mapping(value)
            reference = ShiftEventRef(
                source_instance_id=str(data.get("source_instance_id") or "").strip(),
                turn_id=int(data.get("turn_id") or 0),
                operational_day_id=int(data.get("operational_day_id") or 0),
                shift_type=str(data.get("shift_type") or "").strip(),
                representative=str(data.get("representative") or "").strip(),
                closed_at=str(data.get("closed_at") or "").strip(),
                event_uuid=str(data.get("event_uuid") or "").strip(),
            )
            if not reference.source_instance_id or reference.turn_id <= 0:
                raise ValueError("V15 emitió una referencia de turno incompleta.")
            self.shift_changed.emit(reference)
        except Exception as exc:
            self.logger.exception("Falló el contrato V15 shift_changed")
            self.failed.emit(f"shift_changed: {type(exc).__name__}")

    @Slot(object)
    def _shift_closed(self, value: Any) -> None:
        try:
            data = self._mapping(value)
            source_instance_id = str(data.get("source_instance_id") or "").strip()
            turn_id = int(data.get("turn_id") or 0)
            if not source_instance_id or turn_id <= 0:
                raise ValueError("V15 emitió un cierre de turno incompleto.")
            self.shift_closed.emit(source_instance_id, turn_id)
        except Exception as exc:
            self.logger.exception("Falló el contrato V15 shift_closed")
            self.failed.emit(f"shift_closed: {type(exc).__name__}")


class AdmissionV15Factory:
    """Construye una sola instancia V15 con el contexto vigente del host."""

    def __init__(
        self,
        host_context: Any,
        *,
        session_checker: Callable[[], bool],
        users_provider: Callable[[], Iterable[Mapping[str, Any]]],
        credential_verifier: Callable[[str, str], Mapping[str, Any] | None],
        audit_callback: Callable[[str, str], None] | None = None,
        v15_root: Path = DEFAULT_V15_ROOT,
    ):
        self.host_context = host_context
        self.session_checker = session_checker
        self.users_provider = users_provider
        self.credential_verifier = credential_verifier
        self.audit_callback = audit_callback
        self.v15_root = Path(v15_root)
        self.modules: _V15Modules | None = None
        self.context: Any = None

    def _create_internal_fallback_widget(self, parent=None):
        """Construye la UI PySide6 incluida, sin requerir una carpeta externa."""
        from admission_hybrid import (
            AdmissionCloudRepository,
            AdmissionSyncService,
            AdmissionWriteGuard,
            DatabaseConfigurationMissing,
            DatabaseTemporarilyOffline,
            OfflineAdmissionStore,
            OperationalSessionService,
            StationRole,
            is_temporary_connection_error,
        )
        from admission_pyside6 import (
            AdmissionController,
            AdmissionDocumentService,
            AdmissionRepository,
            AdmissionService,
            AdmissionWidget,
            AppContext,
            LegacyAdmissionBackend,
        )

        host = self.host_context
        legacy_module = _load_legacy_admission_module()
        database = legacy_module.DatabaseManager()
        store = OfflineAdmissionStore(database.db_name)
        guard = AdmissionWriteGuard()
        operational = None
        role = StationRole.NONE
        offline = False
        lease_valid = False
        status = "Conectado"
        try:
            session_service = OperationalSessionService(host.connection_factory)
            session_service.ensure_schema()
            attachment = session_service.attach_device(
                login_username=str(host.user.get("username") or ""),
                login_user_id=host.user.get("id", host.user.get("user_id")),
                device_id=str(host.device_id),
                login_session_id=str(host.session_id),
                device_name=str(getattr(host, "device_name", "")),
                turn_id=(getattr(host, "current_shift", {}) or {}).get("turn_id"),
                login_display_name=str(host.user.get("full_name") or host.user.get("username") or ""),
                login_role=host.user.get("role"),
            )
            operational, role = attachment.operational_session, attachment.role
            store.cache_operational_session(operational, role)
            store.configure_runtime_context(
                operational,
                device_id=str(host.device_id),
                actor_user_id=host.user.get("id", host.user.get("user_id")),
                actor_username=str(host.user.get("username") or ""),
            )
            status = attachment.message or "Conectado"
        except Exception as exc:
            # El contenedor de Admisión debe poder abrirse aunque la autoridad
            # central no esté disponible. Solo se toleran fallos de
            # conectividad/configuración (y el proveedor de pruebas sin
            # conexión); cualquier defecto de la lógica sigue propagándose.
            no_connection_provider = (
                isinstance(exc, TypeError)
                and "context manager" in str(exc).casefold()
            )
            if not (
                is_temporary_connection_error(exc)
                or isinstance(exc, (DatabaseTemporarilyOffline, DatabaseConfigurationMissing))
                or no_connection_provider
            ):
                raise
            cached = store.cached_attachment()
            if cached is not None:
                operational, role = cached.operational_session, cached.role
                lease_valid = bool(cached.writable)
            offline = True
            status = "Sin conexi\u00f3n \u00b7 trabajando localmente"

        context = AppContext(
            connection_factory=host.connection_factory,
            user=host.user,
            session_id=host.session_id,
            device_id=host.device_id,
            device_name=getattr(host, "device_name", ""),
            current_shift=getattr(host, "current_shift", {}),
            configuration={**dict(getattr(host, "configuration", {}) or {}),
                           "connection_status": status},
            logger=getattr(host, "logger", logging.getLogger("hospital.admission")),
            # El bus pertenece al shell: usar el mismo evita un segundo canal
            # de eventos y mantiene Facturación informada también en modo
            # offline/fallback.
            event_bus=getattr(host, "event_bus", None) or None,
            operational_session=operational,
            station_role=role,
            write_guard=guard,
            offline=offline,
            offline_lease_valid=lease_valid,
            sync_store=store,
            sync_service=AdmissionSyncService(
                store, AdmissionCloudRepository(host.connection_factory)
            ),
        )
        backend = LegacyAdmissionBackend(
            database,
            shift_provider=lambda: dict(context.current_shift or {}),
            username_provider=lambda: context.username,
            module=legacy_module,
        )
        repository = AdmissionRepository(backend)
        service = AdmissionService(context, repository)
        documents = AdmissionDocumentService(repository, context.configuration, parent)
        controller = AdmissionController(service, parent, documents)
        self.context = context
        return AdmissionWidget(context, controller, parent)

    def create_widget(self, parent=None):
        try:
            modules = _load_v15_modules(self.v15_root)
        except AdmissionV15IntegrationError:
            if os.environ.get("HOSPITAL_ALLOW_LEGACY_ADMISSION_FALLBACK") == "1":
                return self._create_internal_fallback_widget(parent)
            raise
        self.modules = modules
        host = self.host_context
        logger = getattr(host, "logger", None) or logging.getLogger(
            "hospital.admission.v15"
        )
        _bind_operational_file_logging(logger, modules.database_class)
        hybrid = _HybridAdmissionRuntime(host)
        gateway = EmbeddedMainAppGateway(
            current_user=getattr(host, "user", {}),
            session_checker=self.session_checker,
            users_provider=self.users_provider,
            credential_verifier=self.credential_verifier,
            audit_callback=self.audit_callback,
            gateway_error_class=modules.gateway_error_class,
            representative_class=modules.representative_class,
        )
        def create_database(session):
            database = modules.database_class(
                session_context=session,
                event_bus=getattr(host, "event_bus", None),
            )
            hybrid.bind_database(database)
            return _HybridDatabaseProxy(database, hybrid)

        def resolve_capability(capability: str) -> bool:
            capability = str(capability)
            # La capacidad funcional pertenece al rol autenticado. El estado
            # operativo es transitorio y se valida en el proxy antes de toda
            # mutación; no se omiten controles durante la construcción porque
            # eso dejaba el modo solo lectura pegado tras una transición.
            return capability in v15_capabilities_for_role(host.user)

        configuration = dict(getattr(host, "configuration", {}) or {})
        configuration["admission_hybrid"] = hybrid.state()
        self.context = modules.context_class(
            connection_factory=host.connection_factory,
            user=host.user,
            session_id=host.session_id,
            device_id=host.device_id,
            device_name=getattr(host, "device_name", ""),
            admission_database_factory=create_database,
            main_app_gateway=gateway,
            configuration=configuration,
            current_shift=getattr(host, "current_shift", {}),
            logger=logger,
            event_bus=getattr(host, "event_bus", None),
            permission_resolver=resolve_capability,
            embedded=True,
        )
        try:
            widget = modules.widget_class(parent, context=self.context)
            # V15 keeps its own SVG family. The shell decorator also recognizes
            # this property when the widget is used standalone.
            widget.setProperty("preserveOriginalIcons", True)
            widget.setProperty("admissionV15Source", str(self.v15_root.resolve()))
            status_label = QLabel(widget)
            status_label.setObjectName("admissionHybridStatus")
            status_label.setWordWrap(True)
            status_label.setMinimumHeight(28)
            layout = widget.layout()
            if layout is not None:
                layout.insertWidget(0, status_label)

            coordinator = _HybridCoordinator(hybrid, widget)
            admission = getattr(widget, "admission", None)
            refresh_controller = _V15BackgroundRefreshCoordinator(
                admission, coordinator, widget
            ) if admission is not None else None
            admission_database = getattr(admission, "db", None) if admission is not None else None
            excel_refresh = (
                _HybridExcelRefreshCoordinator(
                    admission_database,
                    coordinator,
                    getattr(admission, "_retry_excel_export_jobs", None),
                    widget,
                )
                if admission_database is not None
                and callable(getattr(admission_database, "refresh_excel_from_canonical_dataset", None))
                else None
            )
            summary_state_key = {"value": None}

            if admission is not None:
                v15_module = sys.modules.get(admission.__class__.__module__)

                def cancel_selected_async(
                    admission_self,
                    tree,
                    reordenar_ids=False,
                    refrescar_callback=None,
                ):
                    del reordenar_ids
                    capability = getattr(v15_module, "CAP_VOID_RECORDS", "records.void")
                    if not admission_self._exigir_permiso(
                        capability, "anular una atencion"
                    ):
                        return
                    selected_items = tree.selection()
                    if not selected_items:
                        admission_self._mostrar_dialogo_modal_unico(
                            "Historial", "Seleccione un registro para anular."
                        )
                        return
                    attention_id = int(tree.item(selected_items[0], "values")[0])
                    attention = admission_self.db.obtener_atencion_por_id(attention_id)
                    if not attention:
                        v15_module.messagebox.showwarning(
                            "Aviso", "No se encontro el registro."
                        )
                        return
                    affects_excel = admission_self._registro_esta_en_turno_actual(
                        attention
                    )
                    if not v15_module.messagebox.askyesno(
                        "Confirmacion",
                        "¿Anular esta atencion?\n\n"
                        f"Paciente: {(attention.get('nombre') or '').upper()}\n"
                        f"Fecha: {attention.get('fecha', '')} {attention.get('hora', '')}\n"
                        f"Especialidad: {attention.get('hoja', '')}\n\n"
                        "Tambien se actualizara el Excel si pertenece al turno actual.",
                    ):
                        return
                    reason = v15_module.simpledialog.askstring(
                        "Motivo de anulacion",
                        "Indique brevemente por que se anula esta atencion:",
                        parent=admission_self.root,
                    )
                    reason = str(reason or "").strip()
                    if len(reason) < 5:
                        if reason:
                            v15_module.messagebox.showwarning(
                                "Motivo requerido",
                                "Escriba un motivo de al menos 5 caracteres.",
                                parent=tree.winfo_toplevel(),
                            )
                        return
                    actor = admission_self._actor_actual()
                    shift_config = v15_module.cargar_turno_config() or {}
                    username = actor or shift_config.get("representante", "")
                    admission_self._mostrar_notificacion("Anulando atencion...")

                    def operation():
                        return admission_self.db.borrar_atencion(
                            attention_id, motivo=reason, usuario=username
                        )

                    def succeeded(cancelled):
                        if not cancelled:
                            v15_module.messagebox.showwarning(
                                "Aviso", "No se pudo anular. Intente nuevamente."
                            )
                            return
                        attention_type = str(
                            attention.get("tipo_atencion") or "EMERGENCIA"
                        ).strip().upper()
                        if affects_excel and attention_type not in {
                            "URGENCIA", "CONSULTA"
                        }:
                            excel_refresh.request() if excel_refresh is not None else None
                        if refrescar_callback:
                            try:
                                refrescar_callback()
                            except Exception:  # noqa: BLE001 - fallback UI boundary
                                admission_self.cargar_tabla_filtrada(tree)
                        else:
                            admission_self.cargar_tabla_filtrada(tree)
                        if refresh_controller is not None:
                            refresh_controller.request_summary()
                        admission_self._mostrar_notificacion(
                            f"Atencion #{attention_id} anulada."
                        )

                    def failed(_error_code):
                        v15_module.messagebox.showwarning(
                            "No se puede anular",
                            "No fue posible confirmar la anulacion central.",
                            parent=admission_self.root,
                        )

                    coordinator.submit_background(operation, succeeded, failed)

                admission.eliminar_atencion_seleccionada = MethodType(
                    cancel_selected_async, admission
                )

            if admission is not None and refresh_controller is not None:
                admission._hybrid_coordinator = coordinator
                admission._hybrid_refresh_controller = refresh_controller
                cedula_entry = getattr(admission, "entry_cedula", None)
                nss_entry = getattr(admission, "entry_nss", None)
                if cedula_entry is not None:
                    cedula_entry.unbind("<KeyRelease>")
                    cedula_entry.unbind("<FocusOut>")
                    cedula_entry.unbind("<Return>")
                    cedula_entry.bind(
                        "<KeyRelease>",
                        lambda _event: (
                            admission.limitar_caracteres(cedula_entry, 11),
                            admission._try_autocomplete_cedula(),
                        ),
                    )
                    cedula_entry.bind(
                        "<FocusOut>",
                        lambda _event: admission.auto_completar(input_method="TAB"),
                    )
                    cedula_entry.bind(
                        "<Return>",
                        lambda _event: admission.auto_completar(input_method="ENTER"),
                    )
                if nss_entry is not None:
                    nss_entry.unbind("<KeyRelease>")
                    nss_entry.unbind("<FocusOut>")
                    nss_entry.unbind("<Return>")
                    nss_entry.bind(
                        "<KeyRelease>",
                        lambda _event: (
                            admission._actualizar_deteccion_seguro(),
                            admission._try_autocomplete_nss(),
                        ),
                    )
                    nss_entry.bind(
                        "<FocusOut>",
                        lambda _event: admission.auto_completar_por_nss(input_method="TAB"),
                    )
                    nss_entry.bind(
                        "<Return>",
                        lambda _event: admission.auto_completar_por_nss(input_method="ENTER"),
                    )

            def apply_state(value: Mapping[str, Any]) -> None:
                state = dict(value or {})
                writable = bool(state.get("writable"))
                offline = bool(state.get("offline"))
                role = str(state.get("role") or "NONE")
                active_user = str(state.get("active_username") or "")
                sync_state = str(state.get("sync_state") or "").upper()
                reason_code = str(state.get("reason_code") or "").upper()
                if sync_state in {"RECONCILING", "LOCAL_MIRROR_PENDING"}:
                    station = "Principal" if role == "PRIMARY" else "Secundaria"
                    text = f"Conectado · {station} · Actualizando turno..."
                    colors = ("#FFF4D6", "#7A4E00", "#E5C36A")
                elif sync_state == "ERROR_RETRYABLE":
                    text = "Conectado · actualización local pendiente"
                    colors = ("#FFF4D6", "#7A4E00", "#E5C36A")
                elif reason_code in {"AUX_NOT_ADMISSION_USER", "READONLY_AUXILIARY"}:
                    display = str(
                        state.get("active_user_display_name") or active_user
                    )
                    text = "Admisión continúa operando con " + display
                    colors = ("#EEF3F8", "#334E68", "#BCCCDC")
                elif reason_code == "WAITING_ADMISSION_OPERATOR":
                    text = "Admisión espera un usuario operativo autorizado"
                    colors = ("#EEF3F8", "#334E68", "#BCCCDC")
                elif not writable:
                    text = (
                        "Solo lectura · usuario operativo: " + active_user
                        if active_user
                        else "Solo lectura · sesión operativa no validada"
                    )
                    colors = ("#FDECEC", "#9B1C1C", "#E5A3A3")
                elif offline:
                    station = "Principal" if role == "PRIMARY" else "Secundaria"
                    pending = int(state.get("pending_sync_count") or 0)
                    text = f"Sin conexión · {station} · {pending} pendientes"
                    colors = ("#FFF4D6", "#7A4E00", "#E5C36A")
                else:
                    text, colors = _online_sync_status(
                        role, int(state.get("pending_sync_count") or 0)
                    )
                status_label.setText(text)
                status_label.setStyleSheet(
                    f"background:{colors[0]};color:{colors[1]};"
                    f"border:1px solid {colors[2]};padding:4px 10px;font-weight:600;"
                )
                widget.setProperty("admissionReadOnly", not writable)
                self.context.configuration["admission_hybrid"] = state
                admission = getattr(widget, "admission", None)
                connection_var = getattr(admission, "connection_var", None)
                if connection_var is not None and hasattr(connection_var, "set"):
                    connection_var.set(text)
                if admission is not None:
                    apply_snapshot = getattr(
                        admission, "apply_operational_snapshot", None
                    )
                    if callable(apply_snapshot):
                        apply_snapshot(state)
                    else:
                        admission.current_shift_context = dict(
                            getattr(self.context, "current_shift", {}) or {}
                        )
                        refresh_turn_visual = getattr(
                            admission, "_actualizar_turno_visual_en_vivo", None
                        )
                        if callable(refresh_turn_visual):
                            refresh_turn_visual()
                    for entry in tuple(getattr(admission, "all_entries", ()) or ()):
                        if hasattr(entry, "configure"):
                            entry.configure(state="normal" if writable else "disabled")
                    clear_button = getattr(admission, "boton_limpiar", None)
                    if clear_button is not None and hasattr(clear_button, "configure"):
                        clear_button.configure(
                            state="normal" if writable else "disabled"
                        )
                    turn_button = getattr(admission, "boton_cambiar_turno", None)
                    if turn_button is not None and hasattr(turn_button, "configure"):
                        turn_button.configure(
                            state=(
                                "normal"
                                if bool(state.get("can_change_turn")) and not offline
                                else "disabled"
                            )
                        )
                    pdf_button = getattr(admission, "boton_generar_pdf", None)
                    if pdf_button is not None and hasattr(pdf_button, "configure"):
                        pdf_button.configure(
                            state="normal" if writable else "disabled"
                        )
                    state_key = (
                        state.get("turn_id"), state.get("generation"),
                        state.get("active_user_id"),
                    )
                    if (
                        refresh_controller is not None
                        and state_key != summary_state_key["value"]
                    ):
                        summary_state_key["value"] = state_key
                        refresh_controller.request_summary("operational_state_changed")
                        if excel_refresh is not None:
                            excel_refresh.request()
                if state.get("force_logout_required") and not hybrid._force_logout_emitted:
                    hybrid._force_logout_emitted = True
                    callback = getattr(host, "force_logout_callback", None)
                    if callable(callback):
                        QTimer.singleShot(0, lambda s=dict(state): callback(s))

            coordinator.state_changed.connect(apply_state)
            event_bus = getattr(host, "event_bus", None)
            history_signal = getattr(event_bus, "history_refresh_requested", None)
            for signal_name in (
                "attention_created",
                "attention_updated",
                "attention_cancelled",
                "detail_sheet_generated",
            ):
                signal = getattr(event_bus, signal_name, None)
                if signal is not None and hasattr(signal, "connect"):
                    signal.connect(lambda _value=None, c=coordinator: c._schedule())
                    if refresh_controller is not None:
                        refresh_reason = {
                            "attention_created": "attention_created",
                            "attention_cancelled": "attention_voided",
                            "attention_updated": "attention_updated",
                            "detail_sheet_generated": "detail_sheet_generated",
                        }[signal_name]
                        signal.connect(
                            lambda _value=None, r=refresh_controller, reason=refresh_reason:
                                r.request_summary(reason)
                        )
                    if (
                        signal_name != "detail_sheet_generated"
                        and history_signal is not None
                        and hasattr(history_signal, "emit")
                    ):
                        signal.connect(
                            lambda _value=None, h=history_signal: h.emit()
                        )
                    if excel_refresh is not None:
                        signal.connect(lambda _value=None, x=excel_refresh: x.request())

            # History is the visible, canonical replica refresh.  Bind the
            # sidebar to that same signal so local saves and materialized
            # remote events update both views together.  The refresh
            # controller coalesces duplicate attention/history notifications.
            if history_signal is not None and hasattr(history_signal, "connect"):
                def refresh_summary_after_history_event() -> None:
                    refresh = getattr(admission, "request_turn_summary_refresh", None)
                    if callable(refresh):
                        refresh("history_dataset_changed")
                    elif refresh_controller is not None:
                        refresh_controller.request_summary("history_dataset_changed")

                history_signal.connect(refresh_summary_after_history_event)

            def refresh_after_sync(result: Mapping[str, Any]) -> None:
                if refresh_controller is not None:
                    refresh_controller.request_summary("sync_applied")
                changed = any(
                    int(dict(result or {}).get(name) or 0) > 0
                    for name in (
                        "pulled",
                        "recovered",
                        "replayed",
                        "backfilled",
                        "reconciled",
                    )
                )
                if changed and excel_refresh is not None:
                    excel_refresh.request()
                if not changed:
                    return
                if history_signal is not None and hasattr(history_signal, "emit"):
                    history_signal.emit()

            coordinator.sync_finished.connect(refresh_after_sync)
            coordinator.failed.connect(
                lambda code: logger.error("Sincronización V15 detenida: %s", code)
            )
            widget.destroyed.connect(coordinator.stop)
            widget._hybrid_runtime = hybrid
            widget._hybrid_coordinator = coordinator
            widget._hybrid_refresh_controller = refresh_controller
            widget._hybrid_excel_refresh = excel_refresh
            _bind_hybrid_shutdown(
                widget, hybrid, coordinator, refresh_controller, excel_refresh
            )
            apply_state(hybrid.state())
            coordinator.start()
            return widget
        except Exception as exc:
            logger.exception("No se pudo construir AdmissionWidget V15")
            raise AdmissionV15IntegrationError(
                f"No se pudo construir AdmissionWidget V15 ({type(exc).__name__})."
            ) from exc


__all__ = [
    "AdmissionV15EventAdapter",
    "AdmissionV15EventBus",
    "AdmissionV15Factory",
    "AdmissionV15IntegrationError",
    "EmbeddedMainAppGateway",
    "TurnDatasetResult",
    "TurnSummarySnapshot",
]
