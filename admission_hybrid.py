"""Coordinaci\u00f3n central/offline para Admisi\u00f3n.

El m\u00f3dulo no comparte archivos SQLite ni mezcla la sesi\u00f3n de login con
la sesi\u00f3n operativa.  Sus objetos no dependen de Qt, por lo que pueden ser
usados por las dos interfaces de Admisi\u00f3n y probados sin una ventana abierta.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any

from admission_contract import (
    assess_billing_readiness,
    assess_coverage,
    normalize_service_type,
)
from patient_directory import (
    POSTGRES_PATIENT_DIRECTORY_SCHEMA,
    upsert_patient_from_attention_connection,
)
from sqlite_write_coordinator import (
    connect_local_sqlite,
    prepare_sqlite_database,
)

MAX_ACTIVE_SESSION_DEVICES = 2
DEFAULT_OFFLINE_LEASE_SECONDS = 15 * 60
SYNC_TICK_SECONDS = 2
OFFLINE_LOGIN_VALID_DAYS = 30
LOCAL_SYNC_APPLY_BATCH_SIZE = 50
MAX_CLOCK_DRIFT_MS = 5 * 60 * 1000
OPERATIONAL_LOG = logging.getLogger("hospital.admission.operational")


class AdmissionHybridError(RuntimeError):
    """Base para los errores que la interfaz puede presentar de forma segura."""


class DatabaseConfigurationMissing(AdmissionHybridError):
    code = "DATABASE_CONFIG_MISSING"


class DatabaseTemporarilyOffline(AdmissionHybridError):
    code = "DATABASE_TEMPORARILY_OFFLINE"


class AdmissionWriteBlocked(AdmissionHybridError):
    code = "ADMISSION_WRITE_BLOCKED"


class SyncConflict(AdmissionHybridError):
    code = "SYNC_CONFLICT"


class StationRole(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    DETACHED = "DETACHED"
    NONE = "NONE"


class ConnectivityState(str, Enum):
    CONNECTED = "CONNECTED"
    SYNCHRONIZING = "SYNCHRONIZING"
    OFFLINE = "OFFLINE"
    CONFLICT = "CONFLICT"


class ConnectionSupervisor:
    """Stateful recovery helper invoked exclusively by an existing worker.

    The object never performs I/O from a Qt callback: the coordinator calls
    ``recover`` from its worker thread after an offline cycle.
    """

    def __init__(
        self,
        probe: Callable[[], Any],
        *,
        reset_pool: Callable[[], None] | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self._probe = probe
        self._reset_pool = reset_pool
        self._log = log or (lambda _message: None)
        self.state = "ONLINE"
        self.last_error = ""

    def mark_offline(self, exc: BaseException) -> None:
        if self.state != "OFFLINE":
            self._log(f"NETWORK_LOST error={type(exc).__name__}")
        self.state = "OFFLINE"
        self.last_error = type(exc).__name__

    def recover(self) -> Any:
        self.state = "RECONNECTING"
        self._log("NETWORK_RECONNECTING")
        if self._reset_pool is not None:
            self._reset_pool()
            self._log("DB_POOL_RESET")
        result = self._probe()
        self.state = "ONLINE"
        self.last_error = ""
        self._log("NETWORK_RESTORED")
        return result

    def mark_synced(self) -> None:
        self.state = "SYNCED"


ADMISSION_ROLE_AUXILIARY = "auxiliar"
ADMISSION_ROLE_ADMINISTRATOR = "administrador"
ADMISSION_ROLE_AUDIT = "facturador de auditoria"
ADMISSION_TURN_HOURS = 12


def canonical_role(user: Any) -> str:
    """Normaliza el rol de login sin usar el nombre visible del usuario."""
    if isinstance(user, Mapping):
        value = user.get("role", user.get("rol"))
    else:
        value = getattr(user, "role", getattr(user, "rol", user if isinstance(user, str) else ""))
    normalized = unicodedata.normalize("NFKD", str(value or "")).encode(
        "ascii", "ignore"
    ).decode("ascii").strip().casefold()
    aliases = {
        "aux": ADMISSION_ROLE_AUXILIARY,
        "auxiliar de facturacion": ADMISSION_ROLE_AUXILIARY,
        "facturador operativo": ADMISSION_ROLE_AUXILIARY,
        "admin": ADMISSION_ROLE_ADMINISTRATOR,
        "administrator": ADMISSION_ROLE_ADMINISTRATOR,
        "facturador auditoria": ADMISSION_ROLE_AUDIT,
        "auditoria": ADMISSION_ROLE_AUDIT,
    }
    return aliases.get(normalized, normalized)


def user_can_operate_admission(user: Any) -> bool:
    """Roles que pueden trabajar funcionalmente dentro de Admisión.

    El Administrador puede operar aunque no sea el representante vigente.
    El Auxiliar continúa sujeto a la identidad operacional. Auditoría permanece
    en modo de consulta.
    """
    return canonical_role(user) in {
        ADMISSION_ROLE_AUXILIARY,
        ADMISSION_ROLE_ADMINISTRATOR,
    }


def user_can_be_assigned_admission_operator(user: Any) -> bool:
    """Roles asignables mediante una transición operacional explícita."""
    return canonical_role(user) in {
        ADMISSION_ROLE_AUXILIARY,
        ADMISSION_ROLE_ADMINISTRATOR,
    }


@dataclass(frozen=True, slots=True)
class AdmissionAccessDecision:
    view_allowed: bool
    write_allowed: bool
    can_manage_primary: bool
    can_change_turn: bool
    can_generate_attention: bool
    reason_code: str


def evaluate_admission_access(
    authenticated_user: Any,
    operational_state: Mapping[str, Any] | Any,
) -> AdmissionAccessDecision:
    """
    Matriz canónica de acceso a Admisión.

    Reglas:
    - ADMINISTRADOR:
        Puede utilizar completamente Admisión cuando existe un contexto
        operacional válido para la estación.

        No necesita ser el representante operacional.

        Ser Administrador NO lo convierte automáticamente en representante
        ni modifica PRIMARY.

    - FACTURADOR DE AUDITORÍA:
        Solo lectura.

    - AUXILIAR:
        Escritura únicamente cuando el contexto operacional lo autoriza.
    """

    role = canonical_role(authenticated_user)

    state = (
        dict(operational_state)
        if isinstance(operational_state, Mapping)
        else {
            name: getattr(operational_state, name, None)
            for name in (
                "write_allowed",
                "writable",
                "base_write_allowed",
                "device_role",
                "connection_state",
                "offline",
                "status",
                "reason_code",
                "active_user_id",
                "active_username",
            )
        }
    )

    device_role = state.get(
        "device_role",
        state.get("role", "NONE"),
    )

    if isinstance(device_role, StationRole):
        device_role = device_role.value

    base_write = bool(
        state.get(
            "base_write_allowed",
            state.get(
                "write_allowed",
                state.get("writable", False),
            ),
        )
    )

    active = (
        str(state.get("status") or "ACTIVE").upper()
        == "ACTIVE"
    )

    # =========================================================
    # ADMINISTRADOR
    # =========================================================
    #
    # El Administrador tiene acceso funcional completo a
    # Admisión.
    #
    # IMPORTANTE:
    # base_write representa si la estación/contexto operacional
    # está técnicamente listo para escribir.
    #
    # NO significa que Admin tenga que coincidir con el
    # representante.
    #
    # La eliminación de esa dependencia se realiza en
    # resolve_operational_state(), no aquí.
    # =========================================================
    if role == ADMISSION_ROLE_ADMINISTRATOR:
        allowed = bool(
            base_write
            and active
        )

        can_change_turn = can_change_admission_turn(
            authenticated_user, state, device_role
        )

        return AdmissionAccessDecision(
            view_allowed=True,
            write_allowed=allowed,
            can_manage_primary=True,
            can_change_turn=can_change_turn,
            can_generate_attention=allowed,
            reason_code=(
                "ADMIN_OPERATIONAL_ACCESS"
                if allowed
                else str(
                    state.get("reason_code")
                    or "ADMIN_OPERATIONAL_CONTEXT_PENDING"
                )
            ),
        )

    # =========================================================
    # FACTURADOR DE AUDITORÍA
    # =========================================================
    #
    # Siempre consulta.
    # Nunca escritura operacional de Admisión.
    # =========================================================
    if role == ADMISSION_ROLE_AUDIT:
        return AdmissionAccessDecision(
            view_allowed=True,
            write_allowed=False,
            can_manage_primary=False,
            can_change_turn=False,
            can_generate_attention=False,
            reason_code="READONLY_AUDIT_DEFAULT",
        )

    # =========================================================
    # AUXILIAR
    # =========================================================
    #
    # Depende completamente del contexto operacional.
    # La coincidencia con el representante se resuelve antes,
    # al construir base_write_allowed.
    # =========================================================
    if role == ADMISSION_ROLE_AUXILIARY:
        allowed = bool(
            base_write
            and active
        )

        return AdmissionAccessDecision(
            view_allowed=True,
            write_allowed=allowed,
            can_manage_primary=False,
            can_change_turn=can_change_admission_turn(
                authenticated_user, state, device_role
            ),
            can_generate_attention=allowed,
            reason_code=(
                "AUX_OPERATIONAL_ACCESS"
                if allowed
                else str(
                    state.get("reason_code")
                    or "READONLY_OPERATIONAL_CONTEXT"
                )
            ),
        )

    # =========================================================
    # OTROS ROLES
    # =========================================================
    return AdmissionAccessDecision(
        view_allowed=False,
        write_allowed=False,
        can_manage_primary=False,
        can_change_turn=False,
        can_generate_attention=False,
        reason_code="READONLY_ROLE_DENIED",
    )


@dataclass(frozen=True, slots=True)
class OperationalSession:
    operational_session_id: str
    active_username: str
    active_user_id: str
    primary_device_id: str
    primary_login_session_id: str
    turn_id: int | None
    operational_source_id: str
    status: str
    generation: int
    operational_revision: int = 1
    primary_last_seen: str = ""
    updated_at: str = ""
    active_user_display_name: str = ""
    turn_started_at: str = ""
    turn_ends_at: str = ""
    lease_generation: int = 0
    turn_code: str = ""

    @property
    def representative_user_id(self) -> str:
        """Canonical name for the legacy ``active_user_id`` column."""
        return self.active_user_id

    @property
    def representative_username(self) -> str:
        """Canonical name for the legacy ``active_username`` column."""
        return self.active_username

    @property
    def representative_display_name(self) -> str:
        """Visible representative label; never an authentication identity."""
        return self.active_user_display_name or self.active_username

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> OperationalSession:
        return cls(
            operational_session_id=str(value.get("operational_session_id") or ""),
            active_username=str(value.get("active_username") or "").strip(),
            active_user_id=str(value.get("active_user_id") or ""),
            primary_device_id=str(value.get("primary_device_id") or ""),
            primary_login_session_id=str(value.get("primary_login_session_id") or ""),
            turn_id=_as_int_or_none(value.get("turn_id")),
            operational_source_id=str(value.get("operational_source_id") or ""),
            status=str(value.get("status") or "ACTIVE").upper(),
            generation=max(1, int(value.get("generation") or 1)),
            operational_revision=max(1, int(value.get("operational_revision") or 1)),
            primary_last_seen=str(value.get("primary_last_seen") or ""),
            updated_at=str(value.get("updated_at") or ""),
            active_user_display_name=str(
                value.get("active_user_display_name") or ""
            ).strip(),
            lease_generation=max(0, int(value.get("lease_generation") or 0)),
            turn_code=str(value.get("turn_code") or "").strip(),
            turn_started_at=str(value.get("turn_started_at") or ""),
            turn_ends_at=str(value.get("turn_ends_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class DeviceAttachment:
    operational_session: OperationalSession
    role: StationRole
    writable: bool
    message: str = ""


@dataclass(frozen=True, slots=True)
class AdmissionIdentity:
    """Unambiguous identity shared by local, cloud and document flows."""

    global_attention_id: str = ""
    source_instance_id: str = ""
    local_attention_id: int = 0

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | Any) -> AdmissionIdentity:
        data = dict(value or {}) if isinstance(value, Mapping) else {}
        global_id = str(data.get("global_attention_id") or "").strip()
        if global_id:
            try:
                global_id = str(uuid.UUID(global_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("global_attention_id no es un UUID válido.") from exc
        local_id = _as_int_or_none(
            data.get("local_attention_id", data.get("attention_id", data.get("id")))
        ) or 0
        source_id = str(
            data.get("source_instance_id")
            or data.get("legacy_source_instance_id")
            or ""
        ).strip()
        identity = cls(global_id, source_id, int(local_id))
        identity.require_complete()
        return identity

    def require_complete(self) -> AdmissionIdentity:
        if self.global_attention_id:
            return self
        if self.local_attention_id > 0 and self.source_instance_id:
            return self
        raise ValueError(
            "La atención requiere global_attention_id o identidad legacy compuesta."
        )

    def matches(self, other: AdmissionIdentity) -> bool:
        if self.global_attention_id and other.global_attention_id:
            return self.global_attention_id == other.global_attention_id
        return bool(
            self.source_instance_id
            and other.source_instance_id
            and self.source_instance_id == other.source_instance_id
            and self.local_attention_id == other.local_attention_id
        )


@dataclass(frozen=True, slots=True)
class WriteDecision:
    allowed: bool
    code: str
    message: str
    role: StationRole = StationRole.NONE
    offline: bool = False


def _canonical_text(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def canonical_user_id(user: Any) -> str:
    """Identificador lógico; nunca deriva del nombre visible."""
    if isinstance(user, Mapping):
        value = user.get("active_user_id", user.get("user_id", user.get("id")))
    else:
        value = getattr(
            user,
            "active_user_id",
            getattr(user, "user_id", getattr(user, "id", "")),
        )
    return str(value or "").strip()


def canonical_username(user: Any) -> str:
    """Username normalizado; full_name/display_name quedan fuera de permisos."""
    if isinstance(user, str):
        value = user
    elif isinstance(user, Mapping):
        value = user.get("active_username", user.get("username"))
    else:
        value = getattr(
            user,
            "active_username",
            getattr(user, "username", ""),
        )
    return _canonical_text(value)


def same_user(first: Any, second: Any) -> bool:
    """Compara ID cuando ambos existen y usa username solo como respaldo."""
    first_id = canonical_user_id(first)
    second_id = canonical_user_id(second)
    if first_id and second_id:
        return first_id == second_id
    first_username = canonical_username(first)
    second_username = canonical_username(second)
    return bool(first_username and first_username == second_username)


def can_change_admission_turn(
    authenticated_user: Any,
    operational_state: Mapping[str, Any] | Any,
    station_role: StationRole | str | None = None,
) -> bool:
    """Return whether this login may change the live Admission turn.

    A turn change is a PRIMARY command. Administrators may issue it from
    PRIMARY without becoming the representative; an Auxiliary may issue it
    only when it is the representative recorded by the central operational
    snapshot. Display names never participate in this comparison.
    """
    state = (
        dict(operational_state)
        if isinstance(operational_state, Mapping)
        else {
            name: getattr(operational_state, name, None)
            for name in (
                "active_user_id",
                "active_username",
                "device_role",
                "role",
                "status",
                "connection_state",
                "offline",
            )
        }
    )
    role = canonical_role(authenticated_user)
    resolved_station_role = station_role or state.get(
        "device_role", state.get("role", StationRole.NONE.value)
    )
    if isinstance(resolved_station_role, StationRole):
        resolved_station_role = resolved_station_role.value
    if str(resolved_station_role or "").upper() != StationRole.PRIMARY.value:
        return False
    if bool(state.get("offline")) or str(
        state.get("connection_state", ConnectivityState.CONNECTED.value)
    ).upper() in {ConnectivityState.OFFLINE.value, "DISCONNECTED"}:
        return False
    if str(state.get("status") or "ACTIVE").upper() != "ACTIVE":
        return False
    if role == ADMISSION_ROLE_AUDIT or not user_can_operate_admission(
        authenticated_user
    ):
        return False
    if role == ADMISSION_ROLE_ADMINISTRATOR:
        return True
    return same_user(authenticated_user, state)


@dataclass(frozen=True, slots=True)
class OperationalState:
    operational_session_id: str
    generation: int
    active_user_id: str
    active_username: str
    active_user_display_name: str
    turn_id: int | None
    primary_device_id: str
    primary_login_session_id: str
    local_device_id: str
    local_login_session_id: str
    device_role: StationRole
    device_attached: bool
    user_matches_operational: bool
    write_allowed: bool
    connection_state: ConnectivityState = ConnectivityState.CONNECTED
    sync_state: str = "SYNCHRONIZED"
    reason_code: str = "ALLOWED"
    message: str = "Conectado."
    invalidated_reason: str = ""
    operational_source_id: str = ""
    status: str = "ACTIVE"
    updated_at: str = ""
    turn_started_at: str = ""
    turn_ends_at: str = ""
    turn_code: str = ""
    lease_generation: int = 0
    operational_revision: int = 1
    view_allowed: bool = True
    can_manage_primary: bool = False
    can_change_turn: bool = False
    can_generate_attention: bool = False

    @property
    def representative_user_id(self) -> str:
        return self.active_user_id

    @property
    def representative_username(self) -> str:
        return self.active_username

    @property
    def representative_display_name(self) -> str:
        return self.active_user_display_name or self.active_username

    def as_mapping(self) -> dict[str, Any]:
        result = asdict(self)
        result["role"] = self.device_role.value
        result["device_role"] = self.device_role.value
        result["connection_state"] = self.connection_state.value
        result["writable"] = self.write_allowed
        # Explicit aliases make the legacy active_user_* schema unambiguous
        # to all new callers without a destructive column migration.
        result["representative_user_id"] = self.representative_user_id
        result["representative_username"] = self.representative_username
        result["representative_display_name"] = self.representative_display_name
        return result


@dataclass(frozen=True, slots=True)
class PrimaryTransitionResult:
    operational_session: OperationalSession
    transition_id: str
    invalidated_login_session_ids: tuple[str, ...] = ()
    old_primary_login_session_id: str = ""
    committed: bool = True
    old_turn_id: int | None = None
    new_turn_id: int | None = None
    old_generation: int = 0
    new_generation: int = 0
    old_user_id: str = ""
    new_user_id: str = ""
    old_username: str = ""
    new_username: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncEvent:
    event_uuid: str
    entity_type: str
    entity_uuid: str
    operation: str
    payload: Mapping[str, Any]
    operational_session_id: str
    generation: int
    device_id: str
    created_at: str
    base_version: int = 0
    operational_source_id: str = ""
    turn_id: int | None = None
    origin_user_id: str = ""
    origin_username: str = ""
    created_at_device: str = ""
    created_at_effective_utc: str = ""
    device_local_sequence: int = 0
    server_time_offset_ms: int = 0

    def payload_json(self) -> str:
        return json.dumps(dict(self.payload), ensure_ascii=False, sort_keys=True)

    def historical_order_key(self) -> tuple[str, str, int, str]:
        return deterministic_event_order_key(
            {
                "created_at_effective_utc": self.created_at_effective_utc or self.created_at,
                "origin_device_id": self.device_id,
                "device_local_sequence": self.device_local_sequence,
                "global_attention_id": self.entity_uuid,
            }
        )


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _timestamp(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat(timespec="seconds")


def _as_int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _normalize_service_date(value: Any) -> str:
    text = str(value or "").strip()
    for date_format in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], date_format).replace(
                tzinfo=timezone.utc
            ).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _legacy_effective_timestamp(service_date: Any, service_time: Any) -> str:
    date_text = _normalize_service_date(service_date)
    time_text = str(service_time or "").strip()
    for time_format in ("%H:%M:%S", "%H:%M", "%I:%M %p"):
        try:
            parsed_time = datetime.strptime(time_text, time_format).replace(
                tzinfo=timezone.utc
            ).time()
            if date_text:
                return datetime.combine(
                    datetime.strptime(date_text, "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc)
                    .date(),
                    parsed_time,
                    tzinfo=timezone.utc,
                ).isoformat(timespec="milliseconds")
        except ValueError:
            continue
    return _timestamp()


def _mapping(row: Any) -> dict[str, Any]:
    return dict(row or {})


def deterministic_event_order_key(value: Mapping[str, Any]) -> tuple[str, str, int, str]:
    """Orden histórico estable; nunca depende de la hora de subida al cloud."""
    return (
        str(
            value.get("created_at_effective_utc")
            or value.get("created_at_device")
            or value.get("created_at")
            or ""
        ),
        str(value.get("origin_device_id") or value.get("device_id") or ""),
        int(value.get("device_local_sequence") or 0),
        str(value.get("global_attention_id") or value.get("entity_uuid") or ""),
    )


def build_admission_order_key(value: Mapping[str, Any]) -> tuple[str, str, int, str]:
    """Orden canónico compartido por historial, resumen y documentos derivados."""
    return deterministic_event_order_key(value)


def select_effective_turn_interval(
    intervals: list[Mapping[str, Any]], effective_at: datetime | str,
) -> Mapping[str, Any] | None:
    """Selecciona [started_at, ended_at); el instante exacto pertenece al nuevo turno."""
    effective = (
        datetime.fromisoformat(str(effective_at).replace("Z", "+00:00"))
        if isinstance(effective_at, str)
        else effective_at
    )
    if effective.tzinfo is None:
        effective = effective.replace(tzinfo=timezone.utc)
    selected = None
    selected_start = None
    for interval in intervals:
        started = datetime.fromisoformat(
            str(interval.get("started_at") or "").replace("Z", "+00:00")
        )
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        ended_value = interval.get("ended_at")
        ended = (
            datetime.fromisoformat(str(ended_value).replace("Z", "+00:00"))
            if ended_value
            else None
        )
        if ended is not None and ended.tzinfo is None:
            ended = ended.replace(tzinfo=timezone.utc)
        if (
            started <= effective
            and (ended is None or effective < ended)
            and (selected_start is None or started > selected_start)
        ):
            selected = interval
            selected_start = started
    return selected


def is_temporary_connection_error(exc: BaseException) -> bool:
    """No confunde una configuraci\u00f3n ausente con una ca\u00edda transitoria."""
    text = str(exc or "").casefold()
    temporary_tokens = (
        "connection refused", "could not connect", "timeout", "timed out",
        "network is unreachable", "server closed the connection",
        "could not translate host", "name or service not known", "ssl syscall",
    )
    return any(token in text for token in temporary_tokens)


def connection_state_from_error(exc: BaseException, *, configured: bool) -> ConnectivityState:
    if not configured:
        raise DatabaseConfigurationMissing(
            "La configuraci\u00f3n central de la base de datos no est\u00e1 disponible."
        )
    if is_temporary_connection_error(exc):
        raise DatabaseTemporarilyOffline(
            "Sin conexi\u00f3n \u00b7 trabajando localmente."
        ) from exc
    raise AdmissionHybridError("No fue posible validar la conexi\u00f3n central.") from exc


POSTGRES_HYBRID_SCHEMA = """
CREATE TABLE IF NOT EXISTS admission_operational_sessions(
  operational_session_id UUID PRIMARY KEY,
  active_username TEXT NOT NULL,
  active_user_id TEXT,
  active_user_display_name TEXT,
  primary_device_id TEXT NOT NULL,
  primary_login_session_id TEXT NOT NULL,
  turn_id BIGINT,
  turn_code TEXT NOT NULL DEFAULT '',
  turn_started_at TIMESTAMPTZ,
  turn_ends_at TIMESTAMPTZ,
  operational_source_id UUID NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE',
  generation INTEGER NOT NULL DEFAULT 1,
  operational_revision BIGINT NOT NULL DEFAULT 1,
  lease_generation BIGINT NOT NULL DEFAULT 0,
  production_epoch_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  primary_last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  changed_by TEXT,
  change_reason TEXT,
  CHECK(status IN ('ACTIVE','CLOSED'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_operational_active
  ON admission_operational_sessions((status)) WHERE status='ACTIVE';
CREATE TABLE IF NOT EXISTS admission_operational_identity(
  singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
  operational_source_id UUID NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS admission_operational_devices(
  operational_session_id UUID NOT NULL REFERENCES admission_operational_sessions(operational_session_id) ON DELETE CASCADE,
  device_id TEXT NOT NULL,
  login_session_id TEXT NOT NULL,
  device_name TEXT,
  station_role TEXT NOT NULL,
  attached_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  detached_at TIMESTAMPTZ,
  invalidated_at TIMESTAMPTZ,
  invalidated_reason TEXT,
  invalidated_generation INTEGER,
  new_active_username TEXT,
  PRIMARY KEY(operational_session_id, device_id),
  CHECK(station_role IN ('PRIMARY','SECONDARY'))
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_operational_login
  ON admission_operational_devices(login_session_id) WHERE detached_at IS NULL;
WITH ranked_primary_devices AS (
  SELECT d.ctid,
         ROW_NUMBER() OVER (
           PARTITION BY d.operational_session_id
           ORDER BY (d.device_id=s.primary_device_id) DESC,
                    d.last_seen DESC,
                    d.attached_at DESC
         ) AS position
  FROM admission_operational_devices d
  JOIN admission_operational_sessions s
    ON s.operational_session_id=d.operational_session_id
  WHERE d.station_role='PRIMARY' AND d.detached_at IS NULL
)
UPDATE admission_operational_devices d
   SET station_role='SECONDARY',detached_at=NOW(),
       invalidated_at=NOW(),invalidated_reason='DUPLICATE_PRIMARY_REPAIRED'
  FROM ranked_primary_devices ranked
 WHERE d.ctid=ranked.ctid AND ranked.position>1;
CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_operational_primary_device
  ON admission_operational_devices(operational_session_id)
  WHERE station_role='PRIMARY' AND detached_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_admission_operational_snapshot
  ON admission_operational_sessions(updated_at DESC)
  WHERE status='ACTIVE';
CREATE INDEX IF NOT EXISTS idx_admission_operational_device_active
  ON admission_operational_devices(operational_session_id,device_id)
  WHERE detached_at IS NULL;
CREATE TABLE IF NOT EXISTS admission_operational_audit(
  id BIGSERIAL PRIMARY KEY,
  operational_session_id UUID,
  event_type TEXT NOT NULL,
  device_id TEXT,
  username TEXT,
  generation INTEGER,
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  transition_id UUID,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admission_operational_audit_session
  ON admission_operational_audit(operational_session_id,created_at DESC);
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS active_user_display_name TEXT;
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS turn_started_at TIMESTAMPTZ;
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS turn_ends_at TIMESTAMPTZ;
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS turn_code TEXT NOT NULL DEFAULT '';
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS operational_revision BIGINT NOT NULL DEFAULT 1;
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS lease_generation BIGINT NOT NULL DEFAULT 0;
ALTER TABLE admission_operational_devices
  ADD COLUMN IF NOT EXISTS invalidated_at TIMESTAMPTZ;
ALTER TABLE admission_operational_devices
  ADD COLUMN IF NOT EXISTS invalidated_reason TEXT;
ALTER TABLE admission_operational_devices
  ADD COLUMN IF NOT EXISTS invalidated_generation INTEGER;
ALTER TABLE admission_operational_devices
  ADD COLUMN IF NOT EXISTS new_active_username TEXT;
ALTER TABLE admission_operational_audit
  ADD COLUMN IF NOT EXISTS transition_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_operational_transition
  ON admission_operational_audit(transition_id) WHERE transition_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS sigeh_product_state(
  singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
  product_id TEXT NOT NULL CHECK(product_id='SIGEH'),
  bootstrap_version TEXT NOT NULL,
  production_epoch_id UUID NOT NULL UNIQUE,
  bootstrap_status TEXT NOT NULL,
  bootstrap_completed_at TIMESTAMPTZ NOT NULL,
  production_initialized_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK(bootstrap_status IN ('COMPLETED','PRODUCTION_ACTIVE'))
);
CREATE TABLE IF NOT EXISTS admission_sync_events(
  sequence BIGSERIAL PRIMARY KEY,
  event_uuid UUID NOT NULL UNIQUE,
  entity_type TEXT NOT NULL,
  entity_uuid UUID NOT NULL,
  operation TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  operational_session_id UUID NOT NULL,
  generation INTEGER NOT NULL,
  origin_device_id TEXT NOT NULL,
  base_version INTEGER NOT NULL DEFAULT 0,
  resulting_version INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  received_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_admission_sync_events_cursor
  ON admission_sync_events(sequence);
CREATE INDEX IF NOT EXISTS idx_admission_sync_events_entity
  ON admission_sync_events(entity_type, entity_uuid, resulting_version);
CREATE TABLE IF NOT EXISTS admission_replication_event_floors(
  stream_name TEXT PRIMARY KEY,
  minimum_available_sequence BIGINT NOT NULL DEFAULT 0,
  checkpoint_sequence BIGINT NOT NULL DEFAULT 0,
  retention_days INTEGER NOT NULL DEFAULT 7,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  CHECK(stream_name IN ('ATTENTION','PATIENT_DIRECTORY')),
  CHECK(minimum_available_sequence >= 0),
  CHECK(checkpoint_sequence >= minimum_available_sequence)
);
INSERT INTO admission_replication_event_floors(
  stream_name,minimum_available_sequence,checkpoint_sequence,retention_days
) VALUES('ATTENTION',0,0,7)
ON CONFLICT(stream_name) DO NOTHING;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS global_attention_id UUID;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS global_patient_id UUID;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS operational_source_id UUID;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS origin_device_id TEXT;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS operational_session_id UUID;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS generation INTEGER;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS operational_source_id UUID;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS turn_id BIGINT;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS origin_user_id TEXT;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS origin_username TEXT;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS created_at_device TIMESTAMPTZ;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS created_at_effective_utc TIMESTAMPTZ;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS device_local_sequence BIGINT NOT NULL DEFAULT 0;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS server_time_offset_ms BIGINT NOT NULL DEFAULT 0;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS reconciliation_status TEXT NOT NULL DEFAULT 'DIRECT';
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS cloud_event_seq BIGINT
  GENERATED ALWAYS AS (sequence) STORED;
ALTER TABLE admission_sync_events ADD COLUMN IF NOT EXISTS server_received_at TIMESTAMPTZ
  GENERATED ALWAYS AS (received_at) STORED;
CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_sync_events_cloud_sequence
  ON admission_sync_events(cloud_event_seq);
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS created_at_device TIMESTAMPTZ;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS created_at_effective_utc TIMESTAMPTZ;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS device_local_sequence BIGINT NOT NULL DEFAULT 0;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS origin_user_id TEXT;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS captured_by_username TEXT;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS reconciliation_status TEXT NOT NULL DEFAULT 'DIRECT';
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS original_turn_id BIGINT;
CREATE INDEX IF NOT EXISTS idx_admission_sync_events_deterministic_order
  ON admission_sync_events(
    created_at_effective_utc,origin_device_id,device_local_sequence,entity_uuid
  );
CREATE TABLE IF NOT EXISTS admission_operational_turn_intervals(
  operational_session_id UUID NOT NULL,
  generation INTEGER NOT NULL,
  turn_id BIGINT,
  active_user_id TEXT,
  active_username TEXT NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  nominal_ends_at TIMESTAMPTZ,
  ended_at TIMESTAMPTZ,
  production_epoch_id UUID,
  PRIMARY KEY(operational_session_id,generation)
);
CREATE INDEX IF NOT EXISTS idx_admission_turn_intervals_effective
  ON admission_operational_turn_intervals(operational_session_id,started_at,ended_at);
ALTER TABLE admission_operational_turn_intervals
  ADD COLUMN IF NOT EXISTS nominal_ends_at TIMESTAMPTZ;
ALTER TABLE admission_operational_turn_intervals
  ADD COLUMN IF NOT EXISTS production_epoch_id UUID;
ALTER TABLE admission_operational_sessions
  ADD COLUMN IF NOT EXISTS production_epoch_id UUID;
CREATE UNIQUE INDEX IF NOT EXISTS uq_admission_projection_global_attention
  ON admission_attention_projection(global_attention_id)
  WHERE global_attention_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_admission_projection_operational_turn
  ON admission_attention_projection(operational_source_id,turn_id,source_status);
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS deleted_by_user_id TEXT;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS delete_event_uuid UUID;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS delete_reason TEXT;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS server_revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE admission_attention_projection ADD COLUMN IF NOT EXISTS latest_payload_json JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX IF NOT EXISTS idx_admission_projection_active_turn
  ON admission_attention_projection(turn_id,source_status,is_deleted);
CREATE INDEX IF NOT EXISTS idx_admission_projection_global_deleted
  ON admission_attention_projection(global_attention_id,is_deleted,server_revision);
CREATE INDEX IF NOT EXISTS idx_admission_sync_events_event_uuid
  ON admission_sync_events(event_uuid);
CREATE INDEX IF NOT EXISTS idx_admission_sync_events_entity_revision
  ON admission_sync_events(entity_uuid,resulting_version DESC);
CREATE TABLE IF NOT EXISTS admission_central_seeds(
  central_seed_id UUID PRIMARY KEY,
  legacy_source_instance_id TEXT NOT NULL,
  seed_source_fingerprint TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  imported_records BIGINT NOT NULL DEFAULT 0,
  started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  seed_completed_at TIMESTAMPTZ,
  origin_device_id TEXT,
  UNIQUE(legacy_source_instance_id,seed_source_fingerprint,schema_version),
  CHECK(status IN ('RUNNING','COMPLETED','FAILED'))
);
CREATE TABLE IF NOT EXISTS admission_import_batches(
  import_batch_id UUID PRIMARY KEY,
  source_filename TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  legacy_source_instance_id TEXT NOT NULL,
  imported_by TEXT NOT NULL,
  imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  totals_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  applied_at TIMESTAMPTZ,
  current_phase TEXT NOT NULL DEFAULT '',
  progress_percent INTEGER NOT NULL DEFAULT 0,
  processed_records BIGINT NOT NULL DEFAULT 0,
  total_records BIGINT NOT NULL DEFAULT 0,
  status_message TEXT NOT NULL DEFAULT '',
  started_at TIMESTAMPTZ,
  progress_updated_at TIMESTAMPTZ,
  last_heartbeat_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  error_code TEXT,
  error_message TEXT,
  CHECK(mode IN ('SEED','MERGE')),
  CHECK(status IN ('ANALYZING','ANALYZED','APPLYING','COMPLETED','FAILED')),
  CHECK(progress_percent BETWEEN 0 AND 100)
);
CREATE INDEX IF NOT EXISTS idx_admission_import_batches_source
  ON admission_import_batches(source_sha256,mode,imported_at DESC);
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS current_phase TEXT NOT NULL DEFAULT '';
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS progress_percent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS processed_records BIGINT NOT NULL DEFAULT 0;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS total_records BIGINT NOT NULL DEFAULT 0;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS status_message TEXT NOT NULL DEFAULT '';
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMPTZ;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS error_code TEXT;
ALTER TABLE admission_import_batches
  ADD COLUMN IF NOT EXISTS error_message TEXT;
CREATE INDEX IF NOT EXISTS idx_admission_import_batches_active
  ON admission_import_batches(status,last_heartbeat_at DESC)
  WHERE status IN ('ANALYZING','APPLYING');
CREATE TABLE IF NOT EXISTS admission_import_staging(
  import_batch_id UUID NOT NULL REFERENCES admission_import_batches(import_batch_id) ON DELETE CASCADE,
  row_number BIGINT NOT NULL,
  global_attention_id UUID NOT NULL,
  legacy_source_instance_id TEXT NOT NULL,
  legacy_attention_id BIGINT NOT NULL,
  local_revision INTEGER NOT NULL DEFAULT 0,
  cloud_revision INTEGER NOT NULL DEFAULT 0,
  classification TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  error_message TEXT,
  applied_at TIMESTAMPTZ,
  result_code TEXT,
  PRIMARY KEY(import_batch_id,row_number)
);
CREATE INDEX IF NOT EXISTS idx_admission_import_staging_identity
  ON admission_import_staging(global_attention_id,classification);
CREATE TABLE IF NOT EXISTS admission_dataset_state(
  singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
  dataset_epoch BIGINT NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_import_batch_id UUID
);
INSERT INTO admission_dataset_state(singleton) VALUES(1)
  ON CONFLICT(singleton) DO NOTHING;
""" + POSTGRES_PATIENT_DIRECTORY_SCHEMA


SQLITE_HYBRID_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_metadata(
  clave TEXT PRIMARY KEY,
  valor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS admission_operational_cache(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  operational_session_id TEXT NOT NULL,
  active_username TEXT NOT NULL,
  active_user_id TEXT,
  primary_device_id TEXT NOT NULL,
  turn_id INTEGER,
  operational_source_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  role TEXT NOT NULL,
  turn_started_at TEXT,
  turn_ends_at TEXT,
  verified_at TEXT NOT NULL,
  lease_expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_outbox(
  event_uuid TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  entity_uuid TEXT NOT NULL,
  operation TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  operational_session_id TEXT NOT NULL,
  operational_source_id TEXT NOT NULL DEFAULT '',
  generation INTEGER NOT NULL,
  turn_id INTEGER,
  device_id TEXT NOT NULL,
  origin_user_id TEXT NOT NULL DEFAULT '',
  origin_username TEXT NOT NULL DEFAULT '',
  created_at_device TEXT NOT NULL DEFAULT '',
  created_at_effective_utc TEXT NOT NULL DEFAULT '',
  device_local_sequence INTEGER NOT NULL DEFAULT 0,
  server_time_offset_ms INTEGER NOT NULL DEFAULT 0,
  base_version INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  sent_at TEXT,
  sync_status TEXT NOT NULL DEFAULT 'PENDING',
  retry_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_outbox_pending
  ON sync_outbox(sync_status, created_at);
CREATE INDEX IF NOT EXISTS idx_sync_outbox_entity
  ON sync_outbox(entity_type,entity_uuid);
CREATE TABLE IF NOT EXISTS sync_state(
  state_key TEXT PRIMARY KEY,
  state_value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runtime_context(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  operational_session_id TEXT NOT NULL,
  generation INTEGER NOT NULL,
  device_id TEXT NOT NULL,
  operational_source_id TEXT NOT NULL,
  operational_turn_id INTEGER,
  active_username TEXT NOT NULL DEFAULT '',
  active_user_id TEXT NOT NULL DEFAULT '',
  actor_username TEXT NOT NULL DEFAULT '',
  actor_user_id TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_apply_context(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  event_uuid TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_applied_events(
  event_uuid TEXT PRIMARY KEY,
  applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_attention_aliases(
  remote_global_attention_id TEXT PRIMARY KEY,
  local_attention_id INTEGER NOT NULL,
  reason TEXT NOT NULL,
  mapped_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_conflicts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_uuid TEXT NOT NULL UNIQUE,
  entity_type TEXT NOT NULL,
  entity_uuid TEXT NOT NULL,
  local_payload_json TEXT NOT NULL,
  remote_payload_json TEXT NOT NULL,
  detected_at TEXT NOT NULL,
  resolved_at TEXT,
  resolution TEXT
);
CREATE TABLE IF NOT EXISTS sync_device_sequence(
  singleton INTEGER PRIMARY KEY CHECK(singleton=1),
  last_sequence INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO sync_device_sequence(singleton,last_sequence) VALUES(1,0);
CREATE TABLE IF NOT EXISTS sync_entity_tombstones(
  entity_type TEXT NOT NULL,
  entity_uuid TEXT NOT NULL,
  server_revision INTEGER NOT NULL,
  delete_event_uuid TEXT NOT NULL,
  deleted_at TEXT NOT NULL,
  deleted_by_user_id TEXT,
  delete_reason TEXT,
  applied_at TEXT NOT NULL,
  PRIMARY KEY(entity_type,entity_uuid)
);
CREATE TABLE IF NOT EXISTS sync_seed_state(
  central_seed_id TEXT PRIMARY KEY,
  legacy_source_instance_id TEXT NOT NULL,
  schema_version INTEGER NOT NULL,
  status TEXT NOT NULL,
  imported_records INTEGER NOT NULL DEFAULT 0,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS sync_seed_entities(
  central_seed_id TEXT NOT NULL,
  entity_uuid TEXT NOT NULL,
  event_uuid TEXT NOT NULL,
  queued_at TEXT NOT NULL,
  PRIMARY KEY(central_seed_id,entity_uuid)
);
"""
ADMISSION_IMPORT_PROGRESS_MIGRATION_ID = "20260823_admission_import_progress_v2"
ADMISSION_IMPORT_BATCH_COLUMNS = frozenset({
    "import_batch_id", "source_filename", "source_sha256",
    "legacy_source_instance_id", "imported_by", "imported_at", "mode", "status",
    "totals_json", "applied_at", "current_phase", "progress_percent",
    "processed_records", "total_records", "status_message", "started_at",
    "progress_updated_at", "last_heartbeat_at", "completed_at", "error_code",
    "error_message",
})
ADMISSION_IMPORT_STAGING_COLUMNS = frozenset({
    "import_batch_id", "row_number", "global_attention_id",
    "legacy_source_instance_id", "legacy_attention_id", "local_revision",
    "cloud_revision", "classification", "payload_json", "error_message",
    "applied_at", "result_code",
})


def ensure_admission_import_progress_schema(connection: Any) -> None:
    """Migrate legacy import tables to durable progress without deleting rows."""
    statements = (
        """CREATE TABLE IF NOT EXISTS admission_import_batches(
               import_batch_id UUID PRIMARY KEY,
               source_filename TEXT NOT NULL, source_sha256 TEXT NOT NULL,
               legacy_source_instance_id TEXT NOT NULL, imported_by TEXT NOT NULL,
               imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), mode TEXT NOT NULL,
               status TEXT NOT NULL, totals_json JSONB NOT NULL DEFAULT '{}'::JSONB,
               applied_at TIMESTAMPTZ, current_phase TEXT NOT NULL DEFAULT '',
               progress_percent INTEGER NOT NULL DEFAULT 0,
               processed_records BIGINT NOT NULL DEFAULT 0,
               total_records BIGINT NOT NULL DEFAULT 0,
               status_message TEXT NOT NULL DEFAULT '', started_at TIMESTAMPTZ,
               progress_updated_at TIMESTAMPTZ, last_heartbeat_at TIMESTAMPTZ,
               completed_at TIMESTAMPTZ, error_code TEXT, error_message TEXT,
               CHECK(mode IN ('SEED','MERGE')),
               CHECK(status IN ('ANALYZING','ANALYZED','APPLYING','COMPLETED','FAILED')),
               CHECK(progress_percent BETWEEN 0 AND 100)
           )""",
        # Every column referenced by the importer is made available here.  The
        # legacy shape normally already contains the identity fields; the
        # defensive additions also make a partial/manual install query-safe.
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS import_batch_id UUID",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS source_filename TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS source_sha256 TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS legacy_source_instance_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS imported_by TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'SEED'",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'FAILED'",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS totals_json JSONB NOT NULL DEFAULT '{}'::JSONB",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS current_phase TEXT",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS progress_percent INTEGER",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS processed_records BIGINT",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS total_records BIGINT",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS status_message TEXT",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS progress_updated_at TIMESTAMPTZ",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS last_heartbeat_at TIMESTAMPTZ",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS error_code TEXT",
        "ALTER TABLE admission_import_batches ADD COLUMN IF NOT EXISTS error_message TEXT",
        """CREATE TABLE IF NOT EXISTS admission_import_staging(
               import_batch_id UUID NOT NULL REFERENCES admission_import_batches(import_batch_id) ON DELETE CASCADE,
               row_number BIGINT NOT NULL, global_attention_id UUID NOT NULL,
               legacy_source_instance_id TEXT NOT NULL, legacy_attention_id BIGINT NOT NULL,
               local_revision INTEGER NOT NULL DEFAULT 0, cloud_revision INTEGER NOT NULL DEFAULT 0,
               classification TEXT NOT NULL, payload_json JSONB NOT NULL, error_message TEXT,
               applied_at TIMESTAMPTZ, result_code TEXT,
               PRIMARY KEY(import_batch_id,row_number)
           )""",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS import_batch_id UUID",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS row_number BIGINT",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS global_attention_id UUID",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS legacy_source_instance_id TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS legacy_attention_id BIGINT NOT NULL DEFAULT 0",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS local_revision INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS cloud_revision INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS classification TEXT NOT NULL DEFAULT 'EXISTING'",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS payload_json JSONB NOT NULL DEFAULT '{}'::JSONB",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS error_message TEXT",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS applied_at TIMESTAMPTZ",
        "ALTER TABLE admission_import_staging ADD COLUMN IF NOT EXISTS result_code TEXT",
        """DO $$
            DECLARE existing_constraint RECORD;
            BEGIN
                FOR existing_constraint IN
                    SELECT conname FROM pg_constraint
                     WHERE conrelid='public.admission_import_batches'::regclass
                       AND contype='c'
                       AND (
                           pg_get_constraintdef(oid) ILIKE '%status%'
                           OR pg_get_constraintdef(oid) ILIKE '%progress_percent%'
                       )
                LOOP
                    EXECUTE format(
                        'ALTER TABLE public.admission_import_batches DROP CONSTRAINT IF EXISTS %I',
                        existing_constraint.conname
                    );
                END LOOP;
            END $$""",
        """UPDATE admission_import_batches
              SET mode=CASE WHEN UPPER(COALESCE(mode,'')) IN ('SEED','MERGE')
                      THEN UPPER(mode) ELSE 'SEED' END,
                  status=CASE WHEN UPPER(COALESCE(status,'')) IN
                      ('ANALYZING','ANALYZED','APPLYING','COMPLETED','FAILED')
                      THEN UPPER(status) ELSE 'FAILED' END,
                  started_at=COALESCE(started_at,imported_at),
                  progress_updated_at=COALESCE(progress_updated_at,applied_at,imported_at),
                  completed_at=CASE WHEN UPPER(COALESCE(status,''))='COMPLETED'
                      THEN COALESCE(completed_at,applied_at,imported_at) ELSE completed_at END,
                  current_phase=COALESCE(NULLIF(current_phase,''),CASE
                      WHEN UPPER(COALESCE(status,''))='COMPLETED' THEN 'FINALIZE_APPLY'
                      WHEN UPPER(COALESCE(status,''))='ANALYZED' THEN 'FINALIZE_ANALYSIS'
                      ELSE '' END),
                  status_message=COALESCE(status_message,''),
                  progress_percent=CASE
                      WHEN UPPER(COALESCE(status,'')) IN ('COMPLETED','ANALYZED') THEN 100
                      WHEN COALESCE(progress_percent,0)<0 THEN 0
                      WHEN COALESCE(progress_percent,0)>100 THEN 100
                      ELSE COALESCE(progress_percent,0) END,
                  total_records=CASE WHEN COALESCE(totals_json->>'records','') ~ '^[0-9]+$'
                      THEN GREATEST(COALESCE(total_records,0),(totals_json->>'records')::BIGINT)
                      ELSE GREATEST(COALESCE(total_records,0),0) END,
                  processed_records=CASE
                      WHEN UPPER(COALESCE(status,'')) IN ('COMPLETED','ANALYZED')
                           AND COALESCE(totals_json->>'records','') ~ '^[0-9]+$'
                          THEN (totals_json->>'records')::BIGINT
                      WHEN COALESCE(processed_records,0)<0 THEN 0
                      ELSE COALESCE(processed_records,0) END""",
        "ALTER TABLE admission_import_batches ALTER COLUMN current_phase SET DEFAULT ''",
        "ALTER TABLE admission_import_batches ALTER COLUMN progress_percent SET DEFAULT 0",
        "ALTER TABLE admission_import_batches ALTER COLUMN processed_records SET DEFAULT 0",
        "ALTER TABLE admission_import_batches ALTER COLUMN total_records SET DEFAULT 0",
        "ALTER TABLE admission_import_batches ALTER COLUMN status_message SET DEFAULT ''",
        "ALTER TABLE admission_import_batches ALTER COLUMN mode SET NOT NULL",
        "ALTER TABLE admission_import_batches ALTER COLUMN status SET NOT NULL",
        "ALTER TABLE admission_import_batches ALTER COLUMN current_phase SET NOT NULL",
        "ALTER TABLE admission_import_batches ALTER COLUMN progress_percent SET NOT NULL",
        "ALTER TABLE admission_import_batches ALTER COLUMN processed_records SET NOT NULL",
        "ALTER TABLE admission_import_batches ALTER COLUMN total_records SET NOT NULL",
        "ALTER TABLE admission_import_batches ALTER COLUMN status_message SET NOT NULL",
        """CREATE INDEX IF NOT EXISTS idx_admission_import_batches_source
               ON admission_import_batches(source_sha256,mode,imported_at DESC)""",
        """CREATE INDEX IF NOT EXISTS idx_admission_import_batches_active
               ON admission_import_batches(status,last_heartbeat_at DESC)
               WHERE status IN ('ANALYZING','APPLYING')""",
        """CREATE INDEX IF NOT EXISTS idx_admission_import_staging_identity
               ON admission_import_staging(global_attention_id,classification)""",
        """CREATE TABLE IF NOT EXISTS admission_import_schema_migrations(
               migration_id TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
           )""",
        f"""INSERT INTO admission_import_schema_migrations(migration_id)
               VALUES('{ADMISSION_IMPORT_PROGRESS_MIGRATION_ID}')
               ON CONFLICT(migration_id) DO UPDATE SET applied_at=NOW()""",
    )
    for statement in statements:
        connection.execute(statement)

    for column_name, constraint_name, definition in (
        ("status", "chk_admission_import_batches_status",
         "CHECK(status IN ('ANALYZING','ANALYZED','APPLYING','COMPLETED','FAILED'))"),
        ("progress_percent", "chk_admission_import_batches_progress_percent",
         "CHECK(progress_percent BETWEEN 0 AND 100)"),
    ):
        connection.execute(
            f"""DO $$
                DECLARE existing_constraint RECORD;
                BEGIN
                    FOR existing_constraint IN
                        SELECT conname FROM pg_constraint
                         WHERE conrelid='public.admission_import_batches'::regclass
                           AND contype='c'
                           AND pg_get_constraintdef(oid) ILIKE '%{column_name}%'
                    LOOP
                        EXECUTE format(
                            'ALTER TABLE public.admission_import_batches DROP CONSTRAINT IF EXISTS %I',
                            existing_constraint.conname
                        );
                    END LOOP;
                    ALTER TABLE public.admission_import_batches
                        ADD CONSTRAINT {constraint_name} {definition};
                END $$"""
        )


def install_central_hybrid_schema(connection: Any) -> None:
    """Migraci\u00f3n PostgreSQL idempotente, sin borrar registros legacy."""
    execute_script = getattr(connection, "executescript", None)
    if callable(execute_script):
        execute_script(POSTGRES_HYBRID_SCHEMA)
    else:
        for statement in (part.strip() for part in POSTGRES_HYBRID_SCHEMA.split(";") if part.strip()):
            connection.execute(statement)
    ensure_admission_import_progress_schema(connection)


class OfflineAdmissionStore:
    """Extensi\u00f3n transaccional de una SQLite local de Admisi\u00f3n."""

    def __init__(self, database: str | Path | sqlite3.Connection):
        self._database = database
        self.automatic_outbox = False
        self._initialized = False

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        if isinstance(self._database, sqlite3.Connection) or (
            not isinstance(self._database, (str, Path))
            and hasattr(self._database, "execute")
        ):
            yield self._database
            return
        con = connect_local_sqlite(str(self._database), operation="admission-local-write")
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    @staticmethod
    def _add_column(con: sqlite3.Connection, table: str, name: str, definition: str) -> None:
        rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        if rows and name not in {str(row[1]) for row in rows}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")

    def initialize(self) -> None:
        if self._initialized:
            return
        if isinstance(self._database, (str, Path)):
            prepare_sqlite_database(self._database)
        with self.connection() as con:
            con.executescript(SQLITE_HYBRID_SCHEMA)
            con.execute(
                """INSERT OR IGNORE INTO app_metadata(clave,valor)
                   VALUES('integration.source_instance_id',lower(hex(randomblob(16))))"""
            )
            self._add_column(
                con, "sync_runtime_context", "active_username", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column(
                con, "sync_runtime_context", "operational_turn_id", "INTEGER"
            )
            self._add_column(
                con, "sync_runtime_context", "active_user_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column(
                con, "sync_runtime_context", "actor_username", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column(
                con, "sync_runtime_context", "actor_user_id", "TEXT NOT NULL DEFAULT ''"
            )
            self._add_column(con, "admission_operational_cache", "turn_started_at", "TEXT")
            self._add_column(con, "admission_operational_cache", "turn_ends_at", "TEXT")
            for name, definition in (
                ("operational_source_id", "TEXT NOT NULL DEFAULT ''"),
                ("turn_id", "INTEGER"),
                ("origin_user_id", "TEXT NOT NULL DEFAULT ''"),
                ("origin_username", "TEXT NOT NULL DEFAULT ''"),
                ("created_at_device", "TEXT NOT NULL DEFAULT ''"),
                ("created_at_effective_utc", "TEXT NOT NULL DEFAULT ''"),
                ("device_local_sequence", "INTEGER NOT NULL DEFAULT 0"),
                ("server_time_offset_ms", "INTEGER NOT NULL DEFAULT 0"),
                ("sent_at", "TEXT"),
            ):
                self._add_column(con, "sync_outbox", name, definition)
            con.execute(
                "INSERT OR IGNORE INTO app_metadata(clave,valor) "
                "VALUES('sync.server_time_offset_ms','0')"
            )
            # Las columnas nuevas preservan tanto IDs como datos V15 existentes.
            self._add_column(con, "pacientes", "global_patient_id", "TEXT")
            self._add_column(con, "pacientes", "cedula_clean", "TEXT")
            self._add_column(con, "pacientes", "nss_clean", "TEXT")
            self._add_column(con, "pacientes", "version", "INTEGER NOT NULL DEFAULT 1")
            self._add_column(con, "pacientes", "origin_device_id", "TEXT")
            self._add_column(con, "atenciones", "global_attention_id", "TEXT")
            self._add_column(con, "atenciones", "global_patient_id", "TEXT")
            self._add_column(con, "atenciones", "version", "INTEGER NOT NULL DEFAULT 1")
            self._add_column(con, "atenciones", "origin_device_id", "TEXT")
            self._add_column(con, "atenciones", "operational_source_id", "TEXT")
            self._add_column(con, "atenciones", "operational_session_id", "TEXT")
            self._add_column(con, "atenciones", "generation", "INTEGER")
            self._add_column(con, "atenciones", "operational_turn_id", "INTEGER")
            self._add_column(con, "atenciones", "created_at_device", "TEXT")
            self._add_column(con, "atenciones", "created_at_effective_utc", "TEXT")
            self._add_column(con, "atenciones", "device_local_sequence", "INTEGER")
            self._add_column(con, "atenciones", "captured_by_user_id", "TEXT")
            self._add_column(con, "atenciones", "captured_by_username", "TEXT")
            self._add_column(con, "atenciones", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(con, "atenciones", "deleted_at", "TEXT")
            self._add_column(con, "atenciones", "deleted_by_user_id", "TEXT")
            self._add_column(con, "atenciones", "delete_event_uuid", "TEXT")
            self._add_column(con, "atenciones", "delete_reason", "TEXT")
            self._add_column(con, "atenciones", "sync_state", "TEXT NOT NULL DEFAULT 'SYNCED'")
            self._add_column(con, "atenciones", "base_server_revision", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(con, "atenciones", "server_revision", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(con, "atenciones", "legacy_source_instance_id", "TEXT")
            self._add_column(con, "atenciones", "legacy_attention_id", "INTEGER")
            self._add_column(con, "atenciones", "legacy_patient_id", "INTEGER")
            self._add_column(con, "atenciones", "anulada_at", "TEXT")
            self._add_column(con, "atenciones", "anulada_por", "TEXT")
            self._add_column(con, "atenciones", "anulada_motivo", "TEXT")
            self._add_column(con, "pacientes", "is_deleted", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(con, "pacientes", "sync_state", "TEXT NOT NULL DEFAULT 'SYNCED'")
            self._add_column(con, "pacientes", "server_revision", "INTEGER NOT NULL DEFAULT 0")
            self._add_column(
                con, "atenciones", "reconciliation_status", "TEXT NOT NULL DEFAULT 'DIRECT'"
            )
            tables = {
                str(row[0]) for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "pacientes" in tables:
                source = str(con.execute(
                    "SELECT valor FROM app_metadata WHERE clave='integration.source_instance_id'"
                ).fetchone()[0])
                for row in con.execute(
                    "SELECT id FROM pacientes WHERE global_patient_id IS NULL OR TRIM(global_patient_id)=''"
                ).fetchall():
                    con.execute(
                        "UPDATE pacientes SET global_patient_id=? WHERE id=?",
                        (str(uuid.uuid5(uuid.NAMESPACE_URL, f"hospital-legacy-patient:{source}:{int(row[0])}")), int(row[0])),
                    )
            if "atenciones" in tables:
                source = str(con.execute(
                    "SELECT valor FROM app_metadata WHERE clave='integration.source_instance_id'"
                ).fetchone()[0])
                for row in con.execute(
                    "SELECT id,paciente_id FROM atenciones WHERE global_attention_id IS NULL OR TRIM(global_attention_id)=''"
                ).fetchall():
                    con.execute(
                        """UPDATE atenciones SET global_attention_id=?,
                               legacy_source_instance_id=?,legacy_attention_id=?,legacy_patient_id=?
                           WHERE id=?""",
                        (
                            str(uuid.uuid5(uuid.NAMESPACE_URL, f"hospital-legacy-attention:{source}:{int(row[0])}")),
                            source,
                            int(row[0]),
                            int(row[1]),
                            int(row[0]),
                        ),
                    )
                con.execute(
                    "UPDATE atenciones SET global_patient_id=(SELECT global_patient_id "
                    "FROM pacientes p WHERE p.id=atenciones.paciente_id) "
                    "WHERE global_patient_id IS NULL OR TRIM(global_patient_id)=''"
                )
                self._install_attention_outbox_triggers(con)
            con.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_atenciones_global_attention "
                "ON atenciones(global_attention_id)"
            ) if "atenciones" in tables else None
            if "atenciones" in tables:
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_atenciones_global_patient ON atenciones(global_patient_id)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_atenciones_turn_deleted ON atenciones(operational_turn_id,is_deleted)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_atenciones_effective_order ON atenciones(created_at_effective_utc,origin_device_id,device_local_sequence,global_attention_id)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_atenciones_sync_state ON atenciones(sync_state)"
                )
            # The directory is part of the local admission replica.  Preparing
            # it during the normal replica bootstrap keeps schema DDL out of
            # the existing-patient save path.
            if "pacientes" in tables:
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pacientes_cedula_clean "
                    "ON pacientes(cedula_clean)"
                )
                con.execute(
                    "CREATE INDEX IF NOT EXISTS idx_pacientes_nss_clean "
                    "ON pacientes(nss_clean)"
                )
                con.execute(
                    """CREATE TABLE IF NOT EXISTS patient_directory_state(
                           state_key TEXT PRIMARY KEY,
                           state_value TEXT NOT NULL,
                           updated_at TEXT NOT NULL
                       )"""
                )
                con.execute(
                    "INSERT OR REPLACE INTO app_metadata(clave,valor) "
                    "VALUES('admission.patient_directory_schema_version','1')"
                )
        self._initialized = True

    @staticmethod
    def _install_attention_outbox_triggers(con: sqlite3.Connection) -> None:
        """Registra la cola en la misma transacción de atenciones legacy."""
        con.executescript(
            """
            DROP TRIGGER IF EXISTS trg_admission_sync_patient_identity;
            DROP TRIGGER IF EXISTS trg_admission_sync_attention_create;
            DROP TRIGGER IF EXISTS trg_admission_sync_attention_update;
            CREATE TRIGGER trg_admission_sync_patient_identity
            AFTER INSERT ON pacientes
            BEGIN
              UPDATE pacientes
                 SET global_patient_id=COALESCE(
                       NULLIF(global_patient_id,''),lower(hex(randomblob(16))))
               WHERE id=NEW.id;
            END;
            CREATE TRIGGER trg_admission_sync_attention_create
            AFTER INSERT ON atenciones
            WHEN EXISTS (SELECT 1 FROM sync_runtime_context WHERE singleton=1)
             AND NOT EXISTS (SELECT 1 FROM sync_apply_context WHERE singleton=1)
            BEGIN
              UPDATE sync_device_sequence
                 SET last_sequence=last_sequence+1 WHERE singleton=1;
              UPDATE atenciones
                 SET global_attention_id=COALESCE(NULLIF(global_attention_id,''),lower(hex(randomblob(16)))),
                     global_patient_id=COALESCE(NULLIF(global_patient_id,''),
                         (SELECT global_patient_id FROM pacientes p WHERE p.id=atenciones.paciente_id)),
                     version=COALESCE(version,1),
                     origin_device_id=(SELECT device_id FROM sync_runtime_context WHERE singleton=1),
                     operational_source_id=(SELECT operational_source_id FROM sync_runtime_context WHERE singleton=1),
                     operational_session_id=(SELECT operational_session_id FROM sync_runtime_context WHERE singleton=1),
                     generation=(SELECT generation FROM sync_runtime_context WHERE singleton=1),
                     operational_turn_id=COALESCE(
                         (SELECT operational_turn_id FROM sync_runtime_context WHERE singleton=1),
                         NEW.turno_id),
                     created_at_device=strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                     created_at_effective_utc=strftime(
                         '%Y-%m-%dT%H:%M:%fZ','now',
                         printf('%+.3f seconds',COALESCE((
                           SELECT CAST(valor AS REAL)/1000.0 FROM app_metadata
                           WHERE clave='sync.server_time_offset_ms'),0))),
                     device_local_sequence=(SELECT last_sequence FROM sync_device_sequence WHERE singleton=1),
                     captured_by_user_id=COALESCE(NULLIF((SELECT actor_user_id FROM sync_runtime_context WHERE singleton=1),''),(SELECT active_user_id FROM sync_runtime_context WHERE singleton=1)),
                     captured_by_username=COALESCE(NULLIF((SELECT actor_username FROM sync_runtime_context WHERE singleton=1),''),(SELECT active_username FROM sync_runtime_context WHERE singleton=1)),
                     reconciliation_status='DIRECT',
                     is_deleted=0,
                     sync_state='LOCAL_NEW',
                     base_server_revision=0,
                     server_revision=0
               WHERE id=NEW.id;
              INSERT OR IGNORE INTO sync_outbox(
                  event_uuid,entity_type,entity_uuid,operation,payload_json,
                  operational_session_id,operational_source_id,generation,turn_id,
                  device_id,origin_user_id,origin_username,created_at_device,
                  created_at_effective_utc,device_local_sequence,server_time_offset_ms,
                  base_version,created_at,sync_status,retry_count
              ) SELECT lower(hex(randomblob(16))),'attention',a.global_attention_id,'CREATE',
                  json_object('attention_id',a.id,'global_attention_id',a.global_attention_id,
                              'patient_id',a.paciente_id,'global_patient_id',a.global_patient_id,
                              'turn_id',COALESCE(c.operational_turn_id,a.turno_id),
                              'local_turn_id',a.turno_id,'name',a.nombre,'sex',a.sexo,
                              'age',a.edad_num,'age_unit',a.unidad,
                              'cedula',a.cedula,'phone',a.telefono,
                              'address',a.direccion,'nationality',a.nacionalidad,
                              'ars',a.ars,'nss',a.nss,'detail_sheet',a.hoja,
                              'service_date',a.fecha,'service_time',a.hora,
                              'service_type',a.tipo_atencion,
                              'source_status',a.estado,'version',a.version,
                              'source_instance_id',COALESCE(
                                  (SELECT valor FROM app_metadata
                                   WHERE clave='integration.source_instance_id'),''),
                              'operational_source_id',a.operational_source_id,
                              'origin_device_id',a.origin_device_id,
                              'admission_username',c.active_username,
                              'operational_representative_user_id',c.active_user_id,
                              'captured_by_username',COALESCE(NULLIF(c.actor_username,''),c.active_username),
                              'origin_user_id',COALESCE(NULLIF(c.actor_user_id,''),c.active_user_id),
                              'created_at_device',a.created_at_device,
                              'created_at_effective_utc',a.created_at_effective_utc,
                              'device_local_sequence',a.device_local_sequence,
                              'is_deleted',0,'base_server_revision',0,
                              'legacy_source_instance_id',a.legacy_source_instance_id,
                              'legacy_attention_id',COALESCE(a.legacy_attention_id,a.id),
                              'legacy_patient_id',COALESCE(a.legacy_patient_id,a.paciente_id)),
                  c.operational_session_id,c.operational_source_id,c.generation,
                  COALESCE(c.operational_turn_id,a.turno_id),c.device_id,
                  COALESCE(NULLIF(c.actor_user_id,''),c.active_user_id),
                  COALESCE(NULLIF(c.actor_username,''),c.active_username),a.created_at_device,
                  a.created_at_effective_utc,a.device_local_sequence,
                  COALESCE((SELECT CAST(valor AS INTEGER) FROM app_metadata
                            WHERE clave='sync.server_time_offset_ms'),0),0,
                  strftime('%Y-%m-%dT%H:%M:%SZ','now'),'PENDING',0
              FROM atenciones a CROSS JOIN sync_runtime_context c
              WHERE a.id=NEW.id AND c.singleton=1;
            END;
            CREATE TRIGGER trg_admission_sync_attention_update
            AFTER UPDATE OF nombre,sexo,edad_num,unidad,cedula,telefono,direccion,nacionalidad,
                            ars,hoja,fecha,hora,tipo_atencion,estado,nss,turno_id
            ON atenciones
            WHEN EXISTS (SELECT 1 FROM sync_runtime_context WHERE singleton=1)
             AND NOT EXISTS (SELECT 1 FROM sync_apply_context WHERE singleton=1)
            BEGIN
              UPDATE sync_device_sequence
                 SET last_sequence=last_sequence+1 WHERE singleton=1;
              UPDATE atenciones SET
                  version=COALESCE(version,1)+1,
                  origin_device_id=(SELECT device_id FROM sync_runtime_context WHERE singleton=1),
                  operational_source_id=(SELECT operational_source_id FROM sync_runtime_context WHERE singleton=1),
                  operational_session_id=(SELECT operational_session_id FROM sync_runtime_context WHERE singleton=1),
                  generation=(SELECT generation FROM sync_runtime_context WHERE singleton=1),
                  base_server_revision=COALESCE(server_revision,0),
                  is_deleted=CASE WHEN UPPER(COALESCE(estado,''))='ANULADA' THEN 1 ELSE is_deleted END,
                  deleted_at=CASE WHEN UPPER(COALESCE(estado,''))='ANULADA'
                                  THEN COALESCE(deleted_at,NEW.anulada_at,
                                      strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                                  ELSE deleted_at END,
                  deleted_by_user_id=CASE WHEN UPPER(COALESCE(estado,''))='ANULADA'
                                  THEN COALESCE(NULLIF(deleted_by_user_id,''),
                                      NULLIF(NEW.anulada_por,''),
                                      COALESCE(
                                          (SELECT NULLIF(actor_user_id,'') FROM sync_runtime_context WHERE singleton=1),
                                          (SELECT active_user_id FROM sync_runtime_context WHERE singleton=1)
                                      ))
                                  ELSE deleted_by_user_id END,
                  delete_event_uuid=CASE WHEN UPPER(COALESCE(estado,''))='ANULADA'
                                  THEN COALESCE(NULLIF(delete_event_uuid,''),lower(hex(randomblob(16))))
                                  ELSE delete_event_uuid END,
                  delete_reason=CASE WHEN UPPER(COALESCE(estado,''))='ANULADA'
                                  THEN COALESCE(NULLIF(delete_reason,''),NEW.anulada_motivo,'')
                                  ELSE delete_reason END,
                  sync_state=CASE WHEN UPPER(COALESCE(estado,''))='ANULADA'
                                  THEN 'LOCAL_DELETED' ELSE 'LOCAL_DIRTY' END
              WHERE id=NEW.id;
              INSERT OR IGNORE INTO sync_outbox(
                  event_uuid,entity_type,entity_uuid,operation,payload_json,
                  operational_session_id,operational_source_id,generation,turn_id,
                  device_id,origin_user_id,origin_username,created_at_device,
                  created_at_effective_utc,device_local_sequence,server_time_offset_ms,
                  base_version,created_at,sync_status,retry_count
              ) SELECT CASE WHEN UPPER(COALESCE(a.estado,''))='ANULADA'
                            THEN a.delete_event_uuid ELSE lower(hex(randomblob(16))) END,
                  'attention',a.global_attention_id,
                  CASE
                    WHEN UPPER(COALESCE(a.estado,''))='ANULADA' THEN 'DELETE'
                    WHEN COALESCE(OLD.hoja,'')<>COALESCE(NEW.hoja,'')
                         AND TRIM(COALESCE(NEW.hoja,''))<>'' THEN 'DETAIL_SHEET_GENERATED'
                    WHEN COALESCE(OLD.turno_id,0)<>COALESCE(NEW.turno_id,0)
                         THEN 'ATTENTION_TURN_REASSIGNED'
                    ELSE 'UPDATE'
                  END,
                  json_object('attention_id',a.id,'global_attention_id',a.global_attention_id,
                              'patient_id',a.paciente_id,'global_patient_id',a.global_patient_id,
                              'turn_id',COALESCE(c.operational_turn_id,a.turno_id),
                              'local_turn_id',a.turno_id,'name',a.nombre,'sex',a.sexo,
                              'age',a.edad_num,'age_unit',a.unidad,
                              'cedula',a.cedula,'phone',a.telefono,
                              'address',a.direccion,'nationality',a.nacionalidad,
                              'ars',a.ars,'nss',a.nss,'detail_sheet',a.hoja,
                              'service_date',a.fecha,'service_time',a.hora,
                              'service_type',a.tipo_atencion,
                              'source_status',a.estado,'version',a.version,
                              'source_instance_id',COALESCE(
                                  (SELECT valor FROM app_metadata
                                   WHERE clave='integration.source_instance_id'),''),
                              'operational_source_id',a.operational_source_id,
                              'origin_device_id',a.origin_device_id,
                              'admission_username',c.active_username,
                              'operational_representative_user_id',c.active_user_id,
                              'captured_by_username',COALESCE(NULLIF(c.actor_username,''),c.active_username),
                              'origin_user_id',COALESCE(NULLIF(c.actor_user_id,''),c.active_user_id),
                              'created_at_device',strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                              'created_at_effective_utc',strftime(
                                '%Y-%m-%dT%H:%M:%fZ','now',
                                printf('%+.3f seconds',COALESCE((
                                  SELECT CAST(valor AS REAL)/1000.0 FROM app_metadata
                                  WHERE clave='sync.server_time_offset_ms'),0))),
                              'device_local_sequence',(SELECT last_sequence FROM sync_device_sequence WHERE singleton=1),
                              'is_deleted',a.is_deleted,'deleted_at',a.deleted_at,
                              'deleted_by_user_id',a.deleted_by_user_id,
                              'delete_event_uuid',a.delete_event_uuid,
                              'delete_reason',COALESCE(a.delete_reason,a.anulada_motivo,''),
                              'base_server_revision',a.base_server_revision,
                              'legacy_source_instance_id',a.legacy_source_instance_id,
                              'legacy_attention_id',COALESCE(a.legacy_attention_id,a.id),
                              'legacy_patient_id',COALESCE(a.legacy_patient_id,a.paciente_id)),
                  c.operational_session_id,c.operational_source_id,c.generation,
                  COALESCE(c.operational_turn_id,a.turno_id),c.device_id,
                  COALESCE(NULLIF(c.actor_user_id,''),c.active_user_id),
                  COALESCE(NULLIF(c.actor_username,''),c.active_username),
                  strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                  strftime('%Y-%m-%dT%H:%M:%fZ','now',
                    printf('%+.3f seconds',COALESCE((SELECT CAST(valor AS REAL)/1000.0
                      FROM app_metadata WHERE clave='sync.server_time_offset_ms'),0))),
                  (SELECT last_sequence FROM sync_device_sequence WHERE singleton=1),
                  COALESCE((SELECT CAST(valor AS INTEGER) FROM app_metadata
                            WHERE clave='sync.server_time_offset_ms'),0),a.base_server_revision,
                  strftime('%Y-%m-%dT%H:%M:%SZ','now'),'PENDING',0
              FROM atenciones a CROSS JOIN sync_runtime_context c
              WHERE a.id=NEW.id AND c.singleton=1;
            END;
            """
        )


    def configure_runtime_context(
        self,
        session: OperationalSession,
        *,
        device_id: str,
        actor_user_id: Any = None,
        actor_username: str = "",
    ) -> None:
        """Configura triggers separando actor autenticado y representante."""
        self.initialize()
        actor_id = str(actor_user_id or session.active_user_id or "")
        actor_name = str(actor_username or session.active_username or "")
        with self.connection() as con:
            con.execute(
                """INSERT INTO sync_runtime_context(
                       singleton,operational_session_id,generation,device_id,
                       operational_source_id,operational_turn_id,active_username,
                       active_user_id,actor_username,actor_user_id,updated_at
                   ) VALUES(1,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET
                       operational_session_id=excluded.operational_session_id,
                       generation=excluded.generation,device_id=excluded.device_id,
                       operational_source_id=excluded.operational_source_id,
                       operational_turn_id=excluded.operational_turn_id,
                       active_username=excluded.active_username,
                       active_user_id=excluded.active_user_id,
                       actor_username=excluded.actor_username,
                       actor_user_id=excluded.actor_user_id,
                       updated_at=excluded.updated_at""",
                (
                    session.operational_session_id, session.generation, str(device_id),
                    session.operational_source_id, session.turn_id,
                    session.active_username, session.active_user_id,
                    actor_name, actor_id, _timestamp(),
                ),
            )
        self.automatic_outbox = True

    def cache_operational_session(
        self,
        session: OperationalSession,
        role: StationRole,
        *,
        lease_seconds: int = DEFAULT_OFFLINE_LEASE_SECONDS,
    ) -> None:
        self.initialize()
        now = _utc_now()
        expires = now + timedelta(seconds=max(1, int(lease_seconds)))
        values = (
            session.operational_session_id, session.active_username,
            session.active_user_id, session.primary_device_id, session.turn_id,
            session.operational_source_id, session.generation, role.value,
            session.turn_started_at, session.turn_ends_at,
            _timestamp(now), _timestamp(expires),
        )
        with self.connection() as con:
            con.execute(
                """INSERT INTO admission_operational_cache(
                       singleton,operational_session_id,active_username,active_user_id,
                       primary_device_id,turn_id,operational_source_id,generation,role,
                       turn_started_at,turn_ends_at,
                       verified_at,lease_expires_at
                   ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET
                       operational_session_id=excluded.operational_session_id,
                       active_username=excluded.active_username,
                       active_user_id=excluded.active_user_id,
                       primary_device_id=excluded.primary_device_id,
                       turn_id=excluded.turn_id,
                       operational_source_id=excluded.operational_source_id,
                       generation=excluded.generation,role=excluded.role,
                       turn_started_at=excluded.turn_started_at,
                       turn_ends_at=excluded.turn_ends_at,
                       verified_at=excluded.verified_at,lease_expires_at=excluded.lease_expires_at""",
                values,
            )


    def apply_remote_operational_state(
        self,
        session: OperationalSession,
        role: StationRole,
        *,
        device_id: str,
        lease_seconds: int = DEFAULT_OFFLINE_LEASE_SECONDS,
        actor_user_id: Any = None,
        actor_username: str = "",
    ) -> None:
        """Aplica snapshot central y conserva actor/representante por separado."""
        self.initialize()
        now = _utc_now()
        expires = now + timedelta(seconds=max(1, int(lease_seconds)))
        representative = (
            session.active_user_display_name or session.active_username
        ).strip()
        actor_id = str(actor_user_id or session.active_user_id or "")
        actor_name = str(actor_username or session.active_username or "")
        with self.connection() as con:
            con.execute(
                """INSERT INTO admission_operational_cache(
                       singleton,operational_session_id,active_username,active_user_id,
                       primary_device_id,turn_id,operational_source_id,generation,role,
                       turn_started_at,turn_ends_at,verified_at,lease_expires_at
                   ) VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET
                       operational_session_id=excluded.operational_session_id,
                       active_username=excluded.active_username,
                       active_user_id=excluded.active_user_id,
                       primary_device_id=excluded.primary_device_id,
                       turn_id=excluded.turn_id,
                       operational_source_id=excluded.operational_source_id,
                       generation=excluded.generation,role=excluded.role,
                       turn_started_at=excluded.turn_started_at,
                       turn_ends_at=excluded.turn_ends_at,
                       verified_at=excluded.verified_at,
                       lease_expires_at=excluded.lease_expires_at""",
                (
                    session.operational_session_id, session.active_username,
                    session.active_user_id, session.primary_device_id, session.turn_id,
                    session.operational_source_id, session.generation, role.value,
                    session.turn_started_at, session.turn_ends_at,
                    _timestamp(now), _timestamp(expires),
                ),
            )
            con.execute(
                """INSERT INTO sync_runtime_context(
                       singleton,operational_session_id,generation,device_id,
                       operational_source_id,operational_turn_id,active_username,
                       active_user_id,actor_username,actor_user_id,updated_at
                   ) VALUES(1,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(singleton) DO UPDATE SET
                       operational_session_id=excluded.operational_session_id,
                       generation=excluded.generation,device_id=excluded.device_id,
                       operational_source_id=excluded.operational_source_id,
                       operational_turn_id=excluded.operational_turn_id,
                       active_username=excluded.active_username,
                       active_user_id=excluded.active_user_id,
                       actor_username=excluded.actor_username,
                       actor_user_id=excluded.actor_user_id,
                       updated_at=excluded.updated_at""",
                (
                    session.operational_session_id, session.generation, str(device_id),
                    session.operational_source_id, session.turn_id,
                    session.active_username, session.active_user_id,
                    actor_name, actor_id, _timestamp(now),
                ),
            )
            turn_columns = {
                str(row[1]) for row in con.execute("PRAGMA table_info(turnos)").fetchall()
            }
            if representative and {"representante", "estado"} <= turn_columns:
                assignments = ["representante=?"]
                if "updated_at" in turn_columns:
                    assignments.append("updated_at=datetime('now','localtime')")
                con.execute(
                    "UPDATE turnos SET " + ",".join(assignments)
                    + " WHERE UPPER(COALESCE(estado,''))='ABIERTO'",
                    (representative,),
                )
        self.automatic_outbox = True

    def cached_attachment(self, *, now: datetime | None = None) -> DeviceAttachment | None:
        self.initialize()
        with self.connection() as con:
            row = con.execute("SELECT * FROM admission_operational_cache WHERE singleton=1").fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            valid = datetime.fromisoformat(str(data["lease_expires_at"])) > (now or _utc_now())
        except ValueError:
            valid = False
        session = OperationalSession.from_mapping(data)
        role = StationRole(str(data.get("role") or "NONE"))
        return DeviceAttachment(session, role, valid and role != StationRole.NONE)

    def queue_sync_event(self, event: SyncEvent) -> None:
        self.initialize()
        with self.connection() as con:
            con.execute(
                """INSERT OR IGNORE INTO sync_outbox(
                       event_uuid,entity_type,entity_uuid,operation,payload_json,
                       operational_session_id,operational_source_id,generation,turn_id,
                       device_id,origin_user_id,origin_username,created_at_device,
                       created_at_effective_utc,device_local_sequence,server_time_offset_ms,
                       base_version,created_at,sync_status,retry_count
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',0)""",
                (
                    event.event_uuid, event.entity_type, event.entity_uuid,
                    event.operation, event.payload_json(), event.operational_session_id,
                    event.operational_source_id,event.generation,event.turn_id,
                    event.device_id,event.origin_user_id,event.origin_username,
                    event.created_at_device or event.created_at,
                    event.created_at_effective_utc or event.created_at,
                    event.device_local_sequence,event.server_time_offset_ms,
                    event.base_version,event.created_at,
                ),
            )

    def queue_detail_sheet_generated(self, attention_id: int) -> bool:
        """Persist the document event without keeping SQLite open during rendering."""
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                """SELECT a.*,p.global_patient_id AS patient_global_id
                     FROM atenciones a
                     LEFT JOIN pacientes p ON p.id=a.paciente_id
                    WHERE a.id=?""",
                (int(attention_id),),
            ).fetchone()
            runtime = con.execute(
                "SELECT * FROM sync_runtime_context WHERE singleton=1"
            ).fetchone()
            if not row or not runtime:
                return False
            data = dict(row)
            pending_create = con.execute(
                """SELECT 1 FROM sync_outbox
                    WHERE entity_uuid=? AND operation='CREATE'
                      AND sync_status IN ('PENDING','RETRY') LIMIT 1""",
                (str(data.get("global_attention_id") or ""),),
            ).fetchone()
            if pending_create:
                return False
            con.execute(
                "UPDATE sync_device_sequence SET last_sequence=last_sequence+1 WHERE singleton=1"
            )
            sequence = int(con.execute(
                "SELECT last_sequence FROM sync_device_sequence WHERE singleton=1"
            ).fetchone()[0])
            now = _timestamp()
            actor_user_id = str(
                runtime["actor_user_id"] or runtime["active_user_id"] or ""
            )
            actor_username = str(
                runtime["actor_username"] or runtime["active_username"] or ""
            )
            payload = {
                "event_type": "DETAIL_SHEET_GENERATED",
                "attention_id": int(data.get("id") or 0),
                "global_attention_id": str(data.get("global_attention_id") or ""),
                "patient_id": int(data.get("paciente_id") or 0),
                "global_patient_id": str(
                    data.get("global_patient_id") or data.get("patient_global_id") or ""
                ),
                "name": str(data.get("nombre") or ""),
                "ars": str(data.get("ars") or ""),
                "nss": str(data.get("nss") or ""),
                "cedula": str(data.get("cedula") or ""),
                "detail_sheet": str(data.get("hoja") or "GENERADA"),
                "service_date": str(data.get("fecha") or ""),
                "service_time": str(data.get("hora") or ""),
                "service_type": str(data.get("tipo_atencion") or "EMERGENCIA"),
                "source_status": str(data.get("estado") or "ACTIVA"),
                "source_instance_id": str(con.execute(
                    "SELECT valor FROM app_metadata WHERE clave='integration.source_instance_id'"
                ).fetchone()[0]),
                "operational_source_id": str(runtime["operational_source_id"] or ""),
                "origin_device_id": str(runtime["device_id"] or ""),
                "admission_username": str(runtime["active_username"] or ""),
                "operational_representative_user_id": str(
                    runtime["active_user_id"] or ""
                ),
                "origin_user_id": actor_user_id,
                "captured_by_username": actor_username,
                "created_at_device": now,
                "created_at_effective_utc": str(data.get("created_at_effective_utc") or now),
                "device_local_sequence": sequence,
                "version": int(data.get("version") or 1),
            }
            event_uuid = str(uuid.uuid4())
            con.execute(
                """INSERT INTO sync_outbox(
                       event_uuid,entity_type,entity_uuid,operation,payload_json,
                       operational_session_id,operational_source_id,generation,turn_id,
                       device_id,origin_user_id,origin_username,created_at_device,
                       created_at_effective_utc,device_local_sequence,server_time_offset_ms,
                       base_version,created_at,sync_status,retry_count
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',0)""",
                (
                    event_uuid,
                    "attention",
                    str(data.get("global_attention_id") or ""),
                    "DETAIL_SHEET_GENERATED",
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    str(runtime["operational_session_id"]),
                    str(runtime["operational_source_id"]),
                    int(runtime["generation"]),
                    int(data.get("operational_turn_id") or runtime["operational_turn_id"] or 0),
                    str(runtime["device_id"]),
                    actor_user_id,
                    actor_username,
                    now,
                    str(data.get("created_at_effective_utc") or now),
                    sequence,
                    int((con.execute(
                        "SELECT valor FROM app_metadata WHERE clave='sync.server_time_offset_ms'"
                    ).fetchone() or [0])[0] or 0),
                    int(data.get("server_revision") or 0),
                    now,
                ),
            )
            return True

    def pending_events(self, limit: int = 100) -> list[SyncEvent]:
        self.initialize()
        with self.connection() as con:
            rows = con.execute(
                """SELECT * FROM sync_outbox WHERE sync_status IN ('PENDING','RETRY')
                   ORDER BY created_at_effective_utc,device_local_sequence,event_uuid
                   LIMIT ?""", (max(1, min(int(limit), 500)),)
            ).fetchall()
        return [
            SyncEvent(
                event_uuid=str(row["event_uuid"]), entity_type=str(row["entity_type"]),
                entity_uuid=str(row["entity_uuid"]), operation=str(row["operation"]),
                payload=json.loads(row["payload_json"]),
                operational_session_id=str(row["operational_session_id"]),
                generation=int(row["generation"]), device_id=str(row["device_id"]),
                created_at=str(row["created_at"]), base_version=int(row["base_version"] or 0),
                operational_source_id=str(row["operational_source_id"] or ""),
                turn_id=_as_int_or_none(row["turn_id"]),
                origin_user_id=str(row["origin_user_id"] or ""),
                origin_username=str(row["origin_username"] or ""),
                created_at_device=str(row["created_at_device"] or row["created_at"]),
                created_at_effective_utc=str(
                    row["created_at_effective_utc"] or row["created_at"]
                ),
                device_local_sequence=int(row["device_local_sequence"] or 0),
                server_time_offset_ms=int(row["server_time_offset_ms"] or 0),
            ) for row in rows
        ]

    def pending_count(self) -> int:
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                """SELECT COUNT(*) FROM sync_outbox
                   WHERE sync_status IN ('PENDING','RETRY')"""
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def server_time_offset_ms(self) -> int:
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                "SELECT valor FROM app_metadata WHERE clave='sync.server_time_offset_ms'"
            ).fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def update_server_time_offset(
        self, server_time: datetime | str, *, measured_at: datetime | None = None,
    ) -> dict[str, Any]:
        self.initialize()
        if isinstance(server_time, str):
            parsed = datetime.fromisoformat(server_time.replace("Z", "+00:00"))
        else:
            parsed = server_time
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        local_time = measured_at or _utc_now()
        if local_time.tzinfo is None:
            local_time = local_time.replace(tzinfo=timezone.utc)
        offset_ms = int((parsed - local_time).total_seconds() * 1000)
        drift_detected = abs(offset_ms) > MAX_CLOCK_DRIFT_MS
        with self.connection() as con:
            con.execute(
                """INSERT INTO app_metadata(clave,valor)
                   VALUES('sync.server_time_offset_ms',?)
                   ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor""",
                (str(offset_ms),),
            )
            con.execute(
                """INSERT INTO sync_state(state_key,state_value,updated_at)
                   VALUES('sync.clock_status',?,?)
                   ON CONFLICT(state_key) DO UPDATE SET
                     state_value=excluded.state_value,updated_at=excluded.updated_at""",
                ("TIME_DRIFT_DETECTED" if drift_detected else "SYNCHRONIZED", _timestamp()),
            )
        return {"server_time_offset_ms": offset_ms, "drift_detected": drift_detected}

    def queue_missing_attention_events(
        self,
        *,
        limit: int = 100,
        central_seed_id: str = "",
    ) -> int:
        """Recupera atenciones legacy sin outbox, en lotes acotados e idempotentes."""
        self.initialize()
        queued = 0
        seed_id = str(central_seed_id or "").strip()
        with self.connection() as con:
            runtime = con.execute(
                "SELECT * FROM sync_runtime_context WHERE singleton=1"
            ).fetchone()
            if not runtime:
                return 0
            source_row = con.execute(
                "SELECT valor FROM app_metadata "
                "WHERE clave='integration.source_instance_id'"
            ).fetchone()
            source_instance_id = str(source_row[0] if source_row else "LEGACY")
            row_limit = max(1, min(int(limit), 500))
            turn_columns = {
                str(row[1]) for row in con.execute("PRAGMA table_info(turnos)")
            }
            legacy_representative = (
                "t.representante" if "representante" in turn_columns else "NULL"
            )
            turn_join = (
                "LEFT JOIN turnos t ON t.id=a.turno_id"
                if "representante" in turn_columns
                else ""
            )
            if seed_id:
                rows = con.execute(
                    f"""SELECT a.*,p.global_patient_id AS patient_global_id,
                               {legacy_representative} AS legacy_turn_representative
                       FROM atenciones a
                       LEFT JOIN pacientes p ON p.id=a.paciente_id
                       {turn_join}
                       WHERE NULLIF(TRIM(COALESCE(a.global_attention_id,'')),'') IS NOT NULL
                         AND NOT EXISTS (
                             SELECT 1 FROM sync_seed_entities s
                              WHERE s.central_seed_id=?
                                AND s.entity_uuid=a.global_attention_id
                         )
                       ORDER BY a.id LIMIT ?""",
                    (seed_id, row_limit),
                ).fetchall()
            else:
                rows = con.execute(
                    f"""SELECT a.*,p.global_patient_id AS patient_global_id,
                               {legacy_representative} AS legacy_turn_representative
                       FROM atenciones a
                       LEFT JOIN pacientes p ON p.id=a.paciente_id
                       {turn_join}
                       WHERE NULLIF(TRIM(COALESCE(a.global_attention_id,'')),'') IS NOT NULL
                         AND (
                             NULLIF(TRIM(COALESCE(a.origin_device_id,'')),'') IS NULL
                             OR REPLACE(LOWER(a.origin_device_id),'-','')=
                                REPLACE(LOWER(?),'-','')
                         )
                         AND NOT EXISTS (
                             SELECT 1 FROM sync_outbox o
                             WHERE o.entity_type='attention'
                               AND o.entity_uuid=a.global_attention_id
                         )
                       ORDER BY a.id DESC LIMIT ?""",
                    (str(runtime["device_id"] or ""), row_limit),
                ).fetchall()
            for row in rows:
                data = dict(row)
                entity_uuid = str(data.get("global_attention_id") or "").strip()
                if not entity_uuid:
                    continue
                version = max(1, int(data.get("version") or 1))
                con.execute(
                    "UPDATE sync_device_sequence SET last_sequence=last_sequence+1 WHERE singleton=1"
                )
                local_sequence = int(
                    con.execute(
                        "SELECT last_sequence FROM sync_device_sequence WHERE singleton=1"
                    ).fetchone()[0]
                )
                created_at_device = str(
                    data.get("created_at_device")
                    or _legacy_effective_timestamp(data.get("fecha"), data.get("hora"))
                )
                created_at_effective = str(
                    data.get("created_at_effective_utc") or created_at_device
                )
                event_identity = (
                    f"hospital-admission-seed:{seed_id}:{entity_uuid}"
                    if seed_id
                    else f"hospital-admission-reconcile:{entity_uuid}:{version}"
                )
                event_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, event_identity))
                is_deleted = bool(data.get("is_deleted"))
                payload = {
                    "event_type": (
                        "ATTENTION_DELETED" if is_deleted else "ATTENTION_RECONCILED"
                    ),
                    "attention_id": int(data.get("id") or 0),
                    "global_attention_id": entity_uuid,
                    "patient_id": int(data.get("paciente_id") or 0),
                    "global_patient_id": str(
                        data.get("global_patient_id")
                        or data.get("patient_global_id")
                        or ""
                    ),
                    "turn_id": int(data.get("turno_id") or runtime["operational_turn_id"] or 0),
                    "local_turn_id": int(data.get("turno_id") or 0),
                    "name": str(data.get("nombre") or ""),
                    "sex": str(data.get("sexo") or ""),
                    "age": data.get("edad_num"),
                    "age_unit": str(data.get("unidad") or ""),
                    "cedula": str(data.get("cedula") or ""),
                    "phone": str(data.get("telefono") or ""),
                    "address": str(data.get("direccion") or ""),
                    "nationality": str(data.get("nacionalidad") or ""),
                    "ars": str(data.get("ars") or ""),
                    "nss": str(data.get("nss") or ""),
                    "detail_sheet": str(data.get("hoja") or ""),
                    "service_date": str(data.get("fecha") or ""),
                    "service_time": str(data.get("hora") or ""),
                    "service_type": str(data.get("tipo_atencion") or "EMERGENCIA"),
                    "source_status": str(data.get("estado") or "ACTIVA"),
                    "is_deleted": bool(
                        data.get("is_deleted")
                        or str(data.get("estado") or "").upper() == "ANULADA"
                    ),
                    "deleted_at": str(data.get("deleted_at") or data.get("anulada_at") or ""),
                    "deleted_by_user_id": str(
                        data.get("deleted_by_user_id") or data.get("anulada_por") or ""
                    ),
                    "delete_event_uuid": str(data.get("delete_event_uuid") or ""),
                    "delete_reason": str(data.get("delete_reason") or data.get("anulada_motivo") or ""),
                    "version": version,
                    "source_instance_id": source_instance_id,
                    "operational_source_id": str(
                        data.get("operational_source_id")
                        or runtime["operational_source_id"]
                        or ""
                    ),
                    "operational_session_id": str(
                        data.get("operational_session_id")
                        or runtime["operational_session_id"]
                        or ""
                    ),
                    "generation": int(
                        data.get("generation") or runtime["generation"] or 0
                    ),
                    "origin_device_id": str(
                        data.get("origin_device_id") or runtime["device_id"] or ""
                    ),
                    "admission_username": str(runtime["active_username"] or ""),
                    "captured_by_username": str(
                        data.get("captured_by_username")
                        or data.get("legacy_turn_representative")
                        or runtime["active_username"]
                        or ""
                    ),
                    "origin_user_id": str(runtime["active_user_id"] or ""),
                    "created_at_device": created_at_device,
                    "created_at_effective_utc": created_at_effective,
                    "device_local_sequence": local_sequence,
                    "updated_at": str(data.get("updated_at") or data.get("created_at") or ""),
                    "reconciliation_status": (
                        "CENTRAL_SEED" if seed_id else "LEGACY_RECOVERY"
                    ),
                }
                con.execute(
                    """INSERT OR IGNORE INTO sync_outbox(
                           event_uuid,entity_type,entity_uuid,operation,payload_json,
                           operational_session_id,operational_source_id,generation,turn_id,
                           device_id,origin_user_id,origin_username,created_at_device,
                           created_at_effective_utc,device_local_sequence,server_time_offset_ms,
                           base_version,created_at,sync_status,retry_count
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'PENDING',0)""",
                    (
                        event_uuid,
                        "attention",
                        entity_uuid,
                        "DELETE" if payload["is_deleted"] else "RECONCILE",
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        str(runtime["operational_session_id"]),
                        str(runtime["operational_source_id"]),
                        int(runtime["generation"]),
                        int(data.get("operational_turn_id") or data.get("turno_id") or 0),
                        str(runtime["device_id"]),
                        str(runtime["active_user_id"] or ""),
                        str(runtime["active_username"] or ""),
                        created_at_device,
                        created_at_effective,
                        local_sequence,
                        int((con.execute(
                            "SELECT valor FROM app_metadata "
                            "WHERE clave='sync.server_time_offset_ms'"
                        ).fetchone() or [0])[0] or 0),
                        0,
                        _timestamp(),
                    ),
                )
                inserted = int(con.execute("SELECT changes()").fetchone()[0] or 0)
                if seed_id:
                    con.execute(
                        """UPDATE sync_outbox
                              SET sync_status='PENDING',sent_at=NULL,last_error=NULL
                            WHERE event_uuid=?""",
                        (event_uuid,),
                    )
                    con.execute(
                        """INSERT OR IGNORE INTO sync_seed_entities(
                               central_seed_id,entity_uuid,event_uuid,queued_at
                           ) VALUES(?,?,?,?)""",
                        (seed_id, entity_uuid, event_uuid, _timestamp()),
                    )
                queued += inserted
        return queued

    def recent_attention_entity_ids(self, *, limit: int = 100) -> list[str]:
        """Identidades recientes para reparar una proyecciÃ³n central degradada."""
        self.initialize()
        with self.connection() as con:
            rows = con.execute(
                """SELECT global_attention_id FROM atenciones
                   WHERE NULLIF(TRIM(COALESCE(global_attention_id,'')),'') IS NOT NULL
                   ORDER BY id DESC LIMIT ?""",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [str(row[0]).strip() for row in rows if str(row[0] or "").strip()]

    def legacy_source_instance_id(self) -> str:
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                "SELECT valor FROM app_metadata WHERE clave='integration.source_instance_id'"
            ).fetchone()
        return str(row[0] if row else "")

    def local_attention_count(self) -> int:
        self.initialize()
        with self.connection() as con:
            row = con.execute("SELECT COUNT(*) FROM atenciones").fetchone()
        return int(row[0] or 0) if row else 0

    def seed_entity_count(self, central_seed_id: str) -> int:
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                "SELECT COUNT(*) FROM sync_seed_entities WHERE central_seed_id=?",
                (str(central_seed_id),),
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def prepare_seed_resume(self, central_seed_id: str) -> None:
        """Requeue locally acknowledged seed events when the central marker is absent."""
        self.initialize()
        with self.connection() as con:
            con.execute(
                """UPDATE sync_outbox
                      SET sync_status='PENDING',sent_at=NULL,last_error=NULL
                    WHERE event_uuid IN (
                        SELECT event_uuid FROM sync_seed_entities
                         WHERE central_seed_id=?
                    )""",
                (str(central_seed_id),),
            )

    def record_seed_state(
        self,
        *,
        central_seed_id: str,
        legacy_source_instance_id: str,
        status: str,
        imported_records: int,
        schema_version: int = 1,
    ) -> None:
        self.initialize()
        with self.connection() as con:
            con.execute(
                """INSERT INTO sync_seed_state(
                       central_seed_id,legacy_source_instance_id,schema_version,
                       status,imported_records,completed_at
                   ) VALUES(?,?,?,?,?,?)
                   ON CONFLICT(central_seed_id) DO UPDATE SET
                     status=excluded.status,imported_records=excluded.imported_records,
                     completed_at=excluded.completed_at""",
                (
                    str(central_seed_id),
                    str(legacy_source_instance_id),
                    int(schema_version),
                    str(status).upper(),
                    int(imported_records),
                    _timestamp() if str(status).upper() == "COMPLETED" else None,
                ),
            )

    def mark_uploaded(self, event_uuid: str) -> None:
        self.mark_uploaded_batch([event_uuid])

    def mark_uploaded_batch(self, event_uuids: list[str]) -> None:
        normalized = [str(value) for value in event_uuids if str(value or "").strip()]
        if not normalized:
            return
        placeholders = ",".join("?" for _value in normalized)
        with self.connection() as con:
            rows = con.execute(
                f"""SELECT event_uuid,entity_type,entity_uuid,operation,
                            base_version,payload_json
                       FROM sync_outbox WHERE event_uuid IN ({placeholders})""",
                normalized,
            ).fetchall()
            con.execute(
                f"""UPDATE sync_outbox
                       SET sync_status='SYNCED',last_error=NULL,sent_at=?
                     WHERE event_uuid IN ({placeholders})""",
                (_timestamp(), *normalized),
            )
            for row in rows:
                if str(row[1]).casefold() != "attention":
                    continue
                event_uuid = str(row[0])
                entity_uuid = str(row[2])
                operation = str(row[3] or "").upper()
                revision = int(row[4] or 0) + 1
                state = "TOMBSTONED" if operation == "DELETE" else "SYNCED"
                con.execute(
                    """UPDATE atenciones SET server_revision=MAX(server_revision,?),
                           base_server_revision=MAX(base_server_revision,?),sync_state=?
                       WHERE global_attention_id=?""",
                    (revision, revision, state, entity_uuid),
                )
                if operation == "DELETE":
                    payload = json.loads(str(row[5] or "{}"))
                    con.execute(
                        """INSERT INTO sync_entity_tombstones(
                               entity_type,entity_uuid,server_revision,delete_event_uuid,
                               deleted_at,deleted_by_user_id,delete_reason,applied_at
                           ) VALUES('attention',?,?,?,?,?,?,?)
                           ON CONFLICT(entity_type,entity_uuid) DO UPDATE SET
                             server_revision=MAX(server_revision,excluded.server_revision),
                             delete_event_uuid=excluded.delete_event_uuid,
                             deleted_at=excluded.deleted_at,
                             deleted_by_user_id=excluded.deleted_by_user_id,
                             delete_reason=excluded.delete_reason,
                             applied_at=excluded.applied_at""",
                        (
                            entity_uuid,
                            revision,
                            str(payload.get("delete_event_uuid") or event_uuid),
                            str(payload.get("deleted_at") or _timestamp()),
                            str(payload.get("deleted_by_user_id") or ""),
                            str(payload.get("delete_reason") or ""),
                            _timestamp(),
                        ),
                    )

    def mark_retry(self, event_uuid: str, error: BaseException) -> None:
        with self.connection() as con:
            con.execute(
                """UPDATE sync_outbox SET sync_status='RETRY',retry_count=retry_count+1,
                   last_error=? WHERE event_uuid=?""",
                (type(error).__name__, str(event_uuid)),
            )

    def record_conflict(self, event: SyncEvent, remote_payload: Mapping[str, Any]) -> None:
        with self.connection() as con:
            con.execute(
                """INSERT OR REPLACE INTO sync_conflicts(
                       event_uuid,entity_type,entity_uuid,local_payload_json,
                       remote_payload_json,detected_at
                   ) VALUES(?,?,?,?,?,?)""",
                (event.event_uuid,event.entity_type,event.entity_uuid,event.payload_json(),
                 json.dumps(dict(remote_payload), ensure_ascii=False, sort_keys=True),_timestamp()),
            )
            con.execute("UPDATE sync_outbox SET sync_status='CONFLICT' WHERE event_uuid=?", (event.event_uuid,))

    def last_cloud_cursor(self) -> int:
        self.initialize()
        with self.connection() as con:
            row = con.execute("SELECT state_value FROM sync_state WHERE state_key='last_cloud_cursor'").fetchone()
        return int(row[0]) if row and str(row[0]).isdigit() else 0

    def set_last_cloud_cursor(self, cursor: int) -> None:
        with self.connection() as con:
            con.execute(
                """INSERT INTO sync_state(state_key,state_value,updated_at) VALUES('last_cloud_cursor',?,?)
                   ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value,
                   updated_at=excluded.updated_at""", (str(max(0, int(cursor))), _timestamp()),
            )

    def already_applied(self, event_uuid: str) -> bool:
        with self.connection() as con:
            return bool(con.execute("SELECT 1 FROM sync_applied_events WHERE event_uuid=?", (event_uuid,)).fetchone())

    def mark_applied(self, event_uuid: str) -> None:
        with self.connection() as con:
            con.execute("INSERT OR IGNORE INTO sync_applied_events(event_uuid,applied_at) VALUES(?,?)", (event_uuid,_timestamp()))

    def mark_applied_and_advance(self, event_uuid: str, cursor: int) -> None:
        """Confirma inbox y cursor en una sola transacción local."""
        with self.connection() as con:
            con.execute(
                "INSERT OR IGNORE INTO sync_applied_events(event_uuid,applied_at) VALUES(?,?)",
                (str(event_uuid), _timestamp()),
            )
            con.execute(
                """INSERT INTO sync_state(state_key,state_value,updated_at)
                   VALUES('last_cloud_cursor',?,?)
                   ON CONFLICT(state_key) DO UPDATE SET
                     state_value=excluded.state_value,updated_at=excluded.updated_at""",
                (str(max(0, int(cursor))), _timestamp()),
            )

    def make_event(
        self, *, entity_type: str, entity_uuid: str, operation: str,
        payload: Mapping[str, Any], session: OperationalSession, device_id: str,
        base_version: int = 0,
    ) -> SyncEvent:
        return SyncEvent(
            event_uuid=str(uuid.uuid4()), entity_type=str(entity_type),
            entity_uuid=str(entity_uuid), operation=str(operation).upper(),
            payload=dict(payload), operational_session_id=session.operational_session_id,
            generation=session.generation, device_id=str(device_id),
            created_at=_timestamp(), base_version=max(0, int(base_version)),
            operational_source_id=session.operational_source_id,
            turn_id=session.turn_id,
        )

    @staticmethod
    def _columns(con: sqlite3.Connection, table: str) -> set[str]:
        return {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}

    @staticmethod
    def _insert_mapping(
        con: sqlite3.Connection, table: str, values: Mapping[str, Any], columns: set[str],
    ) -> int:
        selected = [(key, value) for key, value in values.items() if key in columns]
        if not selected:
            raise SyncConflict(f"La tabla local {table} no admite los datos sincronizados.")
        names = ",".join(key for key, _ in selected)
        placeholders = ",".join("?" for _ in selected)
        cursor = con.execute(
            f"INSERT INTO {table}({names}) VALUES({placeholders})",
            tuple(value for _, value in selected),
        )
        return int(cursor.lastrowid)

    def _insert_remote_attention_or_alias(
        self,
        con: sqlite3.Connection,
        values: Mapping[str, Any],
        columns: set[str],
        entity_uuid: str,
        operational_day_id: int,
        patient_id: int,
    ) -> int:
        """Materializa o registra equivalencia legacy sin duplicar la clínica."""
        legacy_source = str(values.get("legacy_source_instance_id") or "").strip()
        legacy_attention_id = _as_int_or_none(values.get("legacy_attention_id"))
        equivalent = None
        if legacy_source and legacy_attention_id:
            equivalent = con.execute(
                """SELECT id,global_attention_id FROM atenciones
                   WHERE legacy_source_instance_id=? AND legacy_attention_id=?
                   LIMIT 1""",
                (legacy_source, int(legacy_attention_id)),
            ).fetchone()
        if not equivalent and str(values.get("origin_device_id") or "").upper() == "CENTRAL-LEGACY":
            clinical_matches = con.execute(
                """SELECT id,global_attention_id FROM atenciones
                   WHERE REPLACE(LOWER(COALESCE(global_patient_id,'')),'-','')=
                         REPLACE(LOWER(?),'-','')
                     AND COALESCE(operational_turn_id,turno_id)=?
                     AND UPPER(TRIM(COALESCE(nombre,'')))=UPPER(TRIM(?))
                     AND COALESCE(is_deleted,0)=0
                   ORDER BY id
                   LIMIT 2""",
                (
                    str(values.get("global_patient_id") or ""),
                    _as_int_or_none(values.get("operational_turn_id"))
                    or _as_int_or_none(values.get("turno_id"))
                    or 0,
                    str(values.get("nombre") or ""),
                ),
            ).fetchall()
            if len(clinical_matches) == 1:
                equivalent = clinical_matches[0]
        if equivalent:
            local_attention_id = int(equivalent[0])
            existing_global = str(equivalent[1] or "").strip()
            if existing_global and existing_global != str(entity_uuid):
                con.execute(
                    """INSERT INTO sync_attention_aliases(
                           remote_global_attention_id,local_attention_id,reason,mapped_at
                       ) VALUES(?,?,?,?)
                       ON CONFLICT(remote_global_attention_id) DO UPDATE SET
                         local_attention_id=excluded.local_attention_id,
                         reason=excluded.reason,mapped_at=excluded.mapped_at""",
                    (
                        str(entity_uuid),
                        local_attention_id,
                        "LEGACY_CLINICAL_IDENTITY_COLLISION",
                        _timestamp(),
                    ),
                )
                return local_attention_id
            con.execute(
                "UPDATE atenciones SET global_attention_id=? WHERE id=?",
                (str(entity_uuid), local_attention_id),
            )
            con.execute(
                """INSERT INTO sync_attention_aliases(
                       remote_global_attention_id,local_attention_id,reason,mapped_at
                   ) VALUES(?,?,?,?)
                   ON CONFLICT(remote_global_attention_id) DO UPDATE SET
                     local_attention_id=excluded.local_attention_id,
                     reason=excluded.reason,mapped_at=excluded.mapped_at""",
                (
                    str(entity_uuid),
                    local_attention_id,
                    "EXACT_LEGACY_IDENTITY",
                    _timestamp(),
                ),
            )
            return local_attention_id
        try:
            return self._insert_mapping(con, "atenciones", values, columns)
        except sqlite3.IntegrityError as exc:
            raise SyncConflict(
                "No se pudo materializar la identidad global sin colisionar "
                "con otra atención local."
            ) from exc

    def apply_remote_event(self, event: Mapping[str, Any]) -> bool:
        """Materializa una atención remota en la SQLite local sin eco de outbox.

        La identidad global manda. El ID entero de la estación de origen no
        se reutiliza, por lo que dos PCs pueden tener secuencias SQLite iguales.
        """
        entity_type = str(event.get("entity_type") or "").casefold()
        if entity_type != "attention":
            return False
        payload_value = event.get("payload_json") or event.get("payload") or {}
        if isinstance(payload_value, str):
            payload = json.loads(payload_value or "{}")
        else:
            payload = dict(payload_value or {})
        entity_uuid = str(
            event.get("entity_uuid") or payload.get("global_attention_id") or ""
        ).strip()
        if not entity_uuid:
            raise SyncConflict("El evento remoto no incluye global_attention_id.")
        origin_device = str(
            event.get("origin_device_id") or payload.get("origin_device_id") or ""
        ).strip()
        operation = str(event.get("operation") or "UPDATE").upper()
        remote_version = max(
            1,
            int(
                event.get("resulting_version")
                or payload.get("version")
                or 1
            ),
        )
        created_at_device = str(
            event.get("created_at_device")
            or payload.get("created_at_device")
            or event.get("created_at")
            or _timestamp()
        )
        created_at_effective = str(
            event.get("created_at_effective_utc")
            or payload.get("created_at_effective_utc")
            or created_at_device
        )
        local_sequence = int(
            event.get("device_local_sequence")
            or payload.get("device_local_sequence")
            or 0
        )
        reconciliation_status = str(
            event.get("reconciliation_status")
            or payload.get("reconciliation_status")
            or "DIRECT"
        ).upper()

        self.initialize()
        with self.connection() as con:
            con.execute(
                "SELECT device_id FROM sync_runtime_context WHERE singleton=1"
            ).fetchone()
            attention_columns = self._columns(con, "atenciones")
            patient_columns = self._columns(con, "pacientes")
            if not attention_columns or not patient_columns:
                raise SyncConflict("La base local de Admisión no está inicializada.")
            existing = con.execute(
                """SELECT id,version,server_revision,sync_state,is_deleted FROM atenciones
                   WHERE REPLACE(LOWER(global_attention_id),'-','')=
                         REPLACE(LOWER(?),'-','')
                      OR id=(
                          SELECT local_attention_id FROM sync_attention_aliases
                          WHERE REPLACE(LOWER(remote_global_attention_id),'-','')=
                                REPLACE(LOWER(?),'-','')
                      )
                   LIMIT 1""",
                (entity_uuid, entity_uuid),
            ).fetchone()
            tombstone = con.execute(
                """SELECT server_revision FROM sync_entity_tombstones
                   WHERE entity_type='attention' AND entity_uuid=?""",
                (entity_uuid,),
            ).fetchone()
            is_delete = operation in {"DELETE", "CANCEL", "ATTENTION_DELETED"} or bool(
                payload.get("is_deleted")
            )
            is_restore = operation == "RESTORE_ATTENTION"
            if tombstone and not is_delete and not is_restore:
                con.execute(
                    """UPDATE sync_outbox SET sync_status='SUPERSEDED',
                           last_error='STALE_RECORD_SUPPRESSED_BY_TOMBSTONE'
                       WHERE entity_uuid=? AND sync_status IN ('PENDING','RETRY')""",
                    (entity_uuid,),
                )
                return False

            con.execute(
                "INSERT OR REPLACE INTO sync_apply_context(singleton,event_uuid) VALUES(1,?)",
                (str(event.get("event_uuid") or entity_uuid),),
            )
            try:
                pending_local = con.execute(
                    """SELECT event_uuid,payload_json,operation,base_version FROM sync_outbox
                       WHERE entity_uuid=? AND sync_status IN ('PENDING','RETRY')
                       ORDER BY device_local_sequence DESC LIMIT 1""",
                    (entity_uuid,),
                ).fetchone()
                if pending_local and str(pending_local[2] or "").upper() == "DELETE" and not is_delete:
                    con.execute(
                        """INSERT OR IGNORE INTO sync_conflicts(
                               event_uuid,entity_type,entity_uuid,local_payload_json,
                               remote_payload_json,detected_at
                           ) VALUES(?,?,?,?,?,?)""",
                        (
                            str(event.get("event_uuid") or entity_uuid),
                            "attention",
                            entity_uuid,
                            str(pending_local[1] or "{}"),
                            json.dumps(payload, ensure_ascii=False, sort_keys=True),
                            _timestamp(),
                        ),
                    )
                    return False
                if is_delete:
                    con.execute(
                        """INSERT INTO sync_entity_tombstones(
                               entity_type,entity_uuid,server_revision,delete_event_uuid,
                               deleted_at,deleted_by_user_id,delete_reason,applied_at
                           ) VALUES('attention',?,?,?,?,?,?,?)
                           ON CONFLICT(entity_type,entity_uuid) DO UPDATE SET
                             server_revision=MAX(server_revision,excluded.server_revision),
                             delete_event_uuid=excluded.delete_event_uuid,
                             deleted_at=excluded.deleted_at,
                             deleted_by_user_id=excluded.deleted_by_user_id,
                             delete_reason=excluded.delete_reason,
                             applied_at=excluded.applied_at""",
                        (
                            entity_uuid,
                            remote_version,
                            str(payload.get("delete_event_uuid") or event.get("event_uuid") or entity_uuid),
                            str(payload.get("deleted_at") or event.get("created_at") or _timestamp()),
                            str(payload.get("deleted_by_user_id") or event.get("origin_user_id") or ""),
                            str(payload.get("delete_reason") or ""),
                            _timestamp(),
                        ),
                    )
                    con.execute(
                        """UPDATE sync_outbox SET sync_status='SUPERSEDED',
                               last_error='SYNC_TOMBSTONE_WON'
                           WHERE entity_uuid=? AND operation<>'DELETE'
                             AND sync_status IN ('PENDING','RETRY')""",
                        (entity_uuid,),
                    )
                    if existing:
                        con.execute(
                            """UPDATE atenciones SET estado='ANULADA',is_deleted=1,
                                   deleted_at=?,deleted_by_user_id=?,delete_event_uuid=?,
                                   delete_reason=?,server_revision=MAX(server_revision,?),
                                   base_server_revision=MAX(base_server_revision,?),
                                   sync_state='TOMBSTONED',version=MAX(version,?)
                               WHERE id=?""",
                            (
                                str(payload.get("deleted_at") or event.get("created_at") or _timestamp()),
                                str(payload.get("deleted_by_user_id") or event.get("origin_user_id") or ""),
                                str(payload.get("delete_event_uuid") or event.get("event_uuid") or entity_uuid),
                                str(payload.get("delete_reason") or ""),
                                remote_version,
                                remote_version,
                                remote_version,
                                int(existing[0]),
                            ),
                        )
                    return True
                local_server_revision = int(existing[2] or 0) if existing else 0
                if existing and local_server_revision > remote_version:
                    return False
                if existing and pending_local and remote_version > int(pending_local[3] or 0):
                    con.execute(
                        """INSERT OR IGNORE INTO sync_conflicts(
                               event_uuid,entity_type,entity_uuid,local_payload_json,
                               remote_payload_json,detected_at,resolved_at,resolution
                           ) VALUES(?,?,?,?,?,?,?,?)""",
                        (
                            str(event.get("event_uuid") or entity_uuid),
                            "attention",
                            entity_uuid,
                            str(pending_local[1] or "{}"),
                            json.dumps(payload, ensure_ascii=False, sort_keys=True),
                            _timestamp(),
                            _timestamp(),
                            "CLOUD_NEWER_WON",
                        ),
                    )
                    con.execute(
                        """UPDATE sync_outbox SET sync_status='CONFLICT',
                               last_error='SYNC_STALE_UPDATE_REJECTED'
                           WHERE event_uuid=?""",
                        (str(pending_local[0]),),
                    )

                patient_uuid = str(payload.get("global_patient_id") or "").strip()
                if not patient_uuid:
                    patient_uuid = str(
                        uuid.uuid5(uuid.NAMESPACE_URL, f"hospital-patient:{entity_uuid}")
                    )
                patient = None
                if patient_uuid and "global_patient_id" in patient_columns:
                    patient = con.execute(
                        """SELECT id FROM pacientes
                           WHERE REPLACE(LOWER(global_patient_id),'-','')=
                                 REPLACE(LOWER(?),'-','') LIMIT 1""",
                        (patient_uuid,),
                    ).fetchone()
                if patient:
                    patient_id = int(patient[0])
                elif existing:
                    patient_id = int(
                        con.execute(
                            "SELECT paciente_id FROM atenciones WHERE id=?", (int(existing[0]),)
                        ).fetchone()[0]
                    )
                else:
                    patient_id = self._insert_mapping(
                        con,
                        "pacientes",
                        {
                            "nombre": str(payload.get("name") or "SIN NOMBRE"),
                            "sexo": str(payload.get("sex") or ""),
                            "edad_num": _as_int_or_none(payload.get("age")),
                            "unidad": str(payload.get("age_unit") or "Años"),
                            "cedula": str(payload.get("cedula") or ""),
                            "telefono": str(payload.get("phone") or ""),
                            "direccion": str(payload.get("address") or ""),
                            "nacionalidad": str(payload.get("nationality") or ""),
                            "ars": str(payload.get("ars") or ""),
                            "nss": str(payload.get("nss") or ""),
                            "global_patient_id": patient_uuid,
                            "version": remote_version,
                            "server_revision": remote_version,
                            "sync_state": "SYNCED",
                            "origin_device_id": origin_device,
                        },
                        patient_columns,
                    )

                if existing:
                    updates = {
                        "nombre": str(payload.get("name") or "SIN NOMBRE"),
                        "sexo": str(payload.get("sex") or ""),
                        "edad_num": _as_int_or_none(payload.get("age")),
                        "unidad": str(payload.get("age_unit") or "Años"),
                        "cedula": str(payload.get("cedula") or ""),
                        "telefono": str(payload.get("phone") or ""),
                        "direccion": str(payload.get("address") or ""),
                        "nacionalidad": str(payload.get("nationality") or ""),
                        "ars": str(payload.get("ars") or ""),
                        "nss": str(payload.get("nss") or ""),
                        "hoja": str(payload.get("detail_sheet") or ""),
                        "fecha": str(payload.get("service_date") or ""),
                        "hora": str(payload.get("service_time") or ""),
                        "tipo_atencion": str(payload.get("service_type") or "EMERGENCIA"),
                        "estado": "ANULADA" if operation == "CANCEL" else str(
                            payload.get("source_status") or "ACTIVA"
                        ).upper(),
                        "global_patient_id": patient_uuid,
                        "version": remote_version,
                        "server_revision": remote_version,
                        "base_server_revision": remote_version,
                        "sync_state": "SYNCED",
                        "is_deleted": 0,
                        "deleted_at": None,
                        "deleted_by_user_id": None,
                        "delete_event_uuid": None,
                        "delete_reason": None,
                        "origin_device_id": origin_device,
                        "operational_source_id": str(
                            payload.get("operational_source_id") or ""
                        ),
                        "operational_session_id": str(
                            event.get("operational_session_id") or ""
                        ),
                        "generation": _as_int_or_none(event.get("generation")),
                        "operational_turn_id": _as_int_or_none(
                            event.get("turn_id") or payload.get("turn_id")
                        ),
                        "created_at_device": created_at_device,
                        "created_at_effective_utc": created_at_effective,
                        "device_local_sequence": local_sequence,
                        "captured_by_user_id": str(
                            event.get("origin_user_id")
                            or payload.get("origin_user_id")
                            or ""
                        ),
                        "captured_by_username": str(
                            event.get("origin_username")
                            or payload.get("admission_username")
                            or ""
                        ),
                        "reconciliation_status": reconciliation_status,
                    }
                    selected = [
                        (key, value) for key, value in updates.items() if key in attention_columns
                    ]
                    con.execute(
                        "UPDATE atenciones SET "
                        + ",".join(f"{key}=?" for key, _ in selected)
                        + " WHERE id=?",
                        tuple(value for _, value in selected) + (int(existing[0]),),
                    )
                else:
                    turn = con.execute(
                        """SELECT id,dia_operativo_id FROM turnos
                           WHERE UPPER(COALESCE(estado,''))='ABIERTO'
                           ORDER BY COALESCE(fecha_inicio_real,fecha_inicio,'') DESC,id DESC
                           LIMIT 1"""
                    ).fetchone()
                    if not turn:
                        raise SyncConflict(
                            "No existe un turno local abierto para materializar la atención remota."
                        )
                    self._insert_remote_attention_or_alias(
                        con,
                        {
                            "paciente_id": patient_id,
                            "dia_operativo_id": int(turn[1]),
                            "turno_id": int(turn[0]),
                            "nss": str(payload.get("nss") or ""),
                            "nombre": str(payload.get("name") or "SIN NOMBRE"),
                            "sexo": str(payload.get("sex") or ""),
                            "edad_num": _as_int_or_none(payload.get("age")),
                            "unidad": str(payload.get("age_unit") or "Años"),
                            "cedula": str(payload.get("cedula") or ""),
                            "telefono": str(payload.get("phone") or ""),
                            "direccion": str(payload.get("address") or ""),
                            "nacionalidad": str(payload.get("nationality") or ""),
                            "ars": str(payload.get("ars") or ""),
                            "hoja": str(payload.get("detail_sheet") or ""),
                            "fecha": str(payload.get("service_date") or ""),
                            "hora": str(payload.get("service_time") or ""),
                            "tipo_atencion": str(payload.get("service_type") or "EMERGENCIA"),
                            "estado": "ANULADA" if operation == "CANCEL" else "ACTIVA",
                            "global_attention_id": entity_uuid,
                            "global_patient_id": patient_uuid,
                            "version": remote_version,
                            "server_revision": remote_version,
                            "base_server_revision": remote_version,
                            "sync_state": "SYNCED",
                            "is_deleted": 0,
                            "origin_device_id": origin_device,
                            "operational_source_id": str(
                                payload.get("operational_source_id") or ""
                            ),
                            "operational_session_id": str(
                                event.get("operational_session_id") or ""
                            ),
                            "generation": _as_int_or_none(event.get("generation")),
                            "operational_turn_id": _as_int_or_none(
                                event.get("turn_id") or payload.get("turn_id")
                            ),
                            "created_at_device": created_at_device,
                            "created_at_effective_utc": created_at_effective,
                            "device_local_sequence": local_sequence,
                            "captured_by_user_id": str(
                                event.get("origin_user_id")
                                or payload.get("origin_user_id")
                                or ""
                            ),
                            "captured_by_username": str(
                                event.get("origin_username")
                                or payload.get("admission_username")
                                or ""
                            ),
                            "reconciliation_status": reconciliation_status,
                            "legacy_source_instance_id": str(
                                payload.get("legacy_source_instance_id")
                                or payload.get("source_instance_id")
                                or ""
                            ),
                            "legacy_attention_id": _as_int_or_none(
                                payload.get("legacy_attention_id")
                                or payload.get("attention_id")
                            ),
                            "legacy_patient_id": _as_int_or_none(
                                payload.get("legacy_patient_id")
                                or payload.get("patient_id")
                            ),
                        },
                        attention_columns,
                        entity_uuid,
                        int(turn[1]),
                        patient_id,
                    )
                if is_restore:
                    con.execute(
                        """DELETE FROM sync_entity_tombstones
                           WHERE entity_type='attention' AND entity_uuid=?""",
                        (entity_uuid,),
                    )
            finally:
                con.execute("DELETE FROM sync_apply_context WHERE singleton=1")
        return True

    @staticmethod
    def _event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
        payload_value = event.get("payload_json") or event.get("payload") or {}
        if isinstance(payload_value, str):
            return dict(json.loads(payload_value or "{}") or {})
        return dict(payload_value or {})

    def _remote_event_is_materialized(
        self, con: sqlite3.Connection, event: Mapping[str, Any]
    ) -> bool:
        """Whether SQLite contains the durable state promised by one event."""
        if str(event.get("entity_type") or "").casefold() != "attention":
            return True
        try:
            payload = self._event_payload(event)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        global_attention_id = str(
            event.get("entity_uuid") or payload.get("global_attention_id") or ""
        ).strip()
        if not global_attention_id:
            return False
        operation = str(event.get("operation") or "UPDATE").upper()
        is_deleted = operation in {"DELETE", "CANCEL", "ATTENTION_DELETED"} or bool(
            payload.get("is_deleted")
        )
        if is_deleted:
            return bool(
                con.execute(
                    """SELECT 1 FROM sync_entity_tombstones
                       WHERE entity_type='attention' AND entity_uuid=?
                       LIMIT 1""",
                    (global_attention_id,),
                ).fetchone()
            )
        return bool(
            con.execute(
                """SELECT 1 FROM atenciones a
                   LEFT JOIN sync_attention_aliases alias
                     ON alias.local_attention_id=a.id
                   WHERE REPLACE(LOWER(a.global_attention_id),'-','')=
                         REPLACE(LOWER(?),'-','')
                      OR REPLACE(LOWER(alias.remote_global_attention_id),'-','')=
                         REPLACE(LOWER(?),'-','')
                   LIMIT 1""",
                (global_attention_id, global_attention_id),
            ).fetchone()
        )

    def is_remote_event_materialized(self, event: Mapping[str, Any]) -> bool:
        """Public verification used by alternate incremental sync consumers."""
        self.initialize()
        with self.connection() as con:
            return self._remote_event_is_materialized(con, event)

    def discard_applied_event(self, event_uuid: str) -> None:
        """Drops a stale local acknowledgement so the event can be recovered."""
        if not event_uuid:
            return
        self.initialize()
        with self.connection() as con:
            con.execute("DELETE FROM sync_applied_events WHERE event_uuid=?", (event_uuid,))

    def _apply_remote_events_batch(
        self,
        events: Iterable[Mapping[str, Any]],
        *,
        advance_cursor: bool,
    ) -> int:
        """Apply a bounded cloud batch so sync cannot monopolize the writer."""
        applied = 0
        with self.connection() as con:
            batch_store = OfflineAdmissionStore(con)
            batch_store._initialized = True
            for event in events:
                event_uuid = str(event.get("event_uuid") or "")
                sequence = int(event.get("sequence") or 0)
                if (
                    str(event.get("entity_type") or "").casefold() == "attention"
                    and not event_uuid
                ):
                    OPERATIONAL_LOG.warning(
                        "ADMISSION_SYNC_EVENT_APPLY_FAILED sequence=%s reason=MISSING_EVENT_UUID",
                        sequence,
                    )
                    break
                already = bool(
                    event_uuid
                    and con.execute(
                        "SELECT 1 FROM sync_applied_events WHERE event_uuid=?",
                        (event_uuid,),
                    ).fetchone()
                )
                if already and not batch_store._remote_event_is_materialized(con, event):
                    # A legacy acknowledgement without SQLite state must never
                    # suppress recovery from the authoritative event stream.
                    con.execute(
                        "DELETE FROM sync_applied_events WHERE event_uuid=?",
                        (event_uuid,),
                    )
                    already = False
                    OPERATIONAL_LOG.warning(
                        "ADMISSION_SYNC_STALE_ACK_REPAIRED event_uuid=%s sequence=%s",
                        event_uuid,
                        sequence,
                    )
                if event_uuid and not already:
                    try:
                        batch_store.apply_remote_event(event)
                    except SyncConflict as exc:
                        payload_value = event.get("payload_json") or {}
                        payload_json = (
                            payload_value
                            if isinstance(payload_value, str)
                            else json.dumps(
                                dict(payload_value),
                                ensure_ascii=False,
                                sort_keys=True,
                            )
                        )
                        con.execute(
                            """INSERT OR IGNORE INTO sync_conflicts(
                                   event_uuid,entity_type,entity_uuid,
                                   local_payload_json,remote_payload_json,detected_at
                               ) VALUES(?,?,?,?,?,?)""",
                            (
                                event_uuid,
                                str(event.get("entity_type") or "attention"),
                                str(event.get("entity_uuid") or ""),
                                json.dumps(
                                    {"reason_code": type(exc).__name__},
                                    ensure_ascii=False,
                                ),
                                payload_json,
                                _timestamp(),
                            ),
                        )
                        # Cursor advancement must be contiguous: skipping this
                        # event would lose it and potentially its dependants.
                        OPERATIONAL_LOG.warning(
                            "ADMISSION_SYNC_EVENT_APPLY_FAILED event_uuid=%s sequence=%s reason=%s",
                            event_uuid,
                            sequence,
                            type(exc).__name__,
                        )
                        break
                    if not batch_store._remote_event_is_materialized(con, event):
                        OPERATIONAL_LOG.warning(
                            "ADMISSION_SYNC_EVENT_APPLY_FAILED event_uuid=%s sequence=%s reason=NOT_MATERIALIZED",
                            event_uuid,
                            sequence,
                        )
                        break
                    con.execute(
                        """INSERT OR IGNORE INTO sync_applied_events(event_uuid,applied_at)
                           VALUES(?,?)""",
                        (event_uuid, _timestamp()),
                    )
                    applied += 1
                    OPERATIONAL_LOG.info(
                        "ADMISSION_SYNC_EVENT_APPLIED event_uuid=%s sequence=%s",
                        event_uuid,
                        sequence,
                    )
                if advance_cursor:
                    con.execute(
                        """INSERT INTO sync_state(state_key,state_value,updated_at)
                           VALUES('last_cloud_cursor',?,?)
                           ON CONFLICT(state_key) DO UPDATE SET
                             state_value=excluded.state_value,
                             updated_at=excluded.updated_at""",
                        (str(sequence), _timestamp()),
                    )
                    OPERATIONAL_LOG.info(
                        "ADMISSION_SYNC_CURSOR_ADVANCED sequence=%s", sequence
                    )
        return applied

    def apply_remote_events(self, events: list[Mapping[str, Any]]) -> int:
        """Apply cloud events in short SQLite transactions with durable cursors."""
        self.initialize()
        applied = 0
        for start in range(0, len(events), LOCAL_SYNC_APPLY_BATCH_SIZE):
            batch = events[start:start + LOCAL_SYNC_APPLY_BATCH_SIZE]
            applied += self._apply_remote_events_batch(
                batch,
                advance_cursor=True,
            )
            if batch and self.last_cloud_cursor() < int(batch[-1].get("sequence") or 0):
                # A recoverable materialization error stopped this contiguous
                # batch.  Never jump to a later batch and lose the failed event.
                break
        return applied

    def hydrate_remote_events(self, events: Iterable[Mapping[str, Any]]) -> int:
        """Hydrates read-through snapshots without moving the incremental cursor."""
        self.initialize()
        values = list(events)
        return sum(
            self._apply_remote_events_batch(
                values[start:start + LOCAL_SYNC_APPLY_BATCH_SIZE],
                advance_cursor=False,
            )
            for start in range(0, len(values), LOCAL_SYNC_APPLY_BATCH_SIZE)
        )

    def local_attention_ids(
        self, global_attention_ids: Iterable[str]
    ) -> dict[str, int]:
        normalized = []
        for value in global_attention_ids:
            try:
                normalized.append(str(uuid.UUID(str(value))).replace("-", "").lower())
            except (ValueError, TypeError, AttributeError):
                continue
        if not normalized:
            return {}
        placeholders = ",".join("?" for _ in normalized)
        self.initialize()
        with self.connection() as con:
            rows = con.execute(
                f"""SELECT a.id,a.global_attention_id,alias.remote_global_attention_id
                       FROM atenciones a
                       LEFT JOIN sync_attention_aliases alias
                         ON alias.local_attention_id=a.id
                      WHERE REPLACE(LOWER(a.global_attention_id),'-','') IN ({placeholders})
                         OR REPLACE(LOWER(alias.remote_global_attention_id),'-','')
                            IN ({placeholders})""",
                tuple(normalized) + tuple(normalized),
            ).fetchall()
        result: dict[str, int] = {}
        for row in rows:
            for candidate in (row[1], row[2]):
                if candidate:
                    result[str(candidate).replace("-", "").lower()] = int(row[0])
        return result

    def get_attention_by_global_id(
        self, global_attention_id: str, *, include_deleted: bool = True
    ) -> dict[str, Any] | None:
        try:
            normalized = str(uuid.UUID(str(global_attention_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("global_attention_id no es válido.") from exc
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                """SELECT a.* FROM atenciones a
                   WHERE (
                       REPLACE(LOWER(a.global_attention_id),'-','')=
                       REPLACE(LOWER(?),'-','')
                       OR a.id=(
                           SELECT local_attention_id FROM sync_attention_aliases
                           WHERE REPLACE(LOWER(remote_global_attention_id),'-','')=
                                 REPLACE(LOWER(?),'-','')
                       )
                   ) AND (? OR COALESCE(a.is_deleted,0)=0)
                   LIMIT 1""",
                (normalized, normalized, bool(include_deleted)),
            ).fetchone()
        return dict(row) if row else None

    def cancel_attention_local(
        self,
        global_attention_id: str,
        *,
        current_user: Mapping[str, Any] | Any,
        reason: str,
    ) -> dict[str, Any] | None:
        """Creates a local tombstone and DELETE outbox in one SQLite transaction."""
        reason_text = str(reason or "").strip()
        if len(reason_text) < 5:
            raise ValueError("La anulación requiere un motivo de al menos 5 caracteres.")
        user_id = canonical_user_id(current_user)
        username = canonical_username(current_user) or user_id or "sistema"
        try:
            normalized = str(uuid.UUID(str(global_attention_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("global_attention_id no es válido.") from exc
        self.initialize()
        with self.connection() as con:
            row = con.execute(
                """SELECT id,is_deleted FROM atenciones
                   WHERE REPLACE(LOWER(global_attention_id),'-','')=
                         REPLACE(LOWER(?),'-','')
                      OR id=(SELECT local_attention_id FROM sync_attention_aliases
                              WHERE REPLACE(LOWER(remote_global_attention_id),'-','')=
                                    REPLACE(LOWER(?),'-',''))
                   LIMIT 1""",
                (normalized, normalized),
            ).fetchone()
            if not row:
                return None
            if bool(row[1]):
                current = con.execute(
                    "SELECT * FROM atenciones WHERE id=?", (int(row[0]),)
                ).fetchone()
                return dict(current) if current else None
            con.execute(
                """UPDATE atenciones SET estado='ANULADA',
                          anulada_at=?,anulada_por=?,anulada_motivo=?,
                          deleted_at=?,deleted_by_user_id=?,delete_reason=?
                    WHERE id=? AND COALESCE(is_deleted,0)=0""",
                (
                    _timestamp(), username, reason_text, _timestamp(),
                    user_id or username, reason_text, int(row[0]),
                ),
            )
            current = con.execute(
                "SELECT * FROM atenciones WHERE id=?", (int(row[0]),)
            ).fetchone()
        return dict(current) if current else None


class OperationalSessionService:
    """Autoridad central de PRIMARY/SECONDARY; no reutiliza active_sessions."""

    def __init__(self, connection_factory: Callable[[], Any], *, max_devices: int = MAX_ACTIVE_SESSION_DEVICES):
        self.connection_factory = connection_factory
        self.max_devices = max(1, int(max_devices))

    def ensure_schema(self) -> None:
        with self.connection_factory() as con:
            install_central_hybrid_schema(con)

    @staticmethod
    def _operational_source_id(con: Any) -> str:
        row = con.execute(
            "SELECT operational_source_id FROM admission_operational_identity "
            "WHERE singleton=1 FOR UPDATE"
        ).fetchone()
        if row:
            return str(row[0])
        source_id = str(uuid.uuid4())
        con.execute(
            """INSERT INTO admission_operational_identity(
                   singleton,operational_source_id,created_at
               ) VALUES(1,%s,NOW()) ON CONFLICT(singleton) DO NOTHING""",
            (source_id,),
        )
        row = con.execute(
            "SELECT operational_source_id FROM admission_operational_identity WHERE singleton=1"
        ).fetchone()
        return str(row[0] if row else source_id)

    @staticmethod
    def _audit(
        con: Any,
        *,
        session_id: str,
        event_type: str,
        device_id: str = "",
        username: str = "",
        generation: int | None = None,
        details: Mapping[str, Any] | None = None,
        transition_id: str | None = None,
    ) -> None:
        con.execute(
            """INSERT INTO admission_operational_audit(
                   operational_session_id,event_type,device_id,username,generation,
                   details_json,transition_id
               ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s)""",
            (
                str(session_id), str(event_type), str(device_id or ""),
                str(username or ""), _as_int_or_none(generation),
                json.dumps(dict(details or {}), ensure_ascii=False, sort_keys=True),
                str(transition_id) if transition_id else None,
            ),
        )

    @staticmethod
    def _allocate_next_central_turn_id(con: Any) -> int:
        """Allocates a central-only turn identity under the operational lock."""
        row = con.execute(
            """SELECT GREATEST(
                    COALESCE((SELECT MAX(turn_id) FROM admission_operational_sessions), 0),
                    COALESCE((SELECT MAX(turn_id) FROM admission_operational_turn_intervals), 0),
                    COALESCE((SELECT MAX(turn_id) FROM admission_attention_projection), 0),
                    COALESCE((SELECT MAX(turn_id) FROM admission_sync_events), 0)
                ) + 1 AS next_turn_id"""
        ).fetchone()
        next_turn_id = _as_int_or_none(_mapping(row).get("next_turn_id"))
        if next_turn_id is None or next_turn_id <= 0:
            raise AdmissionWriteBlocked(
                "No fue posible reservar una identidad central para el nuevo turno."
            )
        return next_turn_id

    @staticmethod
    def _row_to_session(row: Any) -> OperationalSession | None:
        return OperationalSession.from_mapping(_mapping(row)) if row else None

    @staticmethod
    def _active_sessions_available(con: Any) -> bool:
        row = con.execute("SELECT to_regclass('public.active_sessions')").fetchone()
        return bool(row and row[0])

    def get_operational_session(self, *, for_update: bool = False) -> OperationalSession | None:
        suffix = " FOR UPDATE" if for_update else ""
        with self.connection_factory() as con:
            row = con.execute(
                "SELECT * FROM admission_operational_sessions WHERE status='ACTIVE' "
                "ORDER BY updated_at DESC LIMIT 1" + suffix
            ).fetchone()
        return self._row_to_session(row)

    def repair_ambiguous_current_turn_identity(
        self,
        *,
        operational_session_id: str,
        primary_device_id: str,
    ) -> OperationalSession | None:
        """Repairs only a newly-opened turn that reused a historical id.

        Historical attention rows are deliberately never reassigned.  A repair
        is valid only when rows of the current source/turn predate the current
        interval, which proves that the numeric turn id collided with history.
        """
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            row = con.execute(
                """SELECT * FROM admission_operational_sessions
                    WHERE operational_session_id=%s FOR UPDATE""",
                (operational_session_id,),
            ).fetchone()
            current = self._row_to_session(row)
            if (
                current is None
                or current.status != "ACTIVE"
                or current.primary_device_id != str(primary_device_id)
                or current.turn_id is None
                or not current.operational_source_id
                or current.turn_started_at is None
            ):
                return current
            collision = con.execute(
                """SELECT COUNT(*) AS count
                     FROM admission_attention_projection
                    WHERE operational_source_id::TEXT=%s
                      AND turn_id=%s
                      AND is_deleted=FALSE
                      AND created_at_effective_utc < %s""",
                (
                    str(current.operational_source_id),
                    int(current.turn_id),
                    current.turn_started_at,
                ),
            ).fetchone()
            collision_count = int(_mapping(collision).get("count") or 0)
            if collision_count == 0:
                return current
            repaired_turn_id = self._allocate_next_central_turn_id(con)
            con.execute(
                """UPDATE admission_operational_sessions
                       SET turn_id=%s,operational_revision=operational_revision+1,
                           updated_at=NOW(),change_reason=%s
                     WHERE operational_session_id=%s""",
                (
                    repaired_turn_id,
                    "Reparación de identidad única del turno actual",
                    operational_session_id,
                ),
            )
            con.execute(
                """UPDATE admission_operational_turn_intervals
                       SET turn_id=%s
                     WHERE operational_session_id=%s AND generation=%s AND ended_at IS NULL""",
                (repaired_turn_id, operational_session_id, current.generation),
            )
            self._audit(
                con,
                session_id=operational_session_id,
                event_type="TURN_IDENTITY_REPAIRED",
                device_id=primary_device_id,
                username=current.active_username,
                generation=current.generation,
                details={
                    "old_turn_id": current.turn_id,
                    "new_turn_id": repaired_turn_id,
                    "historical_collision_count": collision_count,
                    "historical_rows_reassigned": 0,
                },
            )
            repaired_row = con.execute(
                """SELECT * FROM admission_operational_sessions
                    WHERE operational_session_id=%s""",
                (operational_session_id,),
            ).fetchone()
            return self._row_to_session(repaired_row)

    def backfill_missing_turn_code(
        self,
        *,
        operational_session_id: str,
        primary_device_id: str,
        primary_login_session_id: str,
        expected_generation: int,
        turn_code: str,
        changed_by: str = "",
    ) -> OperationalSession | None:
        """Completa una sesión heredada sin código de turno.

        El código nominal es indispensable para que una SQLite nueva pueda
        crear su espejo V15.  Esta reparación está restringida al PRIMARY y
        únicamente rellena un campo central vacío; no es una transición de
        turno ni cambia representante, horario o generación.
        """
        normalized_code = str(turn_code or "").strip().upper()
        if normalized_code not in {"8AM_8AM", "8AM_8PM", "8PM_8AM"}:
            raise ValueError("Código de turno canónico inválido para el espejo central.")
        session_id = str(operational_session_id or "").strip()
        device_id = str(primary_device_id or "").strip()
        login_session_id = str(primary_login_session_id or "").strip()
        if not session_id or not device_id or not login_session_id:
            raise ValueError(
                "Se requieren sesión, dispositivo y login PRIMARY para completar el turno."
            )

        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            row = con.execute(
                """SELECT * FROM admission_operational_sessions
                     WHERE operational_session_id=%s AND status='ACTIVE'
                     FOR UPDATE""",
                (session_id,),
            ).fetchone()
            current = self._row_to_session(row)
            if current is None:
                return None
            if current.primary_device_id != device_id:
                return current
            if current.primary_login_session_id != login_session_id:
                return current
            if current.generation != int(expected_generation):
                return current
            attachment = con.execute(
                """SELECT login_session_id FROM admission_operational_devices
                     WHERE operational_session_id=%s AND device_id=%s
                       AND detached_at IS NULL
                     FOR UPDATE""",
                (session_id, device_id),
            ).fetchone()
            if not attachment or str(attachment[0] or "") != login_session_id:
                return current
            if str(current.turn_code or "").strip():
                return current
            con.execute(
                """UPDATE admission_operational_sessions
                      SET turn_code=%s,operational_revision=operational_revision+1,
                          updated_at=NOW(),change_reason='TURN_CODE_LEGACY_BACKFILL',
                          changed_by=%s
                    WHERE operational_session_id=%s AND turn_code=''""",
                (normalized_code, str(changed_by or current.active_username), session_id),
            )
            updated_row = con.execute(
                "SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s",
                (session_id,),
            ).fetchone()
            updated = self._row_to_session(updated_row) or current
            if updated.turn_code:
                self._audit(
                    con,
                    session_id=session_id,
                    event_type="TURN_CODE_LEGACY_BACKFILLED",
                    device_id=device_id,
                    username=str(changed_by or current.active_username),
                    generation=updated.generation,
                    details={"turn_code": updated.turn_code},
                )
            return updated

    def _primary_attachment_is_active(
        self, con: Any, session: OperationalSession, *, stale_after_seconds: int = 120
    ) -> bool:
        """Return whether the central primary attachment still has authority.

        The device attachment is authoritative for Admission.  The application
        login table is consulted only to release a login that was explicitly
        closed; this keeps a historical machine record from blocking a new
        active session on another computer.
        """
        if not str(session.primary_device_id or "").strip():
            return False
        attachment = con.execute(
            """SELECT login_session_id,detached_at
                 FROM admission_operational_devices
                WHERE operational_session_id=%s AND device_id=%s
                FOR UPDATE""",
            (session.operational_session_id, session.primary_device_id),
        ).fetchone()
        attachment_data = _mapping(attachment)
        if not attachment_data or attachment_data.get("detached_at"):
            return False
        attachment_login = str(attachment_data.get("login_session_id") or "")
        if attachment_login != str(session.primary_login_session_id or ""):
            return False
        if self._active_sessions_available(con):
            login_row = con.execute(
                "SELECT is_active,device_id FROM active_sessions "
                "WHERE session_id=%s LIMIT 1",
                (attachment_login,),
            ).fetchone()
            login_data = _mapping(login_row)
            if (
                not login_data
                or not bool(login_data.get("is_active"))
                or str(login_data.get("device_id") or "")
                != str(session.primary_device_id or "")
            ):
                return False
        stale = con.execute(
            """SELECT primary_last_seen < NOW() - (%s || ' seconds')::interval
                 FROM admission_operational_sessions
                WHERE operational_session_id=%s""",
            (str(max(1, int(stale_after_seconds))), session.operational_session_id),
        ).fetchone()
        return not bool(stale and stale[0])

    def _take_released_primary_role(
        self,
        con: Any,
        *,
        session: OperationalSession,
        device_id: str,
        login_session_id: str,
        username: str,
    ) -> OperationalSession:
        """Atomically transfer only the station role after a released primary.

        This intentionally preserves the operational user and turn.  A later
        real user transition remains the only path that may close or create a
        turn.
        """
        started = perf_counter()
        OPERATIONAL_LOG.info("PRIMARY_ACQUIRE_START device_id=%s", device_id)
        old_primary_device = str(session.primary_device_id or "").strip()
        if old_primary_device and old_primary_device != device_id:
            con.execute(
                """UPDATE admission_operational_devices
                      SET station_role='SECONDARY',detached_at=NOW(),
                          invalidated_at=NOW(),
                          invalidated_reason='PRIMARY_SESSION_RELEASED'
                    WHERE operational_session_id=%s AND device_id=%s
                      AND detached_at IS NULL""",
                (session.operational_session_id, old_primary_device),
            )
        con.execute(
            """UPDATE admission_operational_sessions
                  SET primary_device_id=%s,primary_login_session_id=%s,
                      primary_last_seen=NOW(),updated_at=NOW(),
                      lease_generation=lease_generation+1,
                      change_reason='PRIMARY_LEASE_ACQUIRED'
                WHERE operational_session_id=%s""",
            (device_id, login_session_id, session.operational_session_id),
        )
        updated_row = con.execute(
            "SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s",
            (session.operational_session_id,),
        ).fetchone()
        updated = self._row_to_session(updated_row) or session
        OPERATIONAL_LOG.info(
            "PRIMARY_LEASE_ACQUIRED device_id=%s role=PRIMARY lease_generation=%s",
            device_id,
            updated.lease_generation,
        )
        OPERATIONAL_LOG.info(
            "PRIMARY_ACQUIRE_COMMIT device_id=%s lease_generation=%s elapsed_ms=%.1f",
            device_id,
            updated.lease_generation,
            (perf_counter() - started) * 1000.0,
        )
        self._audit(
            con,
            session_id=updated.operational_session_id,
            event_type="PRIMARY_ACQUIRED_AFTER_RELEASE",
            device_id=device_id,
            username=username,
            generation=updated.generation,
            details={"previous_primary_device_id": old_primary_device},
        )
        return updated


    def attach_device(
        self, *, login_username: str, login_user_id: Any, device_id: str,
        login_session_id: str, device_name: str = "", turn_id: int | None = None,
        login_display_name: str = "", login_role: Any = None,
    ) -> DeviceAttachment:
        """Adjunta una estación sin confundir login, representante y PRIMARY."""
        username = str(login_username or "").strip()
        device = str(device_id or "").strip()
        login_session = str(login_session_id or "").strip()
        login_identity = {"user_id": login_user_id, "username": username}
        login_role_canonical = canonical_role({"role": login_role})
        login_is_admin = login_role_canonical == ADMISSION_ROLE_ADMINISTRATOR
        login_is_aux = login_role_canonical == ADMISSION_ROLE_AUXILIARY
        if not username or not device or not login_session:
            raise ValueError("Se requieren usuario, dispositivo y sesión de login.")

        started = perf_counter()
        OPERATIONAL_LOG.info("PRIMARY_ATTACH_START device_id=%s", device)
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            row = con.execute(
                "SELECT * FROM admission_operational_sessions WHERE status='ACTIVE' "
                "ORDER BY updated_at DESC LIMIT 1 FOR UPDATE"
            ).fetchone()
            current = self._row_to_session(row)

            if not user_can_operate_admission({"role": login_role}):
                if current is None:
                    raise AdmissionWriteBlocked(
                        "Admisión está esperando que un usuario autorizado inicie "
                        "la sesión operativa desde la computadora principal."
                    )
                return DeviceAttachment(
                    current, StationRole.NONE, False,
                    "Admisión disponible en modo de consulta; usuario operativo: "
                    + (current.active_user_display_name or current.active_username) + ".",
                )

            if current is None:
                if not login_is_admin:
                    raise AdmissionWriteBlocked(
                        "No existe una sesión operativa de Admisión. Un Administrador "
                        "debe configurar primero el representante y turno operativo."
                    )
                # El primer login crea únicamente el contenedor operacional y
                # su lease PRIMARY. No convierte al Administrador autenticado
                # en representante ni inventa un turno/horario.
                operational_source_id = self._operational_source_id(con)
                current = OperationalSession(
                    operational_session_id=str(uuid.uuid4()),
                    active_username="",
                    active_user_id="",
                    primary_device_id=device,
                    primary_login_session_id=login_session,
                    turn_id=None,
                    operational_source_id=operational_source_id,
                    status="ACTIVE",
                    generation=1,
                    operational_revision=1,
                    primary_last_seen=_timestamp(),
                    updated_at=_timestamp(),
                    active_user_display_name="",
                )
                con.execute(
                    """INSERT INTO admission_operational_sessions(
                         operational_session_id,active_username,active_user_id,
                         active_user_display_name,primary_device_id,
                         primary_login_session_id,turn_id,turn_started_at,turn_ends_at,
                         operational_source_id,status,generation,operational_revision,
                         primary_last_seen,created_at,updated_at
                       ) VALUES(%s,%s,%s,%s,%s,%s,%s,NULL,NULL,
                                %s,'ACTIVE',1,1,NOW(),NOW(),NOW())""",
                    (
                        current.operational_session_id, current.active_username,
                        current.active_user_id, current.active_user_display_name,
                        current.primary_device_id, current.primary_login_session_id,
                        current.turn_id, current.operational_source_id,
                    ),
                )
                role = StationRole.PRIMARY
            else:
                identity_matches = same_user(current, login_identity)
                primary_is_active = self._primary_attachment_is_active(con, current)
                if current.primary_device_id == device:
                    if not identity_matches and not login_is_admin:
                        # Un Auxiliar diferente no puede reutilizar el lease PRIMARY
                        # ni reemplazar el login_session_id del operador vigente.
                        return DeviceAttachment(
                            current,
                            StationRole.DETACHED,
                            False,
                            (
                                "Esta computadora conserva el PRIMARY operativo, "
                                "pero Admisión está asignada actualmente a "
                                + (current.active_user_display_name or current.active_username)
                                + "."
                            ),
                        )
                    role = StationRole.PRIMARY
                    # Admin o el mismo Auxiliar pueden reasociar el login de
                    # esta misma PC sin tocar representante, turno ni generation.
                    con.execute(
                        """UPDATE admission_operational_sessions
                              SET primary_login_session_id=%s,
                                  primary_last_seen=NOW(),updated_at=NOW()
                            WHERE operational_session_id=%s
                              AND primary_device_id=%s""",
                        (login_session, current.operational_session_id, device),
                    )
                    OPERATIONAL_LOG.info(
                        "PRIMARY_LOGIN_REBOUND device_id=%s role=PRIMARY lease_generation=%s elapsed_ms=%.1f",
                        device,
                        current.lease_generation,
                        (perf_counter() - started) * 1000.0,
                    )
                elif not primary_is_active and (
                    login_is_admin or (login_is_aux and identity_matches)
                ):
                    OPERATIONAL_LOG.info(
                        "PRIMARY_LEASE_VACANT device_id=%s role_candidate=PRIMARY elapsed_ms=%.1f",
                        device,
                        (perf_counter() - started) * 1000.0,
                    )
                    OPERATIONAL_LOG.info("PRIMARY_LEASE_ACQUIRE_START device_id=%s", device)
                    current = self._take_released_primary_role(
                        con,
                        session=current,
                        device_id=device,
                        login_session_id=login_session,
                        username=username,
                    )
                    role = StationRole.PRIMARY
                else:
                    if primary_is_active:
                        OPERATIONAL_LOG.info(
                            "PRIMARY_EXISTING_VALID device_id=%s current_primary=%s",
                            device,
                            current.primary_device_id,
                        )
                    count = con.execute(
                        "SELECT COUNT(*) FROM admission_operational_devices "
                        "WHERE operational_session_id=%s AND detached_at IS NULL "
                        "AND device_id<>%s",
                        (current.operational_session_id, device),
                    ).fetchone()[0]
                    if int(count) >= self.max_devices:
                        raise AdmissionWriteBlocked(
                            "Se alcanzó el máximo de dos estaciones de Admisión."
                        )
                    # Aux mismatch queda conectado en consulta; Admin mismatch
                    # queda conectado con escritura por su rol.
                    role = StationRole.SECONDARY
                    OPERATIONAL_LOG.info(
                        "PRIMARY_ATTACH_SECONDARY device_id=%s lease_generation=%s elapsed_ms=%.1f",
                        device,
                        current.lease_generation,
                        (perf_counter() - started) * 1000.0,
                    )

            con.execute(
                """INSERT INTO admission_operational_devices(
                       operational_session_id,device_id,login_session_id,device_name,
                       station_role,attached_at,last_seen,detached_at,invalidated_at,
                       invalidated_reason,invalidated_generation,new_active_username
                   ) VALUES(%s,%s,%s,%s,%s,NOW(),NOW(),NULL,NULL,NULL,NULL,NULL)
                   ON CONFLICT(operational_session_id,device_id) DO UPDATE SET
                       login_session_id=EXCLUDED.login_session_id,
                       device_name=EXCLUDED.device_name,
                       station_role=EXCLUDED.station_role,last_seen=NOW(),
                       detached_at=NULL,invalidated_at=NULL,invalidated_reason=NULL,
                       invalidated_generation=NULL,new_active_username=NULL""",
                (
                    current.operational_session_id, device, login_session,
                    device_name, role.value,
                ),
            )
            self._audit(
                con,
                session_id=current.operational_session_id,
                event_type="DEVICE_ATTACHED",
                device_id=device,
                username=username,
                generation=current.generation,
                details={
                    "station_role": role.value,
                    "authenticated_role": login_role_canonical,
                    "user_matches_operational": same_user(current, login_identity),
                },
            )
            fresh = con.execute(
                "SELECT * FROM admission_operational_sessions "
                "WHERE operational_session_id=%s",
                (current.operational_session_id,),
            ).fetchone()

        fresh_session = self._row_to_session(fresh) or current
        identity_matches = same_user(fresh_session, login_identity)
        writable = bool(login_is_admin or (login_is_aux and identity_matches))
        if login_is_admin and not identity_matches:
            message = (
                "Conectado · Administrador · representante operativo: "
                + (
                    fresh_session.active_user_display_name
                    or fresh_session.active_username
                    or "No configurado"
                )
                + "."
            )
        elif writable:
            message = "Conectado."
        else:
            message = (
                "Conectado en modo de consulta · representante operativo: "
                + (
                    fresh_session.active_user_display_name
                    or fresh_session.active_username
                    or "No configurado"
                )
                + "."
            )
        return DeviceAttachment(fresh_session, role, writable, message)


    def rebind_login_session_to_operational_state(
        self,
        *,
        current_user: Mapping[str, Any] | Any,
        login_session_id: str,
        device_id: str,
        device_name: str = "",
    ) -> DeviceAttachment:
        """Reasocia un login sin cambiar representante, turno ni generación."""
        login_session = str(login_session_id or "").strip()
        device = str(device_id or "").strip()
        username = canonical_username(current_user)
        role_name = canonical_role(current_user)
        is_admin = role_name == ADMISSION_ROLE_ADMINISTRATOR
        is_aux = role_name == ADMISSION_ROLE_AUXILIARY
        if not login_session or not device or not username:
            raise ValueError("Se requieren usuario, dispositivo y sesión de login.")
        if not (is_admin or is_aux):
            raise AdmissionWriteBlocked(
                "El usuario inició Admisión en modo de consulta y no puede "
                "reengancharse como operador."
            )

        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            row = con.execute(
                "SELECT * FROM admission_operational_sessions WHERE status='ACTIVE' "
                "ORDER BY updated_at DESC LIMIT 1 FOR UPDATE"
            ).fetchone()
            current = self._row_to_session(row)
            if current is None:
                raise AdmissionWriteBlocked("No hay una sesión operativa activa de Admisión.")

            identity_matches = same_user(current, current_user)
            if not identity_matches and not is_admin:
                return DeviceAttachment(
                    current, StationRole.DETACHED, False,
                    "Admisión está operando actualmente con "
                    + (current.active_user_display_name or current.active_username) + ".",
                )

            device_row = con.execute(
                """SELECT login_session_id,station_role
                     FROM admission_operational_devices
                    WHERE operational_session_id=%s AND device_id=%s
                    FOR UPDATE""",
                (current.operational_session_id, device),
            ).fetchone()
            device_data = _mapping(device_row)
            old_login_session = str(
                device_data.get("login_session_id")
                or (
                    current.primary_login_session_id
                    if current.primary_device_id == device else ""
                )
                or ""
            )

            primary_is_active = self._primary_attachment_is_active(con, current)
            if current.primary_device_id == device:
                role = StationRole.PRIMARY
                con.execute(
                    """UPDATE admission_operational_sessions
                          SET primary_login_session_id=%s,
                              primary_last_seen=NOW(),updated_at=NOW()
                        WHERE operational_session_id=%s
                          AND primary_device_id=%s""",
                    (login_session, current.operational_session_id, device),
                )
                OPERATIONAL_LOG.info(
                    "PRIMARY_LOGIN_REBOUND device_id=%s role=PRIMARY lease_generation=%s",
                    device,
                    current.lease_generation,
                )
            elif not primary_is_active and (
                is_admin or (is_aux and identity_matches)
            ):
                OPERATIONAL_LOG.info(
                    "PRIMARY_LEASE_VACANT device_id=%s role_candidate=PRIMARY",
                    device,
                )
                OPERATIONAL_LOG.info("PRIMARY_LEASE_ACQUIRE_START device_id=%s", device)
                current = self._take_released_primary_role(
                    con,
                    session=current,
                    device_id=device,
                    login_session_id=login_session,
                    username=username,
                )
                role = StationRole.PRIMARY
            else:
                role = StationRole.SECONDARY
                if primary_is_active:
                    OPERATIONAL_LOG.info(
                        "PRIMARY_EXISTING_VALID device_id=%s current_primary=%s",
                        device,
                        current.primary_device_id,
                    )
                OPERATIONAL_LOG.info(
                    "PRIMARY_ATTACH_SECONDARY device_id=%s lease_generation=%s",
                    device,
                    current.lease_generation,
                )
                active_count = con.execute(
                    """SELECT COUNT(*) FROM admission_operational_devices
                        WHERE operational_session_id=%s
                          AND detached_at IS NULL AND device_id<>%s""",
                    (current.operational_session_id, device),
                ).fetchone()[0]
                if not device_row and int(active_count) >= self.max_devices:
                    raise AdmissionWriteBlocked(
                        "Se alcanzó el máximo de dos estaciones de Admisión."
                    )

            con.execute(
                """INSERT INTO admission_operational_devices(
                       operational_session_id,device_id,login_session_id,device_name,
                       station_role,attached_at,last_seen,detached_at,invalidated_at,
                       invalidated_reason,invalidated_generation,new_active_username
                   ) VALUES(%s,%s,%s,%s,%s,NOW(),NOW(),NULL,NULL,NULL,NULL,NULL)
                   ON CONFLICT(operational_session_id,device_id) DO UPDATE SET
                       login_session_id=EXCLUDED.login_session_id,
                       device_name=EXCLUDED.device_name,
                       station_role=EXCLUDED.station_role,last_seen=NOW(),
                       detached_at=NULL,invalidated_at=NULL,invalidated_reason=NULL,
                       invalidated_generation=NULL,new_active_username=NULL""",
                (
                    current.operational_session_id, device, login_session,
                    str(device_name or ""), role.value,
                ),
            )
            self._audit(
                con,
                session_id=current.operational_session_id,
                event_type="LOGIN_SESSION_REBOUND",
                device_id=device,
                username=username,
                generation=current.generation,
                details={
                    "old_login_session_id": old_login_session,
                    "new_login_session_id": login_session,
                    "station_role": role.value,
                    "turn_id": current.turn_id,
                    "authenticated_role": role_name,
                    "user_matches_operational": identity_matches,
                },
            )
            fresh = con.execute(
                "SELECT * FROM admission_operational_sessions "
                "WHERE operational_session_id=%s",
                (current.operational_session_id,),
            ).fetchone()

        session = self._row_to_session(fresh) or current
        writable = bool(is_admin or (is_aux and identity_matches))
        if is_admin and not identity_matches:
            message = (
                "Conectado · Administrador · representante operativo: "
                + (
                    session.active_user_display_name
                    or session.active_username
                    or "No configurado"
                ) + "."
            )
        elif writable:
            message = "Conectado."
        else:
            message = "Conectado en modo de consulta."
        return DeviceAttachment(session, role, writable, message)

    def heartbeat(self, *, operational_session_id: str, device_id: str) -> None:
        with self.connection_factory() as con:
            con.execute("UPDATE admission_operational_devices SET last_seen=NOW() WHERE operational_session_id=%s AND device_id=%s AND detached_at IS NULL", (operational_session_id, device_id))
            con.execute("UPDATE admission_operational_sessions SET primary_last_seen=NOW(),updated_at=NOW() WHERE operational_session_id=%s AND primary_device_id=%s AND status='ACTIVE'", (operational_session_id,device_id))


    def resolve_operational_state(
        self,
        *,
        current_user: Mapping[str, Any] | Any,
        current_session_id: str,
        current_device_id: str,
        local_generation: int | None = None,
        connection_state: ConnectivityState = ConnectivityState.CONNECTED,
        sync_state: str = "SYNCHRONIZED",
    ) -> OperationalState:
        """Lee una instantánea central y deriva permisos sin confundir identidades."""
        device_id = str(current_device_id or "").strip()
        login_session_id = str(current_session_id or "").strip()
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT s.*,
                          d.station_role,d.login_session_id AS device_login_session_id,
                          d.detached_at,d.invalidated_at,d.invalidated_reason,
                          d.invalidated_generation,d.new_active_username
                   FROM admission_operational_sessions s
                   LEFT JOIN admission_operational_devices d
                     ON d.operational_session_id=s.operational_session_id
                    AND d.device_id=%s
                   WHERE s.status='ACTIVE'
                   ORDER BY s.updated_at DESC LIMIT 1""",
                (device_id,),
            ).fetchone()
        data = _mapping(row)
        session = self._row_to_session(row)
        if session is None:
            return OperationalState(
                operational_session_id="", generation=0, active_user_id="",
                active_username="", active_user_display_name="", turn_id=None,
                primary_device_id="", primary_login_session_id="",
                local_device_id=device_id, local_login_session_id=login_session_id,
                device_role=StationRole.NONE, device_attached=False,
                user_matches_operational=False, write_allowed=False,
                connection_state=connection_state, sync_state=sync_state,
                reason_code="NO_OPERATIONAL_SESSION",
                message="No hay una sesión operativa activa de Admisión.",
                operational_revision=0,
            )

        detached = bool(data.get("detached_at") or data.get("invalidated_at"))
        attached = bool(data.get("station_role")) and not detached
        if detached:
            role = StationRole.DETACHED
        elif attached and session.primary_device_id == device_id:
            role = StationRole.PRIMARY
        elif attached:
            role = StationRole.SECONDARY
        else:
            role = StationRole.NONE

        authenticated_role = canonical_role(current_user)
        is_admin = authenticated_role == ADMISSION_ROLE_ADMINISTRATOR
        user_matches = same_user(session, current_user)
        generation_matches = (
            local_generation is None or int(local_generation) == session.generation
        )
        device_login = str(data.get("device_login_session_id") or "")
        login_matches = bool(login_session_id and device_login == login_session_id)
        primary_binding_matches = (
            role != StationRole.PRIMARY
            or session.primary_login_session_id == login_session_id
        )
        invalidated_reason = str(data.get("invalidated_reason") or "")

        reason_code = "ALLOWED"
        message = "Conectado."
        if detached:
            reason_code = "READONLY_DETACHED_DEVICE"
            message = "El equipo fue separado de la sesión operativa."
        elif not attached:
            reason_code = "READONLY_DETACHED_DEVICE"
            message = "El equipo todavía no está adjunto a la sesión operativa."
        elif not generation_matches:
            reason_code = "READONLY_STALE_GENERATION"
            message = "La sesión operativa cambió; actualice antes de escribir."
        elif not login_matches or not primary_binding_matches:
            reason_code = "READONLY_LOGIN_SESSION_STALE"
            message = "La sesión de login ya no está vinculada a este dispositivo."
        elif not user_matches and not is_admin:
            reason_code = "READONLY_DIFFERENT_USER"
            message = (
                "Admisión está operando actualmente con "
                + (session.active_user_display_name or session.active_username) + "."
            )
        elif is_admin and not user_matches:
            reason_code = "ADMIN_OPERATIONAL_MISMATCH_ALLOWED"
            message = (
                "Administrador · representante operativo: "
                + (session.active_user_display_name or session.active_username) + "."
            )

        base_write_allowed = bool(
            attached
            and (user_matches or is_admin)
            and generation_matches
            and login_matches
            and primary_binding_matches
            and session.status == "ACTIVE"
        )
        access = evaluate_admission_access(
            current_user,
            {
                "base_write_allowed": base_write_allowed,
                "device_role": role,
                "connection_state": connection_state,
                "status": session.status,
                "reason_code": reason_code,
                "active_user_id": session.active_user_id,
                "active_username": session.active_username,
            },
        )
        if authenticated_role == ADMISSION_ROLE_AUDIT:
            reason_code = access.reason_code
            message = "Facturador de Auditoría · Admisión en modo de consulta."
        elif not access.write_allowed and access.reason_code.startswith("READONLY_"):
            reason_code = access.reason_code

        return OperationalState(
            operational_session_id=session.operational_session_id,
            generation=session.generation,
            active_user_id=session.active_user_id,
            active_username=session.active_username,
            active_user_display_name=session.active_user_display_name,
            turn_id=session.turn_id,
            turn_code=session.turn_code,
            primary_device_id=session.primary_device_id,
            primary_login_session_id=session.primary_login_session_id,
            local_device_id=device_id,
            local_login_session_id=login_session_id,
            device_role=role,
            device_attached=attached,
            user_matches_operational=user_matches,
            write_allowed=access.write_allowed,
            connection_state=connection_state,
            sync_state=sync_state,
            reason_code=reason_code,
            message=message,
            invalidated_reason=invalidated_reason,
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

    def get_central_admission_operational_state(
        self,
        *,
        current_user: Mapping[str, Any] | Any,
        current_session_id: str,
        current_device_id: str,
        local_generation: int | None = None,
    ) -> OperationalState:
        """Snapshot central ligero; mismatch local/central no implica offline."""
        return self.resolve_operational_state(
            current_user=current_user,
            current_session_id=current_session_id,
            current_device_id=current_device_id,
            local_generation=local_generation,
            connection_state=ConnectivityState.CONNECTED,
            sync_state="SYNCHRONIZED",
        )

    def attachment_for_device(
        self, *, device_id: str, login_username: str,
        login_user_id: Any = None, login_session_id: str = "",
        local_generation: int | None = None,
    ) -> DeviceAttachment | None:
        """Reconsulta la autoridad central para detectar cambios de PRIMARY."""
        if login_session_id:
            state = self.resolve_operational_state(
                current_user={"user_id": login_user_id, "username": login_username},
                current_session_id=login_session_id,
                current_device_id=device_id,
                local_generation=local_generation,
            )
            if not state.operational_session_id:
                return None
            session = self.get_operational_session()
            if session is None:
                return None
            return DeviceAttachment(
                session,state.device_role,state.write_allowed,state.message
            )
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT s.*,d.station_role
                   FROM admission_operational_sessions s
                   LEFT JOIN admission_operational_devices d
                     ON d.operational_session_id=s.operational_session_id
                    AND d.device_id=%s AND d.detached_at IS NULL
                   WHERE s.status='ACTIVE'
                   ORDER BY s.updated_at DESC LIMIT 1""",
                (str(device_id),),
            ).fetchone()
        if not row:
            return None
        data = _mapping(row)
        session = self._row_to_session(row)
        if session is None:
            return None
        try:
            role = StationRole(str(data.get("station_role") or "NONE"))
        except ValueError:
            role = StationRole.NONE
        user_matches = same_user(
            session,
            {"user_id": login_user_id, "username": login_username},
        )
        writable = role in {StationRole.PRIMARY, StationRole.SECONDARY} and user_matches
        message = "Conectado."
        if not user_matches:
            message = (
                "Admisión está operando actualmente con "
                + session.active_username
                + ". Inicie sesión con ese usuario para registrar o modificar pacientes."
            )
        elif role == StationRole.NONE:
            message = "El equipo ya no está adjunto a la sesión operativa."
        return DeviceAttachment(session, role, writable, message)

    def detach_device(self, *, operational_session_id: str, device_id: str) -> None:
        with self.connection_factory() as con:
            con.execute(
                """UPDATE admission_operational_devices SET detached_at=NOW()
                   WHERE operational_session_id=%s AND device_id=%s
                     AND station_role<>'PRIMARY'""",
                (operational_session_id, device_id),
            )
            self._audit(
                con, session_id=operational_session_id,
                event_type="DEVICE_DETACHED", device_id=device_id,
            )

    def _release_login_session_locked(
        self,
        con: Any,
        *,
        device_id: str,
        login_session_id: str,
        reason: str,
    ) -> bool:
        row = con.execute(
            "SELECT * FROM admission_operational_sessions WHERE status='ACTIVE' "
            "ORDER BY updated_at DESC LIMIT 1 FOR UPDATE"
        ).fetchone()
        session = self._row_to_session(row)
        if session is None:
            return False
        device_row = con.execute(
            """SELECT station_role,login_session_id
                 FROM admission_operational_devices
                WHERE operational_session_id=%s AND device_id=%s
                FOR UPDATE""",
            (session.operational_session_id, device_id),
        ).fetchone()
        device_data = _mapping(device_row)
        if (
            not device_data
            or str(device_data.get("login_session_id") or "") != login_session_id
        ):
            return False
        role = str(device_data.get("station_role") or "")
        con.execute(
            """UPDATE admission_operational_devices
                  SET detached_at=NOW(),invalidated_at=NOW(),
                      invalidated_reason=%s
                WHERE operational_session_id=%s AND device_id=%s
                  AND login_session_id=%s AND detached_at IS NULL""",
            (
                "PRIMARY_LOGIN_RELEASED" if role == StationRole.PRIMARY.value
                else "SECONDARY_LOGIN_RELEASED",
                session.operational_session_id,
                device_id,
                login_session_id,
            ),
        )
        if role == StationRole.PRIMARY.value and session.primary_device_id == device_id:
            con.execute(
                """UPDATE admission_operational_sessions
                      SET primary_device_id='',primary_login_session_id='',
                          primary_last_seen=NOW(),updated_at=NOW(),
                          change_reason='PRIMARY_LOGIN_RELEASED'
                    WHERE operational_session_id=%s""",
                (session.operational_session_id,),
            )
            event_type = "PRIMARY_RELEASED"
        else:
            event_type = "SECONDARY_RELEASED"
        self._audit(
            con,
            session_id=session.operational_session_id,
            event_type=event_type,
            device_id=device_id,
            username=session.active_username,
            generation=session.generation,
            details={"reason": str(reason or "LOGOUT")[:120]},
        )
        return True

    def release_login_session(
        self,
        *,
        device_id: str,
        login_session_id: str,
        reason: str = "LOGOUT",
        connection: Any | None = None,
    ) -> bool:
        """Release the login lease before closing the application session.

        The operational turn and generation remain intact. Supplying the
        caller's transaction lets remote logout release the lease and close
        the login atomically under the same PostgreSQL transaction.
        """
        device = str(device_id or "").strip()
        login_session = str(login_session_id or "").strip()
        if not device or not login_session:
            return False
        if connection is not None:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            return self._release_login_session_locked(
                connection,
                device_id=device,
                login_session_id=login_session,
                reason=reason,
            )
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            return self._release_login_session_locked(
                con,
                device_id=device,
                login_session_id=login_session,
                reason=reason,
            )

    def transition_primary_turn(
        self,
        *,
        operational_session_id: str,
        primary_device_id: str,
        new_turn_id: int | None,
        expected_generation: int,
        new_turn_code: str = "",
        transition_id: str | None = None,
        changed_by: str = "",
        reason: str = "Cambio de turno principal",
        administrative_override: bool = False,
        actor_user_id: str = "",
        actor_user: Mapping[str, Any] | Any = None,
        allocate_central_turn_id: bool = False,
    ) -> PrimaryTransitionResult:
        """Cambia solo el turno; conserva representante, login y PRIMARY."""
        transition_uuid = str(transition_id or uuid.uuid4())
        requested_turn_id = _as_int_or_none(new_turn_id)

        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            duplicate = con.execute(
                """SELECT operational_session_id,details_json
                     FROM admission_operational_audit
                    WHERE transition_id=%s LIMIT 1""",
                (transition_uuid,),
            ).fetchone()
            row = con.execute(
                """SELECT * FROM admission_operational_sessions
                    WHERE operational_session_id=%s FOR UPDATE""",
                (operational_session_id,),
            ).fetchone()
            current = self._row_to_session(row)
            if not current or current.status != "ACTIVE":
                raise AdmissionWriteBlocked("La sesión operativa ya no está activa.")

            if duplicate:
                duplicate_data = _mapping(duplicate)
                details_value = duplicate_data.get("details_json") or {}
                if isinstance(details_value, str):
                    try:
                        details_value = json.loads(details_value)
                    except (TypeError, ValueError):
                        details_value = {}
                details = dict(details_value or {})
                return PrimaryTransitionResult(
                    operational_session=current,
                    transition_id=transition_uuid,
                    committed=True,
                    old_turn_id=_as_int_or_none(details.get("old_turn_id")),
                    new_turn_id=current.turn_id,
                    old_generation=int(details.get("old_generation") or current.generation),
                    new_generation=current.generation,
                    old_user_id=current.active_user_id,
                    new_user_id=current.active_user_id,
                    old_username=current.active_username,
                    new_username=current.active_username,
                )

            if current.primary_device_id != str(primary_device_id):
                raise AdmissionWriteBlocked(
                    "Solo el dispositivo principal puede cambiar el turno."
                )
            if current.generation != int(expected_generation):
                raise AdmissionWriteBlocked(
                    "La sesión operativa cambió antes de aplicar. Actualice e intente de nuevo."
                )
            primary_row = con.execute(
                """SELECT station_role FROM admission_operational_devices
                    WHERE operational_session_id=%s AND device_id=%s
                      AND detached_at IS NULL FOR UPDATE""",
                (operational_session_id, primary_device_id),
            ).fetchone()
            if not primary_row or str(primary_row[0] or "") != StationRole.PRIMARY.value:
                raise AdmissionWriteBlocked("El dispositivo principal no está adjunto.")
            if not can_change_admission_turn(
                actor_user,
                {
                    "active_user_id": current.active_user_id,
                    "active_username": current.active_username,
                    "device_role": StationRole.PRIMARY,
                    "status": current.status,
                    "connection_state": ConnectivityState.CONNECTED,
                },
            ):
                raise AdmissionWriteBlocked(
                    "Solo un Administrador o el representante operativo en la "
                    "estación PRIMARY puede cambiar el turno de Admisión."
                )

            target_turn_id = (
                self._allocate_next_central_turn_id(con)
                if allocate_central_turn_id
                else requested_turn_id
            )

            if administrative_override:
                self._audit(
                    con,
                    session_id=operational_session_id,
                    event_type="TURN_ADMIN_OVERRIDE_REQUESTED",
                    device_id=primary_device_id,
                    username=str(changed_by or current.active_username),
                    generation=current.generation,
                    transition_id=transition_uuid,
                    details={
                        "actor_user_id": str(actor_user_id or ""),
                        "old_turn_id": current.turn_id,
                        "new_turn_id": target_turn_id,
                        "reason": str(reason or "")[:240],
                        "representative_unchanged": True,
                        "primary_unchanged": True,
                    },
                )

            if current.turn_id == target_turn_id:
                return PrimaryTransitionResult(
                    operational_session=current,
                    transition_id=transition_uuid,
                    committed=True,
                    old_turn_id=current.turn_id,
                    new_turn_id=current.turn_id,
                    old_generation=current.generation,
                    new_generation=current.generation,
                    old_user_id=current.active_user_id,
                    new_user_id=current.active_user_id,
                    old_username=current.active_username,
                    new_username=current.active_username,
                )

            new_generation = current.generation + 1
            con.execute(
                """UPDATE admission_operational_sessions SET
                       turn_id=%s,
                       turn_code=COALESCE(NULLIF(%s,''),turn_code),
                       generation=%s,
                       operational_revision=operational_revision+1,
                       turn_started_at=NOW(),turn_ends_at=NOW()+INTERVAL '12 hours',
                       changed_by=%s,change_reason=%s,updated_at=NOW(),
                       primary_last_seen=NOW()
                   WHERE operational_session_id=%s""",
                (
                    target_turn_id,
                    str(new_turn_code or "").strip(),
                    new_generation,
                    str(changed_by or current.active_username),
                    str(reason or "Cambio de turno principal")[:240],
                    operational_session_id,
                ),
            )
            con.execute(
                """UPDATE admission_operational_turn_intervals
                      SET ended_at=COALESCE(ended_at,NOW())
                    WHERE operational_session_id=%s AND generation=%s""",
                (operational_session_id, current.generation),
            )
            con.execute(
                """INSERT INTO admission_operational_turn_intervals(
                       operational_session_id,generation,turn_id,active_user_id,
                       active_username,started_at,nominal_ends_at,ended_at,
                       production_epoch_id
                   ) VALUES(%s,%s,%s,%s,%s,NOW(),NOW()+INTERVAL '12 hours',NULL,
                            (SELECT production_epoch_id
                               FROM admission_operational_sessions
                              WHERE operational_session_id=%s))
                   ON CONFLICT(operational_session_id,generation) DO NOTHING""",
                (
                    operational_session_id,
                    new_generation,
                    target_turn_id,
                    current.active_user_id,
                    current.active_username,
                    operational_session_id,
                ),
            )
            changed_row = con.execute(
                "SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s",
                (operational_session_id,),
            ).fetchone()
            changed = self._row_to_session(changed_row)
            if changed is None:
                raise AdmissionWriteBlocked("No fue posible confirmar el nuevo turno.")

            details = {
                "old_turn_id": current.turn_id,
                "new_turn_id": changed.turn_id,
                "old_generation": current.generation,
                "new_generation": changed.generation,
                "operational_user_id": current.active_user_id,
                "operational_username": current.active_username,
                "representative_unchanged": True,
                "primary_device_id": current.primary_device_id,
                "primary_unchanged": True,
                "administrative_override": bool(administrative_override),
                "actor_user_id": str(actor_user_id or ""),
                "reason": str(reason or "")[:240],
            }
            self._audit(
                con,
                session_id=operational_session_id,
                event_type=(
                    "TURN_ADMIN_OVERRIDE_COMMITTED"
                    if administrative_override
                    else "PRIMARY_TURN_TRANSITION"
                ),
                device_id=primary_device_id,
                username=str(changed_by or current.active_username),
                generation=changed.generation,
                transition_id=transition_uuid,
                details=details,
            )
            self._audit(
                con,
                session_id=operational_session_id,
                event_type="OPERATIONAL_GENERATION_CHANGED",
                device_id=primary_device_id,
                username=str(changed_by or current.active_username),
                generation=changed.generation,
                details={"transition_id": transition_uuid, **details},
            )

        return PrimaryTransitionResult(
            operational_session=changed,
            transition_id=transition_uuid,
            committed=True,
            old_turn_id=current.turn_id,
            new_turn_id=changed.turn_id,
            old_generation=current.generation,
            new_generation=changed.generation,
            old_user_id=current.active_user_id,
            new_user_id=changed.active_user_id,
            old_username=current.active_username,
            new_username=changed.active_username,
        )

    def admin_change_admission_turn(
        self,
        *,
        actor_user: Mapping[str, Any] | Any,
        operational_session_id: str,
        primary_device_id: str,
        new_turn_id: int | None,
        expected_generation: int,
        new_turn_code: str = "",
        transition_id: str | None = None,
        reason: str = "Cambio administrativo de turno",
    ) -> PrimaryTransitionResult:
        """Compatibility wrapper for the canonical turn command."""
        return self.admin_set_admission_turn(
            actor_user=actor_user,
            operational_session_id=operational_session_id,
            primary_device_id=primary_device_id,
            new_turn_id=new_turn_id,
            expected_generation=expected_generation,
            new_turn_code=new_turn_code,
            transition_id=transition_id,
            reason=reason,
            administrative_override=False,
        )

    def admin_set_admission_turn(
        self,
        *,
        actor_user: Mapping[str, Any] | Any,
        operational_session_id: str,
        primary_device_id: str,
        new_turn_id: int | None,
        expected_generation: int,
        new_turn_code: str = "",
        transition_id: str | None = None,
        reason: str = "Cambio administrativo de turno",
        administrative_override: bool = False,
        allocate_central_turn_id: bool = False,
    ) -> PrimaryTransitionResult:
        """Apply a permitted PRIMARY turn command without changing its representative."""
        actor_role = canonical_role(actor_user)
        if not user_can_operate_admission(actor_user):
            raise AdmissionWriteBlocked(
                "El rol autenticado no puede cambiar el turno operativo de Admisión."
            )
        if (
            administrative_override
            and actor_role != ADMISSION_ROLE_ADMINISTRATOR
        ):
            raise AdmissionWriteBlocked(
                "Solo un Administrador puede aplicar una corrección manual fuera de horario."
            )
        started = perf_counter()
        actor_username = canonical_username(actor_user)
        event_prefix = (
            "TURN_ADMIN_OVERRIDE" if administrative_override else "TURN_CHANGE"
        )
        OPERATIONAL_LOG.info(
            "%s_START device_id=%s revision=%s",
            event_prefix,
            primary_device_id,
            expected_generation,
        )
        result = self.transition_primary_turn(
            operational_session_id=operational_session_id,
            primary_device_id=primary_device_id,
            new_turn_id=new_turn_id,
            new_turn_code=new_turn_code,
            expected_generation=expected_generation,
            transition_id=transition_id,
            changed_by=actor_username,
            reason=reason,
            administrative_override=administrative_override,
            actor_user_id=canonical_user_id(actor_user),
            actor_user=actor_user,
            allocate_central_turn_id=allocate_central_turn_id,
        )
        OPERATIONAL_LOG.info(
            "%s_COMMIT elapsed_ms=%.1f revision=%s device_id=%s",
            event_prefix,
            (perf_counter() - started) * 1000.0,
            result.new_generation,
            primary_device_id,
        )
        return result

    def transition_primary_user(
        self,
        *,
        operational_session_id: str,
        primary_device_id: str,
        new_login_session_id: str,
        new_user: Mapping[str, Any] | Any,
        new_turn_id: int | None,
        expected_generation: int,
        transition_id: str | None = None,
        changed_by: str = "",
        reason: str = "Cambio de usuario principal",
        invalidate_secondaries: bool = True,
    ) -> PrimaryTransitionResult:
        """Cambio central idempotente; PRIMARY pertenece al device, no al login."""
        if not user_can_be_assigned_admission_operator(new_user):
            raise AdmissionWriteBlocked(
                "El rol seleccionado no puede convertirse en usuario operativo de Admisión."
            )
        transition_uuid = str(transition_id or uuid.uuid4())
        new_username = str(
            (new_user.get("username") if isinstance(new_user, Mapping)
             else getattr(new_user, "username", "")) or ""
        ).strip()
        new_user_id = canonical_user_id(new_user)
        display_name = str(
            (new_user.get("full_name", new_user.get("display_name", ""))
             if isinstance(new_user, Mapping)
             else getattr(new_user, "full_name", getattr(new_user, "display_name", "")))
            or ""
        ).strip()
        if not new_username or not str(new_login_session_id or "").strip():
            raise ValueError("El nuevo usuario y su sesión de login son obligatorios.")
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            duplicate = con.execute(
                """SELECT operational_session_id,details_json FROM admission_operational_audit
                   WHERE transition_id=%s LIMIT 1""",
                (transition_uuid,),
            ).fetchone()
            row = con.execute(
                """SELECT * FROM admission_operational_sessions
                   WHERE operational_session_id=%s FOR UPDATE""",
                (operational_session_id,),
            ).fetchone()
            current = self._row_to_session(row)
            if not current or current.status != "ACTIVE":
                raise AdmissionWriteBlocked("La sesión operativa ya no está activa.")
            if duplicate:
                duplicate_data = _mapping(duplicate)
                details_value = duplicate_data.get("details_json") or {}
                if isinstance(details_value, str):
                    try:
                        details_value = json.loads(details_value)
                    except (TypeError, ValueError):
                        details_value = {}
                details = dict(details_value or {})
                return PrimaryTransitionResult(
                    operational_session=current,
                    transition_id=transition_uuid,
                    committed=True,
                    old_turn_id=_as_int_or_none(details.get("old_turn_id")),
                    new_turn_id=current.turn_id,
                    old_generation=int(details.get("old_generation") or 0),
                    new_generation=current.generation,
                    old_user_id=str(details.get("old_user_id") or ""),
                    new_user_id=current.active_user_id,
                    old_username=str(details.get("old_username") or ""),
                    new_username=current.active_username,
                )
            if current.primary_device_id != str(primary_device_id):
                raise AdmissionWriteBlocked(
                    "Solo el dispositivo principal puede cambiar el usuario operativo."
                )
            if current.generation != int(expected_generation):
                raise AdmissionWriteBlocked(
                    "La sesión operativa cambió antes de aplicar. Actualice e intente de nuevo."
                )
            primary_row = con.execute(
                """SELECT station_role,login_session_id
                   FROM admission_operational_devices
                   WHERE operational_session_id=%s AND device_id=%s
                     AND detached_at IS NULL FOR UPDATE""",
                (operational_session_id, primary_device_id),
            ).fetchone()
            primary_data = _mapping(primary_row)
            if not primary_row or str(primary_data.get("station_role") or "") != "PRIMARY":
                raise AdmissionWriteBlocked("El dispositivo principal no está adjunto.")
            if (
                same_user(current, new_user)
                and _as_int_or_none(new_turn_id) == current.turn_id
            ):
                con.execute(
                    """UPDATE admission_operational_sessions SET
                           primary_login_session_id=%s,primary_last_seen=NOW(),updated_at=NOW()
                       WHERE operational_session_id=%s""",
                    (new_login_session_id, operational_session_id),
                )
                con.execute(
                    """UPDATE admission_operational_devices SET
                           login_session_id=%s,last_seen=NOW(),detached_at=NULL,
                           invalidated_at=NULL,invalidated_reason=NULL,
                           invalidated_generation=NULL,new_active_username=NULL
                       WHERE operational_session_id=%s AND device_id=%s""",
                    (new_login_session_id, operational_session_id, primary_device_id),
                )
                unchanged_row = con.execute(
                    "SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s",
                    (operational_session_id,),
                ).fetchone()
                unchanged = self._row_to_session(unchanged_row) or current
                self._audit(
                    con,
                    session_id=operational_session_id,
                    event_type="PRIMARY_RELOGIN",
                    device_id=primary_device_id,
                    username=unchanged.active_username,
                    generation=unchanged.generation,
                    transition_id=transition_uuid,
                    details={
                        "turn_id": unchanged.turn_id,
                        "generation": unchanged.generation,
                        "requested_turn_id_ignored": _as_int_or_none(new_turn_id),
                    },
                )
                return PrimaryTransitionResult(
                    operational_session=unchanged,
                    transition_id=transition_uuid,
                    old_primary_login_session_id=current.primary_login_session_id,
                    committed=True,
                    old_turn_id=current.turn_id,
                    new_turn_id=current.turn_id,
                    old_generation=current.generation,
                    new_generation=current.generation,
                    old_user_id=current.active_user_id,
                    new_user_id=current.active_user_id,
                    old_username=current.active_username,
                    new_username=current.active_username,
                )
            secondaries = con.execute(
                """SELECT device_id,login_session_id FROM admission_operational_devices
                   WHERE operational_session_id=%s AND device_id<>%s
                     AND detached_at IS NULL ORDER BY device_id FOR UPDATE""",
                (operational_session_id, primary_device_id),
            ).fetchall()
            secondary_logins = tuple(
                str(_mapping(item).get("login_session_id") or "")
                for item in secondaries
                if str(_mapping(item).get("login_session_id") or "")
            )
            new_generation = current.generation + 1
            con.execute(
                """UPDATE admission_operational_sessions SET
                       active_username=%s,active_user_id=%s,active_user_display_name=%s,
                       primary_login_session_id=%s,turn_id=%s,generation=%s,
                       operational_revision=operational_revision+1,
                       turn_started_at=NOW(),turn_ends_at=NOW()+INTERVAL '12 hours',
                       changed_by=%s,change_reason=%s,updated_at=NOW(),primary_last_seen=NOW()
                   WHERE operational_session_id=%s""",
                (
                    new_username,new_user_id,display_name,new_login_session_id,
                    _as_int_or_none(new_turn_id),new_generation,
                    str(changed_by or new_username),str(reason),operational_session_id,
                ),
            )
            con.execute(
                """UPDATE admission_operational_turn_intervals
                   SET ended_at=COALESCE(ended_at,NOW())
                   WHERE operational_session_id=%s AND generation=%s""",
                (operational_session_id, current.generation),
            )
            con.execute(
                """INSERT INTO admission_operational_turn_intervals(
                       operational_session_id,generation,turn_id,active_user_id,
                       active_username,started_at,nominal_ends_at,ended_at,
                       production_epoch_id
                   ) VALUES(%s,%s,%s,%s,%s,NOW(),NOW()+INTERVAL '12 hours',NULL,
                            (SELECT production_epoch_id
                               FROM admission_operational_sessions
                              WHERE operational_session_id=%s))
                   ON CONFLICT(operational_session_id,generation) DO NOTHING""",
                (
                    operational_session_id,new_generation,_as_int_or_none(new_turn_id),
                    new_user_id,new_username,operational_session_id,
                ),
            )
            con.execute(
                """UPDATE admission_operational_devices SET
                       station_role='PRIMARY',login_session_id=%s,last_seen=NOW(),
                       detached_at=NULL,invalidated_at=NULL,invalidated_reason=NULL,
                       invalidated_generation=NULL,new_active_username=NULL
                   WHERE operational_session_id=%s AND device_id=%s""",
                (new_login_session_id, operational_session_id, primary_device_id),
            )
            if invalidate_secondaries:
                con.execute(
                    """UPDATE admission_operational_devices SET
                           detached_at=NOW(),invalidated_at=NOW(),
                           invalidated_reason='PRIMARY_USER_CHANGED',
                           invalidated_generation=%s,new_active_username=%s
                       WHERE operational_session_id=%s AND device_id<>%s
                         AND detached_at IS NULL""",
                    (
                        new_generation,new_username,operational_session_id,primary_device_id,
                    ),
                )
            sessions_to_close = {
                session_id for session_id in secondary_logins if session_id
            } if invalidate_secondaries else set()
            if (
                current.primary_login_session_id
                and current.primary_login_session_id != new_login_session_id
            ):
                sessions_to_close.add(current.primary_login_session_id)
            if self._active_sessions_available(con) and sessions_to_close:
                con.execute(
                    """UPDATE active_sessions SET is_active=0,logout_at=NOW(),
                           logout_reason='PRIMARY_USER_CHANGED'
                       WHERE session_id=ANY(%s) AND session_id<>%s AND is_active=1""",
                    (list(sessions_to_close), new_login_session_id),
                )
            changed_row = con.execute(
                "SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s",
                (operational_session_id,),
            ).fetchone()
            changed = self._row_to_session(changed_row)
            if changed is None:
                raise AdmissionWriteBlocked("No fue posible confirmar la transición operativa.")
            self._audit(
                con,
                session_id=operational_session_id,
                event_type="PRIMARY_USER_TRANSITION",
                device_id=primary_device_id,
                username=new_username,
                generation=changed.generation,
                transition_id=transition_uuid,
                details={
                    "old_user_id": current.active_user_id,
                    "old_username": current.active_username,
                    "new_user_id": new_user_id,
                    "new_username": new_username,
                    "old_turn_id": current.turn_id,
                    "new_turn_id": changed.turn_id,
                    "old_generation": current.generation,
                    "new_generation": changed.generation,
                    "primary_device_id": primary_device_id,
                    "secondary_sessions_invalidated": (
                        len(secondary_logins) if invalidate_secondaries else 0
                    ),
                },
            )
            # Evento de dominio consumible por heartbeats/diagnósticos. Vive en
            # la misma transacción que la nueva generation; no depende de Excel,
            # PDF, impresión ni de ningún otro efecto externo.
            self._audit(
                con,
                session_id=operational_session_id,
                event_type="OPERATIONAL_GENERATION_CHANGED",
                device_id=primary_device_id,
                username=new_username,
                generation=changed.generation,
                details={
                    "transition_id": transition_uuid,
                    "old_generation": current.generation,
                    "new_generation": changed.generation,
                    "old_user_id": current.active_user_id,
                    "new_user_id": new_user_id,
                    "old_turn_id": current.turn_id,
                    "new_turn_id": changed.turn_id,
                    "primary_device_id": primary_device_id,
                },
            )
        return PrimaryTransitionResult(
            operational_session=changed,
            transition_id=transition_uuid,
            invalidated_login_session_ids=(
                secondary_logins if invalidate_secondaries else ()
            ),
            old_primary_login_session_id=current.primary_login_session_id,
            committed=True,
            old_turn_id=current.turn_id,
            new_turn_id=changed.turn_id,
            old_generation=current.generation,
            new_generation=changed.generation,
            old_user_id=current.active_user_id,
            new_user_id=changed.active_user_id,
            old_username=current.active_username,
            new_username=changed.active_username,
        )



    def admin_set_admission_representative(
        self,
        *,
        authorizing_admin_user_id: Any = None,
        authorizing_admin_username: str = "",
        authorizing_admin_role: Any = None,
        requesting_user_id: Any = None,
        requesting_username: str = "",
        requesting_login_session_id: str = "",
        requesting_device_id: str = "",
        target_user: Mapping[str, Any] | Any,
        reason: str = "Corrección administrativa de representante",
        actor_user_id: Any = None,
        actor_username: str = "",
        actor_role: Any = None,
        actor_login_session_id: str = "",
        actor_device_id: str = "",
    ) -> OperationalSession:
        """Corrige SOLO el representante operacional.

        La contraseña del actor se valida previamente en el gateway de la app
        principal. Este servicio confirma rol/identidad del Admin en ``users``
        pero NO exige que el target tenga una sesión abierta y NO hace depender
        la corrección de ``active_sessions`` ni de quién posea PRIMARY.
        """
        admin_user_id = (
            authorizing_admin_user_id
            if authorizing_admin_user_id is not None
            else actor_user_id
        )
        admin_username = str(
            authorizing_admin_username or actor_username or ""
        ).strip()
        admin_role = (
            authorizing_admin_role
            if authorizing_admin_role is not None
            else actor_role
        )
        requester_user_id = (
            requesting_user_id
            if requesting_user_id is not None
            else actor_user_id
        )
        requester_username = str(
            requesting_username or actor_username or ""
        ).strip()
        requester_login = str(
            requesting_login_session_id or actor_login_session_id or ""
        ).strip()
        requester_device = str(
            requesting_device_id or actor_device_id or ""
        ).strip()

        if canonical_role({"role": admin_role}) != ADMISSION_ROLE_ADMINISTRATOR:
            raise AdmissionWriteBlocked(
                "Solo un Administrador puede corregir el representante de Admisión."
            )

        if not admin_username or not requester_login:
            raise AdmissionWriteBlocked(
                "La sesión solicitante actual no es válida para esta corrección."
            )

        requested_target_user_id = canonical_user_id(target_user)
        requested_target_username = canonical_username(target_user)
        if not requested_target_user_id and not requested_target_username:
            raise ValueError("El representante destino no tiene identidad válida.")

        started = perf_counter()
        OPERATIONAL_LOG.info(
            "REPRESENTATIVE_CHANGE_START authorizing_admin_user_id=%s device_id=%s",
            canonical_user_id({"user_id": admin_user_id}),
            requester_device,
        )

        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )

            row = con.execute(
                "SELECT * FROM admission_operational_sessions "
                "WHERE status='ACTIVE' ORDER BY updated_at DESC LIMIT 1 FOR UPDATE"
            ).fetchone()
            current = self._row_to_session(row)
            if current is None:
                raise AdmissionWriteBlocked("No existe una sesión operativa activa.")

            # Confirmar contra la tabla de usuarios, no contra active_sessions.
            # La sesión/login y la contraseña ya fueron verificadas por el host.
            if canonical_user_id({"user_id": admin_user_id}):
                actor_row = con.execute(
                    """SELECT id,username,full_name,role,is_active
                         FROM users
                        WHERE CAST(id AS TEXT)=%s
                        LIMIT 1 FOR UPDATE""",
                    (canonical_user_id({"user_id": admin_user_id}),),
                ).fetchone()
            else:
                actor_row = con.execute(
                    """SELECT id,username,full_name,role,is_active
                         FROM users
                        WHERE LOWER(TRIM(username))=LOWER(TRIM(%s))
                        LIMIT 1 FOR UPDATE""",
                    (admin_username,),
                ).fetchone()
            actor_data = _mapping(actor_row)
            if (
                not actor_data
                or not bool(actor_data.get("is_active"))
                or canonical_role({"role": actor_data.get("role")})
                   != ADMISSION_ROLE_ADMINISTRATOR
                or not same_user(
                    {"user_id": admin_user_id, "username": admin_username},
                    {"user_id": actor_data.get("id"),
                     "username": actor_data.get("username")},
                )
            ):
                raise AdmissionWriteBlocked(
                    "El Administrador seleccionado ya no está habilitado."
                )

            verified_admin_username = str(
                actor_data.get("username") or admin_username
            ).strip()

            # El target puede estar completamente desconectado.
            if requested_target_user_id:
                target_row = con.execute(
                    """SELECT id,username,full_name,role,is_active
                         FROM users
                        WHERE CAST(id AS TEXT)=%s
                        LIMIT 1 FOR UPDATE""",
                    (requested_target_user_id,),
                ).fetchone()
            else:
                target_row = con.execute(
                    """SELECT id,username,full_name,role,is_active
                         FROM users
                        WHERE LOWER(TRIM(username))=LOWER(TRIM(%s))
                        LIMIT 1 FOR UPDATE""",
                    (requested_target_username,),
                ).fetchone()
            target_data = _mapping(target_row)
            if not target_data or not bool(target_data.get("is_active")):
                raise AdmissionWriteBlocked(
                    "El representante seleccionado ya no está habilitado."
                )

            target_username = str(target_data.get("username") or "").strip()
            target_user_id = str(target_data.get("id") or "")
            target_display = str(
                target_data.get("full_name") or target_username
            ).strip()
            con.execute(
                """UPDATE admission_operational_sessions
                      SET active_username=%s,
                          active_user_id=%s,
                          active_user_display_name=%s,
                          operational_revision=operational_revision+1,
                          changed_by=%s,
                          change_reason=%s,
                          updated_at=NOW()
                    WHERE operational_session_id=%s""",
                (
                    target_username,
                    target_user_id,
                    target_display or target_username,
                    verified_admin_username,
                    str(reason or "Corrección administrativa de representante")[:240],
                    current.operational_session_id,
                ),
            )

            updated_row = con.execute(
                "SELECT * FROM admission_operational_sessions "
                "WHERE operational_session_id=%s",
                (current.operational_session_id,),
            ).fetchone()
            updated = self._row_to_session(updated_row)
            if updated is None:
                raise AdmissionWriteBlocked(
                    "No fue posible confirmar el nuevo representante."
                )

            # Una corrección administrativa de representante NO expulsa a nadie.
            # Se limpian únicamente invalidaciones heredadas de la antigua lógica
            # de cambio de usuario. Una revocación real de PRIMARY conserva su
            # razón específica PRIMARY_TRANSFERRED_ADMINISTRATIVELY y no entra aquí.
            con.execute(
                """UPDATE admission_operational_devices
                      SET detached_at=NULL,
                          invalidated_at=NULL,
                          invalidated_reason=NULL,
                          invalidated_generation=NULL,
                          new_active_username=NULL,
                          last_seen=NOW()
                    WHERE operational_session_id=%s
                      AND COALESCE(invalidated_reason,'') IN (
                          'SECONDARY_USER_CHANGED',
                          'PRIMARY_USER_CHANGED',
                          'OPERATIONAL_USER_CHANGED',
                          'READONLY_DIFFERENT_USER'
                      )""",
                (current.operational_session_id,),
            )

            self._audit(
                con,
                session_id=current.operational_session_id,
                event_type="TURN_REPRESENTATIVE_ADMIN_CORRECTED",
                device_id=requester_device,
                username=verified_admin_username,
                generation=current.generation,
                details={
                    "requesting_user_id": str(requester_user_id or ""),
                    "requesting_username": requester_username,
                    "requesting_login_session_id_present": bool(requester_login),
                    "authorizing_admin_user_id": str(
                        actor_data.get("id") or admin_user_id or ""
                    ),
                    "authorizing_admin_username": verified_admin_username,
                    "target_representative_user_id": target_user_id,
                    "target_username": target_username,
                    "old_user_id": current.active_user_id,
                    "old_username": current.active_username,
                    "new_user_id": updated.active_user_id,
                    "new_username": updated.active_username,
                    "turn_id": current.turn_id,
                    "primary_device_id": current.primary_device_id,
                    "primary_login_session_id_unchanged": (
                        updated.primary_login_session_id
                        == current.primary_login_session_id
                    ),
                    "lease_generation_unchanged": (
                        updated.lease_generation == current.lease_generation
                    ),
                    "generation_unchanged": (
                        updated.generation == current.generation
                    ),
                    "old_operational_revision": current.operational_revision,
                    "new_operational_revision": updated.operational_revision,
                },
            )

            OPERATIONAL_LOG.info(
                "REPRESENTATIVE_CHANGE_COMMIT elapsed_ms=%.1f revision=%s device_id=%s",
                (perf_counter() - started) * 1000.0,
                updated.operational_revision,
                requester_device,
            )

        return updated

    def admin_correct_current_turn_representative(self, **kwargs: Any) -> OperationalSession:
        """Compatibility name; representative changes use the canonical service."""
        return self.admin_set_admission_representative(**kwargs)

    def validate_operational_invariants(self) -> dict[str, Any]:
        """Diagnóstico técnico sin credenciales ni datos clínicos."""
        with self.connection_factory() as con:
            session_row = con.execute(
                """SELECT * FROM admission_operational_sessions
                   WHERE status='ACTIVE' ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
            session = self._row_to_session(session_row)
            if session is None:
                return {"valid": False, "reason": "NO_OPERATIONAL_SESSION"}
            rows = con.execute(
                """SELECT device_id,station_role,login_session_id
                   FROM admission_operational_devices
                   WHERE operational_session_id=%s AND detached_at IS NULL""",
                (session.operational_session_id,),
            ).fetchall()
        devices = [_mapping(item) for item in rows]
        primaries = [item for item in devices if item.get("station_role") == "PRIMARY"]
        valid = bool(
            len(primaries) == 1
            and str(primaries[0].get("device_id") or "") == session.primary_device_id
            and str(primaries[0].get("login_session_id") or "")
            == session.primary_login_session_id
        )
        return {
            "valid": valid,
            "operational_session_id": session.operational_session_id,
            "generation": session.generation,
            "active_user_id": session.active_user_id,
            "active_username": session.active_username,
            "turn_id": session.turn_id,
            "primary_device_id": session.primary_device_id,
            "primary_count": len(primaries),
            "attachment_count": len(devices),
        }

    def change_primary_user(
        self, *, operational_session_id: str, device_id: str, login_session_id: str,
        active_username: str, active_user_id: Any, turn_id: int | None,
        changed_by: str, reason: str = "Cambio de usuario principal",
    ) -> OperationalSession:
        current_snapshot = self.get_operational_session()
        if current_snapshot is None:
            raise AdmissionWriteBlocked("La sesión operativa ya no está activa.")
        return self.transition_primary_user(
            operational_session_id=operational_session_id,
            primary_device_id=device_id,
            new_login_session_id=login_session_id,
            new_user={"user_id": active_user_id, "username": active_username},
            new_turn_id=turn_id,
            expected_generation=current_snapshot.generation,
            changed_by=changed_by,
            reason=reason,
        ).operational_session
        # Compatibilidad histórica: el bloque inferior no se alcanza; se
        # conserva temporalmente para facilitar diffs de instalaciones previas.
        with self.connection_factory() as con:
            con.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("admission-operational-session",))
            row = con.execute("SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s FOR UPDATE", (operational_session_id,)).fetchone()
            current = self._row_to_session(row)
            if not current or current.status != "ACTIVE" or current.primary_device_id != str(device_id):
                raise AdmissionWriteBlocked("Solo la estaci\u00f3n principal puede cambiar el turno o usuario operativo.")
            con.execute(
                """UPDATE admission_operational_sessions SET
                       active_username=%s,active_user_id=%s,primary_login_session_id=%s,
                       turn_id=%s,generation=generation+1,changed_by=%s,change_reason=%s,
                       updated_at=NOW(),primary_last_seen=NOW()
                   WHERE operational_session_id=%s""",
                (str(active_username).strip(),str(active_user_id or ""),str(login_session_id),
                _as_int_or_none(turn_id),str(changed_by),str(reason),operational_session_id),
            )
            result = con.execute("SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s", (operational_session_id,)).fetchone()
            changed = self._row_to_session(result)
            self._audit(
                con, session_id=operational_session_id,
                event_type="PRIMARY_USER_CHANGED", device_id=device_id,
                username=str(active_username),
                generation=changed.generation if changed else current.generation + 1,
                details={"reason": str(reason)},
            )
        return self._row_to_session(result)  # type: ignore[return-value]

    def promote_secondary_to_primary(
        self, *, operational_session_id: str, device_id: str, login_session_id: str,
        username: str, stale_after_seconds: int = 120,
    ) -> OperationalSession:
        with self.connection_factory() as con:
            con.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("admission-operational-session",))
            row = con.execute("SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s FOR UPDATE", (operational_session_id,)).fetchone()
            session = self._row_to_session(row)
            if not session or session.status != "ACTIVE":
                raise AdmissionWriteBlocked("La sesi\u00f3n operativa ya no est\u00e1 activa.")
            if session.active_username.casefold() != str(username).strip().casefold():
                raise AdmissionWriteBlocked("Solo el usuario operativo puede promover una estaci\u00f3n.")
            device = con.execute("SELECT station_role FROM admission_operational_devices WHERE operational_session_id=%s AND device_id=%s AND detached_at IS NULL", (operational_session_id,str(device_id))).fetchone()
            if not device or str(device[0]) != StationRole.SECONDARY.value:
                raise AdmissionWriteBlocked("Solo una estaci\u00f3n secundaria adjunta puede promoverse.")
            expired = con.execute(
                """SELECT primary_last_seen < NOW() - (%s || ' seconds')::interval
                   FROM admission_operational_sessions
                   WHERE operational_session_id=%s""",
                (str(max(1,int(stale_after_seconds))), operational_session_id),
            ).fetchone()
            if not bool(expired and expired[0]):
                raise AdmissionWriteBlocked("La estaci\u00f3n principal a\u00fan mantiene un heartbeat vigente.")
            con.execute("UPDATE admission_operational_devices SET station_role='SECONDARY' WHERE operational_session_id=%s AND station_role='PRIMARY'", (operational_session_id,))
            con.execute("UPDATE admission_operational_devices SET station_role='PRIMARY',login_session_id=%s,last_seen=NOW() WHERE operational_session_id=%s AND device_id=%s", (login_session_id,operational_session_id,device_id))
            con.execute("UPDATE admission_operational_sessions SET primary_device_id=%s,primary_login_session_id=%s,primary_last_seen=NOW(),updated_at=NOW(),change_reason='PRIMARY_LEASE_PROMOTED' WHERE operational_session_id=%s", (device_id,login_session_id,operational_session_id))
            result = con.execute("SELECT * FROM admission_operational_sessions WHERE operational_session_id=%s", (operational_session_id,)).fetchone()
            promoted = self._row_to_session(result)
            self._audit(
                con, session_id=operational_session_id,
                event_type="DEVICE_PROMOTED_PRIMARY", device_id=device_id,
                username=username,
                generation=promoted.generation if promoted else session.generation,
            )
        return self._row_to_session(result)  # type: ignore[return-value]

    def force_transfer_admission_primary(
        self,
        *,
        operational_session_id: str,
        device_id: str,
        login_session_id: str,
        admin_user_id: Any,
        admin_username: str,
        admin_role: Any,
        reason: str,
    ) -> OperationalSession:
        """Atomically transfer only the PRIMARY lease after local PIN approval.

        The administrative PIN is verified by the existing ``AdminSecurity``
        component before this command is submitted.  This database operation
        repeats the role check and never changes the clinical turn, operational
        user, operational generation, or closure-report state.
        """
        target_device = str(device_id or "").strip()
        target_login = str(login_session_id or "").strip()
        actor = str(admin_username or "").strip()
        transfer_reason = str(reason or "").strip()
        if canonical_role({"role": admin_role}) != "administrador":
            raise AdmissionWriteBlocked(
                "Solo un Administrador puede transferir la sesión principal."
            )
        if not target_device or not target_login:
            raise ValueError("La estación y la sesión de login son obligatorias.")
        if not transfer_reason:
            raise ValueError("El motivo de la transferencia es obligatorio.")

        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("admission-operational-session",),
            )
            row = con.execute(
                "SELECT * FROM admission_operational_sessions "
                "WHERE operational_session_id=%s FOR UPDATE",
                (str(operational_session_id),),
            ).fetchone()
            session = self._row_to_session(row)
            if session is None or session.status != "ACTIVE":
                raise AdmissionWriteBlocked("La sesión operativa ya no está activa.")

            if not self._active_sessions_available(con):
                raise AdmissionWriteBlocked(
                    "No fue posible validar la sesión autenticada actual."
                )
            actor_row = con.execute(
                """SELECT s.username,s.device_id,s.is_active,
                          u.id AS user_id,u.role
                     FROM active_sessions s
                     JOIN users u ON LOWER(TRIM(u.username))=LOWER(TRIM(s.username))
                    WHERE s.session_id=%s
                    FOR UPDATE""",
                (target_login,),
            ).fetchone()
            actor_data = _mapping(actor_row)
            if (
                not actor_data
                or not bool(actor_data.get("is_active"))
                or str(actor_data.get("device_id") or "") != target_device
                or not same_user(
                    {"user_id": admin_user_id, "username": actor},
                    {
                        "user_id": actor_data.get("user_id"),
                        "username": actor_data.get("username"),
                    },
                )
                or canonical_role({"role": actor_data.get("role")})
                != "administrador"
            ):
                raise AdmissionWriteBlocked(
                    "La sesión autenticada actual no autoriza esta transferencia."
                )

            old_primary_device = str(session.primary_device_id or "")
            old_primary_login = str(session.primary_login_session_id or "")
            if old_primary_device == target_device:
                raise AdmissionWriteBlocked(
                    "Esta estación ya posee el acceso principal."
                )
            audit_details = {
                "admin_user_id": str(admin_user_id or ""),
                "old_primary_device": old_primary_device,
                "new_primary_device": target_device,
                "old_login_session": old_primary_login,
                "new_login_session": target_login,
                "reason": transfer_reason[:240],
            }
            self._audit(
                con,
                session_id=session.operational_session_id,
                event_type="ADMISSION_PRIMARY_TRANSFER_REQUESTED",
                device_id=target_device,
                username=actor,
                generation=session.generation,
                details=audit_details,
            )

            # The former PRIMARY is explicitly detached so its heartbeat cannot
            # silently re-acquire authority.  Its application login is revoked
            # in the same central transaction and SessionHealthWorker performs
            # the visible logout on that station.
            con.execute(
                """UPDATE admission_operational_devices
                      SET station_role='SECONDARY',last_seen=NOW(),
                          detached_at=NOW(),invalidated_at=NOW(),
                          invalidated_reason='PRIMARY_TRANSFERRED_ADMINISTRATIVELY',
                          invalidated_generation=%s,new_active_username=%s
                    WHERE operational_session_id=%s
                      AND station_role='PRIMARY'
                      AND device_id<>%s
                      AND detached_at IS NULL""",
                (
                    session.generation,
                    session.active_username,
                    session.operational_session_id,
                    target_device,
                ),
            )
            if old_primary_login:
                con.execute(
                    """UPDATE active_sessions
                          SET is_active=0,logout_at=NOW(),
                              logout_reason='PRIMARY_TRANSFERRED_ADMINISTRATIVELY'
                        WHERE session_id=%s AND device_id=%s AND is_active=1""",
                    (old_primary_login, old_primary_device),
                )
                self._audit(
                    con,
                    session_id=session.operational_session_id,
                    event_type="ADMISSION_PRIMARY_REVOKED",
                    device_id=old_primary_device,
                    username=session.active_username,
                    generation=session.generation,
                    details=audit_details,
                )
            con.execute(
                """INSERT INTO admission_operational_devices(
                       operational_session_id,device_id,login_session_id,
                       device_name,station_role,attached_at,last_seen
                   ) VALUES(%s,%s,%s,%s,'PRIMARY',NOW(),NOW())
                   ON CONFLICT(operational_session_id,device_id) DO UPDATE SET
                       login_session_id=EXCLUDED.login_session_id,
                       station_role='PRIMARY',last_seen=NOW(),detached_at=NULL,
                       invalidated_at=NULL,invalidated_reason=NULL,
                       invalidated_generation=NULL,new_active_username=NULL""",
                (
                    session.operational_session_id,
                    target_device,
                    target_login,
                    target_device,
                ),
            )
            con.execute(
                """UPDATE admission_operational_sessions
                      SET primary_device_id=%s,primary_login_session_id=%s,
                          primary_last_seen=NOW(),lease_generation=lease_generation+1,
                          updated_at=NOW(),change_reason='PRIMARY_FORCE_TRANSFER'
                    WHERE operational_session_id=%s""",
                (target_device, target_login, session.operational_session_id),
            )
            result = con.execute(
                "SELECT * FROM admission_operational_sessions "
                "WHERE operational_session_id=%s",
                (session.operational_session_id,),
            ).fetchone()
            transferred = self._row_to_session(result) or session
            self._audit(
                con,
                session_id=session.operational_session_id,
                event_type="ADMISSION_PRIMARY_TRANSFER_COMPLETED",
                device_id=target_device,
                username=actor,
                generation=transferred.generation,
                details={
                    **audit_details,
                    "lease_generation": transferred.lease_generation,
                },
            )
        return self._row_to_session(result)  # type: ignore[return-value]

    def force_transfer_primary(self, **kwargs: Any) -> OperationalSession:
        """Compatibility alias for callers from builds prior to 2026-08-20."""
        return self.force_transfer_admission_primary(**kwargs)


class AdmissionWriteGuard:
    """Guardia usada por UI, servicio y mutaci\u00f3n; nunca solo por botones."""


    def can_write_admission(
        self, *, login_user: str, device_id: str, session: OperationalSession | None,
        generation: int | None, role: StationRole, offline: bool = False,
        offline_lease_valid: bool = False, login_user_id: Any = None,
        login_role: Any = None,
    ) -> WriteDecision:
        """Guardia final: Admin no depende de la identidad del representante."""
        role_name = canonical_role({"role": login_role})
        is_admin = role_name == ADMISSION_ROLE_ADMINISTRATOR
        is_aux = role_name == ADMISSION_ROLE_AUXILIARY

        access = evaluate_admission_access(
            {"role": login_role},
            {
                "base_write_allowed": True,
                "device_role": role,
                "offline": offline,
                "status": session.status if session else "INACTIVE",
            },
        )
        if not access.write_allowed:
            owner = (
                session.active_user_display_name or session.active_username
                if session else "un usuario autorizado"
            )
            return WriteDecision(
                False, access.reason_code,
                (
                    "Admisión continúa operando con " + owner + "."
                    if role_name == ADMISSION_ROLE_AUXILIARY
                    else "Admisión disponible en modo de consulta."
                ),
                role, offline,
            )
        if not session or session.status != "ACTIVE":
            return WriteDecision(
                False, "NO_OPERATIONAL_SESSION",
                "No hay una sesión operativa validada para Admisión."
            )
        if int(generation or 0) != session.generation:
            return WriteDecision(
                False, "STALE_GENERATION",
                "La sesión principal cambió. Actualice su sesión antes de registrar pacientes.",
                role,
            )
        identity_matches = same_user(
            session, {"user_id": login_user_id, "username": login_user}
        )
        if not is_admin and (not is_aux or not identity_matches):
            return WriteDecision(
                False, "SECONDARY_USER_MISMATCH",
                "Admisión está operando actualmente con " + session.active_username
                + ". Inicie sesión con ese usuario para registrar o modificar pacientes.",
                role,
            )
        if role not in {StationRole.PRIMARY, StationRole.SECONDARY}:
            return WriteDecision(
                False, "DEVICE_NOT_ATTACHED",
                "El equipo no está autorizado en la sesión operativa."
            )
        if role == StationRole.PRIMARY and session.primary_device_id != str(device_id):
            return WriteDecision(
                False, "PRIMARY_DEVICE_MISMATCH",
                "La identidad de la estación principal no coincide.", role
            )
        if offline and not offline_lease_valid:
            return WriteDecision(
                False, "OFFLINE_LEASE_EXPIRED",
                "Conexión requerida para validar una nueva sesión de Admisión.",
                role, True,
            )
        return WriteDecision(
            True,
            "ADMIN_ALLOWED" if is_admin and not identity_matches else "ALLOWED",
            "Sin conexión · trabajando localmente." if offline else "Conectado.",
            role, offline,
        )

    def require_write(self, **kwargs: Any) -> WriteDecision:
        decision = self.can_write_admission(**kwargs)
        if not decision.allowed:
            raise AdmissionWriteBlocked(decision.message)
        return decision

    def require_primary_turn_change(self, *, role: StationRole, **kwargs: Any) -> WriteDecision:
        if bool(kwargs.get("offline")):
            raise AdmissionWriteBlocked("Se requiere conexión para cambiar el turno.")
        if role != StationRole.PRIMARY:
            raise AdmissionWriteBlocked("Solo la estaci\u00f3n principal puede cambiar o cerrar el turno.")
        return self.require_write(role=role, **kwargs)

    def require_primary_transition(
        self, *, device_id: str, session: OperationalSession | None,
        generation: int | None, role: StationRole, offline: bool = False,
        offline_lease_valid: bool = False, current_user: Any = None,
    ) -> WriteDecision:
        """Permite al mismo device PRIMARY completar un cambio intencional de user."""
        if canonical_role(current_user) != ADMISSION_ROLE_ADMINISTRATOR:
            raise AdmissionWriteBlocked(
                "Solo un Administrador en la estación PRIMARY puede cambiar el turno de Admisión."
            )
        if not session or session.status != "ACTIVE":
            raise AdmissionWriteBlocked("No hay una sesión operativa activa.")
        if role != StationRole.PRIMARY or session.primary_device_id != str(device_id):
            raise AdmissionWriteBlocked(
                "Solo el dispositivo principal puede cambiar el usuario o turno."
            )
        if int(generation or 0) != session.generation:
            raise AdmissionWriteBlocked(
                "La generación operativa cambió; actualice antes de aplicar."
            )
        if offline:
            raise AdmissionWriteBlocked(
                "Se requiere conexión para cambiar el usuario operativo."
            )
        return WriteDecision(True,"PRIMARY_TRANSITION_ALLOWED","Transición principal autorizada.",role,offline)


class AdmissionCloudRepository:
    """Repositorio de eventos centrales idempotentes y con control de versi\u00f3n."""

    def __init__(self, connection_factory: Callable[[], Any]):
        self.connection_factory = connection_factory

    def begin_seed(
        self,
        *,
        central_seed_id: str,
        legacy_source_instance_id: str,
        source_fingerprint: str,
        origin_device_id: str,
        schema_version: int = 1,
    ) -> bool:
        with self.connection_factory() as con:
            row = con.execute(
                """INSERT INTO admission_central_seeds(
                       central_seed_id,legacy_source_instance_id,seed_source_fingerprint,
                       schema_version,status,origin_device_id
                   ) VALUES(%s,%s,%s,%s,'RUNNING',%s)
                   ON CONFLICT(legacy_source_instance_id,seed_source_fingerprint,schema_version)
                   DO UPDATE SET status=CASE
                       WHEN admission_central_seeds.status='COMPLETED' THEN 'COMPLETED'
                       ELSE 'RUNNING' END
                   RETURNING status""",
                (
                    str(central_seed_id),
                    str(legacy_source_instance_id),
                    str(source_fingerprint),
                    int(schema_version),
                    str(origin_device_id),
                ),
            ).fetchone()
        return bool(row and str(row[0]).upper() != "COMPLETED")

    def complete_seed(self, *, central_seed_id: str, imported_records: int) -> None:
        with self.connection_factory() as con:
            con.execute(
                """UPDATE admission_central_seeds
                      SET status='COMPLETED',imported_records=%s,seed_completed_at=NOW()
                    WHERE central_seed_id=%s""",
                (int(imported_records), str(central_seed_id)),
            )

    def server_time(self) -> datetime:
        with self.connection_factory() as con:
            row = con.execute("SELECT NOW() AS server_time").fetchone()
        value = _mapping(row).get("server_time") if row else None
        if value is None and row:
            value = row[0]
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))

    @staticmethod
    def _readthrough_event(row: Mapping[str, Any]) -> dict[str, Any]:
        """Builds one cache-hydration event from the authoritative projection."""
        data = dict(row or {})
        payload_value = (
            data.get("latest_payload_json") or data.get("latest_payload") or {}
        )
        if isinstance(payload_value, str):
            try:
                payload = dict(json.loads(payload_value) or {})
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
        else:
            payload = dict(payload_value or {})
        global_attention_id = str(data.get("global_attention_id") or "").strip()
        source_instance_id = str(data.get("source_instance_id") or "LEGACY").strip()
        effective_at = str(
            data.get("created_at_effective_utc")
            or data.get("source_updated_at")
            or data.get("synced_at")
            or _timestamp()
        )
        projection_payload = {
            "global_attention_id": global_attention_id,
            "attention_id": data.get("attention_id"),
            "legacy_source_instance_id": source_instance_id,
            "legacy_attention_id": data.get("attention_id"),
            "source_instance_id": source_instance_id,
            "global_patient_id": str(data.get("global_patient_id") or ""),
            "patient_id": data.get("patient_id"),
            "legacy_patient_id": data.get("patient_id"),
            "operational_session_id": str(data.get("operational_session_id") or ""),
            "operational_source_id": str(data.get("operational_source_id") or ""),
            "turn_id": data.get("turn_id"),
            "generation": int(data.get("generation") or 0),
            "origin_device_id": str(data.get("origin_device_id") or "CENTRAL"),
            "origin_user_id": str(data.get("origin_user_id") or ""),
            "admission_username": str(data.get("admission_username") or ""),
            "captured_by_username": str(data.get("captured_by_username") or ""),
            "name": str(data.get("patient_name") or ""),
            "ars": str(data.get("canonical_ars") or ""),
            "nss": str(data.get("nss_snapshot") or ""),
            "cedula": str(data.get("cedula_snapshot") or ""),
            "service_date": str(data.get("service_date") or ""),
            "service_time": str(data.get("service_time") or ""),
            "service_type": str(data.get("service_type") or "EMERGENCIA"),
            "specialty": str(data.get("specialty") or ""),
            "detail_sheet": "GENERADA" if data.get("has_detail_sheet") else "",
            "source_status": str(data.get("source_status") or "ACTIVA"),
            "created_at_device": str(data.get("created_at_device") or effective_at),
            "created_at_effective_utc": effective_at,
            "device_local_sequence": int(data.get("device_local_sequence") or 0),
            "version": int(data.get("server_revision") or data.get("version") or 1),
            "base_server_revision": int(data.get("server_revision") or 0),
            "is_deleted": bool(data.get("is_deleted")),
            "deleted_at": str(data.get("deleted_at") or ""),
            "deleted_by_user_id": str(data.get("deleted_by_user_id") or ""),
            "delete_event_uuid": str(data.get("delete_event_uuid") or ""),
            "delete_reason": str(data.get("delete_reason") or ""),
        }
        projection_payload.update(payload)
        # A historical event can contain stale local identifiers. Projection
        # identity/tombstone fields are authoritative for cache hydration.
        projection_payload.update({
            "global_attention_id": global_attention_id,
            "attention_id": data.get("attention_id"),
            "legacy_source_instance_id": source_instance_id,
            "legacy_attention_id": data.get("attention_id"),
            "source_instance_id": source_instance_id,
            "global_patient_id": str(data.get("global_patient_id") or ""),
            "patient_id": data.get("patient_id"),
            "legacy_patient_id": data.get("patient_id"),
            "is_deleted": bool(data.get("is_deleted")),
            "source_status": str(data.get("source_status") or "ACTIVA"),
            "deleted_at": str(data.get("deleted_at") or ""),
            "deleted_by_user_id": str(data.get("deleted_by_user_id") or ""),
            "delete_event_uuid": str(data.get("delete_event_uuid") or ""),
            "delete_reason": str(data.get("delete_reason") or ""),
            "version": int(data.get("server_revision") or data.get("version") or 1),
        })
        operation = str(data.get("latest_operation") or "").upper()
        if not operation:
            operation = "DELETE" if projection_payload["is_deleted"] else "RECONCILE"
        event_uuid = str(data.get("latest_event_uuid") or "").strip()
        if not event_uuid:
            event_uuid = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    "hospital-admission-readthrough:"
                    f"{global_attention_id}:{projection_payload['version']}",
                )
            )
        return {
            "sequence": int(data.get("latest_sequence") or 0),
            "cloud_event_seq": int(data.get("latest_sequence") or 0),
            "event_uuid": event_uuid,
            "entity_type": "attention",
            "entity_uuid": global_attention_id,
            "operation": operation,
            "payload_json": projection_payload,
            "operational_session_id": projection_payload["operational_session_id"],
            "operational_source_id": projection_payload["operational_source_id"],
            "turn_id": projection_payload["turn_id"],
            "generation": projection_payload["generation"],
            "origin_device_id": projection_payload["origin_device_id"],
            "origin_user_id": projection_payload["origin_user_id"],
            "origin_username": projection_payload["admission_username"],
            "created_at_device": projection_payload["created_at_device"],
            "created_at_effective_utc": projection_payload["created_at_effective_utc"],
            "device_local_sequence": projection_payload["device_local_sequence"],
            "resulting_version": projection_payload["version"],
        }

    def get_attention_by_global_id(
        self, global_attention_id: str, *, include_deleted: bool = True
    ) -> dict[str, Any] | None:
        """Returns one authoritative record and a hydration envelope by UUID."""
        try:
            normalized = str(uuid.UUID(str(global_attention_id)))
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("global_attention_id no es valido.") from exc
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT p.*,
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
                    WHERE p.global_attention_id=%s::UUID
                      AND (%s OR p.is_deleted=FALSE)
                    LIMIT 1""",
                (normalized, bool(include_deleted)),
            ).fetchone()
        if not row:
            return None
        result = _mapping(row)
        result["event"] = self._readthrough_event(result)
        return result

    def current_turn_attention_events(
        self,
        *,
        operational_source_id: str,
        turn_id: int,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Read authoritative projection rows for one distributed turn only."""
        source_id = str(operational_source_id or "").strip()
        effective_turn_id = int(turn_id or 0)
        if not source_id or effective_turn_id <= 0:
            return []
        with self.connection_factory() as con:
            rows = con.execute(
                """SELECT p.*,
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
                    WHERE p.operational_source_id::TEXT=%s
                      AND p.turn_id=%s
                    ORDER BY COALESCE(
                                p.created_at_effective_utc,
                                NULLIF(p.synced_at,'')::TIMESTAMPTZ
                             ) ASC,
                             COALESCE(p.origin_device_id,'') ASC,
                             COALESCE(p.device_local_sequence,0) ASC,
                             COALESCE(p.global_attention_id::TEXT,p.attention_id::TEXT) ASC
                    LIMIT %s""",
                (source_id, effective_turn_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._readthrough_event(_mapping(row)) for row in rows]

    def cancel_attention(
        self,
        global_attention_id: str,
        *,
        current_user: Mapping[str, Any] | Any,
        reason: str,
        operational_session: OperationalSession,
        device_id: str,
    ) -> dict[str, Any] | None:
        """Creates an idempotent central tombstone; DELETE wins normal updates."""
        reason_text = str(reason or "").strip()
        if len(reason_text) < 5:
            raise ValueError("La anulacion requiere un motivo de al menos 5 caracteres.")
        current = self.get_attention_by_global_id(
            global_attention_id, include_deleted=True
        )
        if current is None:
            return None
        if bool(current.get("is_deleted")):
            return current
        event_data = dict(current.get("event") or {})
        payload = dict(event_data.get("payload_json") or {})
        event_uuid = str(uuid.uuid4())
        now = _timestamp()
        actor_username = (
            canonical_username(current_user) or operational_session.active_username
        )
        actor_user_id = (
            canonical_user_id(current_user) or operational_session.active_user_id
        )
        payload.update(
            {
                "event_type": "ATTENTION_DELETED",
                "global_attention_id": str(uuid.UUID(str(global_attention_id))),
                "source_status": "ANULADA",
                "is_deleted": True,
                "deleted_at": now,
                "deleted_by_user_id": actor_user_id,
                "delete_event_uuid": event_uuid,
                "delete_reason": reason_text,
                # admission_username identifica al representante operacional.
                # El actor real viaja separado para auditoría.
                "admission_username": operational_session.active_username,
                "operational_representative_user_id": operational_session.active_user_id,
                "origin_user_id": actor_user_id,
                "captured_by_username": actor_username,
                "origin_device_id": str(device_id),
            }
        )
        event = SyncEvent(
            event_uuid=event_uuid,
            entity_type="attention",
            entity_uuid=str(uuid.UUID(str(global_attention_id))),
            operation="DELETE",
            payload=payload,
            operational_session_id=operational_session.operational_session_id,
            generation=operational_session.generation,
            device_id=str(device_id),
            created_at=now,
            base_version=int(current.get("server_revision") or current.get("version") or 0),
            operational_source_id=operational_session.operational_source_id,
            turn_id=_as_int_or_none(current.get("turn_id")),
            origin_user_id=actor_user_id,
            origin_username=actor_username,
            created_at_device=now,
            created_at_effective_utc=now,
        )
        self.push_event(event)
        return self.get_attention_by_global_id(global_attention_id, include_deleted=True)

    def backfill_projection_events(self, *, limit: int = 500) -> int:
        """Crea transporte cloud para proyecciones legacy sin event log."""
        try:
            from psycopg2.extras import execute_values
        except ImportError:
            return 0
        with self.connection_factory() as con:
            raw_connection = getattr(con, "con", None)
            if raw_connection is None:
                return 0
            session = con.execute(
                """SELECT operational_session_id,operational_source_id,
                          generation,active_username
                   FROM admission_operational_sessions
                   WHERE status='ACTIVE'
                   ORDER BY updated_at DESC LIMIT 1"""
            ).fetchone()
            if not session:
                return 0
            rows = con.execute(
                """SELECT p.* FROM admission_attention_projection p
                   WHERE p.global_attention_id IS NOT NULL
                     AND NOT EXISTS(
                         SELECT 1 FROM admission_sync_events e
                         WHERE e.entity_uuid=p.global_attention_id
                     )
                   ORDER BY p.service_date,p.service_time,p.attention_id
                   LIMIT %s""",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
            values = []
            for raw in rows:
                data = _mapping(raw)
                global_attention_id = str(data.get("global_attention_id") or "")
                if not global_attention_id:
                    continue
                event_uuid = str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"hospital-admission-projection-backfill:{global_attention_id}",
                    )
                )
                effective = str(
                    data.get("created_at_effective_utc")
                    or data.get("source_updated_at")
                    or f"{data.get('service_date') or '1970-01-01'} "
                       f"{data.get('service_time') or '00:00:00'}"
                )
                device_id = str(data.get("origin_device_id") or "CENTRAL-LEGACY")
                operational_source_id = str(
                    data.get("operational_source_id") or session[1] or ""
                )
                username = str(
                    data.get("admission_username") or session[3] or ""
                )
                is_deleted = bool(data.get("is_deleted"))
                operation = "DELETE" if is_deleted else "RECONCILE"
                payload = {
                    "event_type": "ATTENTION_RECONCILED",
                    "global_attention_id": global_attention_id,
                    "attention_id": data.get("attention_id"),
                    "global_patient_id": str(data.get("global_patient_id") or ""),
                    "patient_id": data.get("patient_id"),
                    "name": str(data.get("patient_name") or ""),
                    "cedula": str(data.get("cedula_snapshot") or ""),
                    "nss": str(data.get("nss_snapshot") or ""),
                    "ars": str(data.get("canonical_ars") or ""),
                    "service_date": str(data.get("service_date") or ""),
                    "service_time": str(data.get("service_time") or ""),
                    "service_type": str(data.get("service_type") or "EMERGENCIA"),
                    "specialty": str(data.get("specialty") or ""),
                    "detail_sheet": "GENERADA" if data.get("has_detail_sheet") else "",
                    "source_status": str(data.get("source_status") or "ACTIVA"),
                    "is_deleted": is_deleted,
                    "deleted_at": str(data.get("deleted_at") or ""),
                    "deleted_by_user_id": str(data.get("deleted_by_user_id") or ""),
                    "delete_event_uuid": str(data.get("delete_event_uuid") or ""),
                    "delete_reason": str(data.get("delete_reason") or ""),
                    "source_instance_id": str(data.get("source_instance_id") or "LEGACY"),
                    "operational_source_id": operational_source_id,
                    "operational_session_id": str(session[0]),
                    "turn_id": data.get("turn_id"),
                    "generation": int(session[2] or 0),
                    "origin_device_id": device_id,
                    "admission_username": username,
                    "created_at_device": effective,
                    "created_at_effective_utc": effective,
                    "device_local_sequence": int(data.get("device_local_sequence") or 0),
                    "version": int(data.get("version") or 1),
                    "reconciliation_status": "CENTRAL_BACKFILL",
                }
                values.append(
                    (
                        event_uuid,
                        global_attention_id,
                        operation,
                        json.dumps(payload, ensure_ascii=False, sort_keys=True),
                        str(session[0]),
                        int(session[2] or 0),
                        device_id,
                        int(data.get("version") or 1),
                        effective,
                        operational_source_id,
                        data.get("turn_id"),
                        str(data.get("origin_user_id") or ""),
                        username,
                        effective,
                        effective,
                        int(data.get("device_local_sequence") or 0),
                    )
                )
            if not values:
                return 0
            with raw_connection.cursor() as cursor:
                execute_values(
                    cursor,
                    """INSERT INTO admission_sync_events(
                           event_uuid,entity_type,entity_uuid,operation,payload_json,
                           operational_session_id,generation,origin_device_id,
                           base_version,resulting_version,created_at,received_at,
                           operational_source_id,turn_id,origin_user_id,origin_username,
                           created_at_device,created_at_effective_utc,
                           device_local_sequence,server_time_offset_ms,reconciliation_status
                       ) VALUES %s
                       ON CONFLICT(event_uuid) DO NOTHING
                       RETURNING sequence""",
                    values,
                    template=(
                        "(%s::uuid,'attention',%s::uuid,%s,%s::jsonb,"
                        "%s::uuid,%s,%s,0,%s,%s::timestamptz,NOW(),%s::uuid,%s,"
                        "%s,%s,%s::timestamptz,%s::timestamptz,%s,0,'CENTRAL_BACKFILL')"
                    ),
                    page_size=200,
                )
                return len(cursor.fetchall())

    def backfill_projection_payloads(self, *, limit: int = 500) -> int:
        """Copies the latest durable event payload into legacy projection rows."""
        with self.connection_factory() as con:
            rows = con.execute(
                """WITH targets AS (
                       SELECT p.global_attention_id
                         FROM admission_attention_projection p
                        WHERE p.global_attention_id IS NOT NULL
                          AND (p.latest_payload_json IS NULL
                               OR p.latest_payload_json='{}'::jsonb)
                        ORDER BY p.global_attention_id
                        LIMIT %s
                     ), latest AS (
                       SELECT t.global_attention_id,e.payload_json
                         FROM targets t
                         JOIN LATERAL (
                           SELECT payload_json
                             FROM admission_sync_events e
                            WHERE e.entity_type='attention'
                              AND e.entity_uuid=t.global_attention_id
                            ORDER BY e.sequence DESC LIMIT 1
                         ) e ON TRUE
                     )
                     UPDATE admission_attention_projection p
                        SET latest_payload_json=latest.payload_json
                       FROM latest
                      WHERE p.global_attention_id=latest.global_attention_id
                  RETURNING p.global_attention_id""",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return len(rows)

    @staticmethod
    def _reconcile_stale_creation(con: Any, event: SyncEvent) -> SyncEvent:
        effective_at = (
            event.created_at_effective_utc
            or event.created_at_device
            or event.created_at
        )
        interval = con.execute(
            """SELECT generation,turn_id,active_user_id,active_username
               FROM admission_operational_turn_intervals
               WHERE operational_session_id=%s
                 AND started_at<=%s
                 AND (ended_at IS NULL OR %s<ended_at)
               ORDER BY started_at DESC LIMIT 1""",
            (event.operational_session_id, effective_at, effective_at),
        ).fetchone()
        if not interval:
            return replace(
                event,
                payload={
                    **dict(event.payload),
                    "reconciliation_status": "OFFLINE_UNRESOLVED",
                    "original_turn_id": event.turn_id,
                    "captured_by_username": event.origin_username,
                },
            )
        try:
            data = _mapping(interval)
        except (TypeError, ValueError):
            data = {}
        generation = int(data.get("generation") or interval[0] or event.generation)
        turn_id = _as_int_or_none(data.get("turn_id") if data else interval[1])
        payload = {
            **dict(event.payload),
            "turn_id": turn_id,
            "generation": generation,
            "original_turn_id": event.turn_id,
            "reconciliation_status": "OFFLINE_ADJUSTED",
            "captured_by_user_id": event.origin_user_id,
            "captured_by_username": event.origin_username,
        }
        return replace(event, payload=payload, turn_id=turn_id)

    @staticmethod
    def _materialize_attention(con: Any, event: SyncEvent, version: int) -> None:
        payload = dict(event.payload or {})
        attention_id = _as_int_or_none(payload.get("attention_id"))
        patient_id = _as_int_or_none(payload.get("patient_id"))
        if attention_id is None or patient_id is None:
            raise SyncConflict("La atención no incluye sus identificadores locales de origen.")
        global_attention_id = str(
            payload.get("global_attention_id") or event.entity_uuid
        ).strip()
        operation = event.operation.upper()
        is_delete = operation in {"DELETE", "CANCEL", "ATTENTION_DELETED"} or bool(
            payload.get("is_deleted")
        )
        is_restore = operation == "RESTORE_ATTENTION"
        existing_tombstone = con.execute(
            """SELECT is_deleted,server_revision
               FROM admission_attention_projection
               WHERE global_attention_id=%s""",
            (global_attention_id,),
        ).fetchone()
        if existing_tombstone and bool(existing_tombstone[0]) and not is_delete and not is_restore:
            raise SyncConflict(
                json.dumps(
                    {"reason_code": "STALE_RECORD_SUPPRESSED_BY_TOMBSTONE"},
                    ensure_ascii=False,
                )
            )
        global_patient_id = str(payload.get("global_patient_id") or "").strip() or None
        operational_source_id = str(
            payload.get("operational_source_id") or ""
        ).strip() or None
        source_instance_id = str(
            payload.get("source_instance_id") or operational_source_id or "LEGACY"
        ).strip()
        status = (
            "ANULADA"
            if is_delete
            else str(payload.get("source_status") or "ACTIVA").strip().upper()
        )
        name = str(payload.get("name") or "SIN NOMBRE").strip()
        ars = str(payload.get("ars") or "").strip()
        nss = str(payload.get("nss") or "").strip()
        cedula = str(payload.get("cedula") or "").strip()
        sheet = str(payload.get("detail_sheet") or "").strip()
        service_date = _normalize_service_date(payload.get("service_date"))
        service_type = normalize_service_type(payload.get("service_type"))
        coverage = assess_coverage(ars, nss)
        readiness = assess_billing_readiness(
            name=name,
            service_date=service_date,
            attention_type=service_type,
            coverage=coverage,
            cedula=cedula,
        )
        operational_session_id = str(
            payload.get("operational_session_id") or event.operational_session_id
        ).strip()
        generation = (
            _as_int_or_none(payload.get("generation")) or int(event.generation)
        )
        origin_device_id = str(
            payload.get("origin_device_id") or event.device_id
        ).strip()
        created_at_device = str(
            payload.get("created_at_device")
            or event.created_at_device
            or event.created_at
        )
        created_at_effective = str(
            payload.get("created_at_effective_utc")
            or event.created_at_effective_utc
            or created_at_device
        )
        device_local_sequence = int(
            payload.get("device_local_sequence")
            or event.device_local_sequence
            or 0
        )
        origin_user_id = str(
            payload.get("origin_user_id") or event.origin_user_id or ""
        )
        captured_by_username = str(
            payload.get("captured_by_username")
            or payload.get("admission_username")
            or event.origin_username
            or ""
        )
        reconciliation_status = str(
            payload.get("reconciliation_status") or "DIRECT"
        ).upper()
        original_turn_id = _as_int_or_none(
            payload.get("original_turn_id") or payload.get("turn_id")
        )
        payload_json = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, default=str
        )
        if global_patient_id:
            upsert_patient_from_attention_connection(
                con,
                global_patient_id=global_patient_id,
                source_instance_id=source_instance_id,
                legacy_patient_id=patient_id,
                patient_name=name,
                cedula=cedula,
                nss=nss,
                phone=str(payload.get("phone") or ""),
                address=str(payload.get("address") or ""),
                nationality=str(payload.get("nationality") or ""),
                ars=ars,
                server_revision=int(version),
            )
        snapshot_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        values = (
            patient_id,
            _as_int_or_none(payload.get("turn_id")) or 0,
            service_date,
            str(payload.get("service_time") or ""),
            name,
            coverage.status,
            coverage.canonical_ars,
            nss,
            cedula,
            service_type,
            str(payload.get("specialty") or ""),
            str(payload.get("admission_username") or ""),
            status,
            bool(sheet),
            readiness.status,
            json.dumps(list(readiness.reasons), ensure_ascii=False),
            str(payload.get("updated_at") or event.created_at),
            snapshot_hash,
            event.created_at,
            global_patient_id,
            operational_source_id,
            int(version),
            origin_device_id,
            operational_session_id,
            generation,
            created_at_device,
            created_at_effective,
            device_local_sequence,
            origin_user_id,
            captured_by_username,
            reconciliation_status,
            original_turn_id,
            payload_json,
            global_attention_id,
        )
        updated = con.execute(
            """UPDATE admission_attention_projection SET
                   patient_id=%s,turn_id=%s,service_date=%s,service_time=%s,
                   patient_name=%s,coverage_status=%s,canonical_ars=%s,
                   nss_snapshot=%s,cedula_snapshot=%s,service_type=%s,specialty=%s,
                   admission_username=%s,source_status=%s,has_detail_sheet=%s,
                   readiness=%s,readiness_reasons=%s,source_updated_at=%s,
                   snapshot_hash=%s,contract_version=1,synced_at=%s,
                   global_patient_id=COALESCE(%s,global_patient_id),
                   operational_source_id=COALESCE(%s,operational_source_id),
                   version=%s,origin_device_id=%s,operational_session_id=%s,generation=%s
                  ,created_at_device=COALESCE(%s,created_at_device)
                  ,created_at_effective_utc=COALESCE(%s,created_at_effective_utc)
                  ,device_local_sequence=%s,origin_user_id=%s
                  ,captured_by_username=%s,reconciliation_status=%s
                  ,original_turn_id=COALESCE(%s,original_turn_id)
                  ,latest_payload_json=%s::jsonb
               WHERE global_attention_id=%s
               RETURNING attention_id""",
            values,
        ).fetchone()
        tombstone_values = (
            bool(is_delete),
            str(payload.get("deleted_at") or event.created_at) if is_delete else None,
            str(payload.get("deleted_by_user_id") or event.origin_user_id or "") if is_delete else None,
            str(payload.get("delete_event_uuid") or event.event_uuid) if is_delete else None,
            str(payload.get("delete_reason") or "") if is_delete else None,
            int(version),
            global_attention_id,
        )
        if updated:
            con.execute(
                """UPDATE admission_attention_projection SET
                       is_deleted=%s,deleted_at=%s,deleted_by_user_id=%s,
                       delete_event_uuid=%s,delete_reason=%s,server_revision=%s
                   WHERE global_attention_id=%s""",
                tombstone_values,
            )
            return
        con.execute(
            """INSERT INTO admission_attention_projection(
                   source_instance_id,attention_id,patient_id,turn_id,service_date,service_time,
                   patient_name,coverage_status,canonical_ars,nss_snapshot,cedula_snapshot,
                   service_type,specialty,admission_username,source_status,has_detail_sheet,
                   readiness,readiness_reasons,source_updated_at,snapshot_hash,contract_version,
                   synced_at,global_attention_id,global_patient_id,operational_source_id,
                   version,origin_device_id,operational_session_id,generation,
                   created_at_device,created_at_effective_utc,device_local_sequence,
                   origin_user_id,captured_by_username,reconciliation_status,original_turn_id
                   ,latest_payload_json
               ) VALUES(
                   %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                   %s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s,
                   %s,%s,%s,%s,%s,%s,%s,%s::jsonb
               )
               ON CONFLICT(source_instance_id,attention_id) DO UPDATE SET
                   patient_id=EXCLUDED.patient_id,turn_id=EXCLUDED.turn_id,
                   service_date=EXCLUDED.service_date,service_time=EXCLUDED.service_time,
                   patient_name=EXCLUDED.patient_name,coverage_status=EXCLUDED.coverage_status,
                   canonical_ars=EXCLUDED.canonical_ars,nss_snapshot=EXCLUDED.nss_snapshot,
                   cedula_snapshot=EXCLUDED.cedula_snapshot,service_type=EXCLUDED.service_type,
                   specialty=EXCLUDED.specialty,admission_username=EXCLUDED.admission_username,
                   source_status=EXCLUDED.source_status,
                   has_detail_sheet=EXCLUDED.has_detail_sheet,readiness=EXCLUDED.readiness,
                   readiness_reasons=EXCLUDED.readiness_reasons,
                   source_updated_at=EXCLUDED.source_updated_at,
                   snapshot_hash=EXCLUDED.snapshot_hash,synced_at=EXCLUDED.synced_at,
                   global_attention_id=EXCLUDED.global_attention_id,
                   global_patient_id=EXCLUDED.global_patient_id,
                   operational_source_id=EXCLUDED.operational_source_id,
                   version=EXCLUDED.version,origin_device_id=EXCLUDED.origin_device_id,
                   operational_session_id=EXCLUDED.operational_session_id,
                   generation=EXCLUDED.generation,
                   created_at_device=EXCLUDED.created_at_device,
                   created_at_effective_utc=EXCLUDED.created_at_effective_utc,
                   device_local_sequence=EXCLUDED.device_local_sequence,
                   origin_user_id=EXCLUDED.origin_user_id,
                   captured_by_username=EXCLUDED.captured_by_username,
                   reconciliation_status=EXCLUDED.reconciliation_status,
                   original_turn_id=EXCLUDED.original_turn_id,
                   latest_payload_json=EXCLUDED.latest_payload_json""",
            (
                source_instance_id,
                attention_id,
                patient_id,
                _as_int_or_none(payload.get("turn_id")) or 0,
                service_date,
                str(payload.get("service_time") or ""),
                name,
                coverage.status,
                coverage.canonical_ars,
                nss,
                cedula,
                service_type,
                str(payload.get("specialty") or ""),
                str(payload.get("admission_username") or ""),
                status,
                bool(sheet),
                readiness.status,
                json.dumps(list(readiness.reasons), ensure_ascii=False),
                str(payload.get("updated_at") or event.created_at),
                snapshot_hash,
                event.created_at,
                global_attention_id,
                global_patient_id,
                operational_source_id,
                int(version),
                origin_device_id,
                operational_session_id,
                generation,
                created_at_device,
                created_at_effective,
                device_local_sequence,
                origin_user_id,
                captured_by_username,
                reconciliation_status,
                original_turn_id,
                payload_json,
            ),
        )
        con.execute(
            """UPDATE admission_attention_projection SET
                   is_deleted=%s,deleted_at=%s,deleted_by_user_id=%s,
                   delete_event_uuid=%s,delete_reason=%s,server_revision=%s
               WHERE global_attention_id=%s""",
            tombstone_values,
        )

    def _push_event_pre_hybrid_v2(self, event: SyncEvent) -> int:
        with self.connection_factory() as con:
            con.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"admission-sync:{event.entity_type}:{event.entity_uuid}",))
            session = con.execute(
                """SELECT active_username,generation,status
                   FROM admission_operational_sessions
                   WHERE operational_session_id=%s FOR UPDATE""",
                (event.operational_session_id,),
            ).fetchone()
            if not session or str(session[2]).upper() != "ACTIVE":
                raise AdmissionWriteBlocked("La sesión operativa ya no está activa.")
            if int(session[1] or 0) != int(event.generation):
                raise AdmissionWriteBlocked(
                    "La sesión principal cambió; el evento requiere revisión antes de sincronizar."
                )
            device = con.execute(
                """SELECT station_role FROM admission_operational_devices
                   WHERE operational_session_id=%s AND device_id=%s AND detached_at IS NULL""",
                (event.operational_session_id, event.device_id),
            ).fetchone()
            if not device:
                raise AdmissionWriteBlocked("El equipo no pertenece a la sesión operativa.")
            actor = str(event.payload.get("admission_username") or "").strip()
            if actor and actor.casefold() != str(session[0] or "").strip().casefold():
                raise AdmissionWriteBlocked(
                    "El usuario del evento no coincide con el usuario operativo principal."
                )
            existing = con.execute("SELECT sequence,resulting_version FROM admission_sync_events WHERE event_uuid=%s", (event.event_uuid,)).fetchone()
            if existing:
                return int(existing[0])
            newest = con.execute("SELECT COALESCE(MAX(resulting_version),0) FROM admission_sync_events WHERE entity_type=%s AND entity_uuid=%s", (event.entity_type,event.entity_uuid)).fetchone()
            current_version = int(newest[0] or 0)
            if (
                current_version != int(event.base_version or 0)
                and event.operation.upper() != "RECONCILE"
            ):
                remote = con.execute("SELECT payload_json FROM admission_sync_events WHERE entity_type=%s AND entity_uuid=%s ORDER BY sequence DESC LIMIT 1", (event.entity_type,event.entity_uuid)).fetchone()
                raise SyncConflict(json.dumps(dict(remote[0] if remote else {}),ensure_ascii=False))
            row = con.execute(
                """INSERT INTO admission_sync_events(
                       event_uuid,entity_type,entity_uuid,operation,payload_json,
                       operational_session_id,generation,origin_device_id,base_version,resulting_version,
                       created_at,received_at
                   ) VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,NOW())
                   RETURNING sequence""",
                (event.event_uuid,event.entity_type,event.entity_uuid,event.operation,event.payload_json(),
                 event.operational_session_id,event.generation,event.device_id,event.base_version,
                 current_version+1,event.created_at),
            ).fetchone()
            if event.entity_type.casefold() == "attention":
                self._materialize_attention(con, event, current_version + 1)
            return int(row[0])

    def push_event(self, event: SyncEvent) -> int:
        """Publica idempotentemente y reconcilia CREATEs offline de generation vieja."""
        with self.connection_factory() as con:
            con.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                (f"admission-sync:{event.entity_type}:{event.entity_uuid}",),
            )
            existing = con.execute(
                "SELECT sequence,resulting_version FROM admission_sync_events WHERE event_uuid=%s",
                (event.event_uuid,),
            ).fetchone()
            if existing:
                return int(existing[0])
            session = con.execute(
                """SELECT active_username,generation,status,turn_id,
                          operational_source_id,updated_at
                   FROM admission_operational_sessions
                   WHERE operational_session_id=%s FOR UPDATE""",
                (event.operational_session_id,),
            ).fetchone()
            if not session or str(session[2]).upper() != "ACTIVE":
                raise AdmissionWriteBlocked("La sesión operativa ya no está activa.")
            stale_generation = int(session[1] or 0) != int(event.generation)
            event_to_store = event
            if stale_generation:
                if event.operation.upper() not in {"CREATE", "RECONCILE", "DELETE"}:
                    raise SyncConflict(
                        json.dumps(
                            {"reason_code": "STALE_GENERATION_EDIT"},
                            ensure_ascii=False,
                        )
                    )
                event_to_store = self._reconcile_stale_creation(con, event)
            device = con.execute(
                """SELECT station_role FROM admission_operational_devices
                   WHERE operational_session_id=%s AND device_id=%s""",
                (event.operational_session_id, event.device_id),
            ).fetchone()
            if not device:
                raise AdmissionWriteBlocked("El equipo no pertenece a la sesión operativa.")
            actor = str(event.payload.get("admission_username") or "").strip()
            if (
                not stale_generation
                and actor
                and actor.casefold() != str(session[0] or "").strip().casefold()
            ):
                raise AdmissionWriteBlocked(
                    "El usuario del evento no coincide con el usuario operativo principal."
                )
            newest = con.execute(
                """SELECT resulting_version,operation,payload_json
                   FROM admission_sync_events
                   WHERE entity_type=%s AND entity_uuid=%s
                   ORDER BY resulting_version DESC,sequence DESC LIMIT 1""",
                (event_to_store.entity_type, event_to_store.entity_uuid),
            ).fetchone()
            projected_version = con.execute(
                """SELECT server_revision FROM admission_attention_projection
                   WHERE global_attention_id=%s""",
                (event_to_store.entity_uuid,),
            ).fetchone()
            current_version = max(
                int(newest[0] or 0) if newest else 0,
                int(projected_version[0] or 0) if projected_version else 0,
            )
            latest_operation = str(newest[1] or "").upper() if newest else ""
            operation = event_to_store.operation.upper()
            if latest_operation in {"DELETE", "CANCEL", "ATTENTION_DELETED"} and operation not in {
                "DELETE", "RESTORE_ATTENTION"
            }:
                raise SyncConflict(
                    json.dumps(
                        {"reason_code": "STALE_RECORD_SUPPRESSED_BY_TOMBSTONE"},
                        ensure_ascii=False,
                    )
                )
            if (
                current_version != int(event_to_store.base_version or 0)
                and operation not in {"RECONCILE", "DELETE"}
            ):
                remote = con.execute(
                    """SELECT payload_json FROM admission_sync_events
                       WHERE entity_type=%s AND entity_uuid=%s
                       ORDER BY sequence DESC LIMIT 1""",
                    (event_to_store.entity_type, event_to_store.entity_uuid),
                ).fetchone()
                raise SyncConflict(
                    json.dumps(dict(remote[0] if remote else {}), ensure_ascii=False)
                )
            row = con.execute(
                """INSERT INTO admission_sync_events(
                       event_uuid,entity_type,entity_uuid,operation,payload_json,
                       operational_session_id,generation,origin_device_id,
                       base_version,resulting_version,created_at,received_at,
                       operational_source_id,turn_id,origin_user_id,origin_username,
                       created_at_device,created_at_effective_utc,
                       device_local_sequence,server_time_offset_ms,reconciliation_status
                   ) VALUES(
                       %s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,NOW(),
                       %s,%s,%s,%s,%s,%s,%s,%s,%s
                   ) RETURNING sequence""",
                (
                    event_to_store.event_uuid,
                    event_to_store.entity_type,
                    event_to_store.entity_uuid,
                    event_to_store.operation,
                    event_to_store.payload_json(),
                    event_to_store.operational_session_id,
                    event_to_store.generation,
                    event_to_store.device_id,
                    event_to_store.base_version,
                    current_version + 1,
                    event_to_store.created_at,
                    event_to_store.operational_source_id or None,
                    event_to_store.turn_id,
                    event_to_store.origin_user_id,
                    event_to_store.origin_username,
                    event_to_store.created_at_device or event_to_store.created_at,
                    event_to_store.created_at_effective_utc or event_to_store.created_at,
                    event_to_store.device_local_sequence,
                    event_to_store.server_time_offset_ms,
                    str(event_to_store.payload.get("reconciliation_status") or "DIRECT"),
                ),
            ).fetchone()
            if event_to_store.entity_type.casefold() == "attention":
                self._materialize_attention(con, event_to_store, current_version + 1)
            return int(row[0])

    @staticmethod
    def _bulk_projection_row(event: SyncEvent) -> tuple[Any, ...]:
        """Construye una fila de proyección sin alterar la identidad local de origen."""
        payload = dict(event.payload or {})
        attention_id = _as_int_or_none(payload.get("attention_id"))
        patient_id = _as_int_or_none(payload.get("patient_id"))
        if attention_id is None or patient_id is None:
            raise SyncConflict(
                "La atención no incluye sus identificadores locales de origen."
            )
        operational_source_id = str(
            payload.get("operational_source_id")
            or event.operational_source_id
            or ""
        ).strip() or None
        source_instance_id = str(
            payload.get("source_instance_id") or operational_source_id or "LEGACY"
        ).strip()
        global_attention_id = str(
            payload.get("global_attention_id") or event.entity_uuid
        ).strip()
        global_patient_id = str(payload.get("global_patient_id") or "").strip() or None
        service_date = _normalize_service_date(payload.get("service_date"))
        service_type = normalize_service_type(payload.get("service_type"))
        name = str(payload.get("name") or "SIN NOMBRE").strip()
        ars = str(payload.get("ars") or "").strip()
        nss = str(payload.get("nss") or "").strip()
        cedula = str(payload.get("cedula") or "").strip()
        coverage = assess_coverage(ars, nss)
        readiness = assess_billing_readiness(
            name=name,
            service_date=service_date,
            attention_type=service_type,
            coverage=coverage,
            cedula=cedula,
        )
        created_at_device = str(
            payload.get("created_at_device")
            or event.created_at_device
            or event.created_at
        )
        created_at_effective = str(
            payload.get("created_at_effective_utc")
            or event.created_at_effective_utc
            or created_at_device
        )
        origin_username = str(
            payload.get("captured_by_username")
            or payload.get("admission_username")
            or event.origin_username
            or ""
        )
        is_delete = bool(
            event.operation.upper()
            in {"DELETE", "CANCEL", "ATTENTION_DELETED"}
            or payload.get("is_deleted")
        )
        status = (
            "ANULADA"
            if is_delete
            else str(payload.get("source_status") or "ACTIVA").strip().upper()
        )
        snapshot_hash = hashlib.sha256(
            json.dumps(
                payload, ensure_ascii=False, sort_keys=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        return (
            source_instance_id,
            attention_id,
            patient_id,
            _as_int_or_none(payload.get("turn_id")) or event.turn_id or 0,
            service_date,
            str(payload.get("service_time") or ""),
            name,
            coverage.status,
            coverage.canonical_ars,
            nss,
            cedula,
            service_type,
            str(payload.get("specialty") or ""),
            str(payload.get("admission_username") or event.origin_username or ""),
            status,
            bool(str(payload.get("detail_sheet") or "").strip()),
            readiness.status,
            json.dumps(list(readiness.reasons), ensure_ascii=False),
            str(payload.get("updated_at") or event.created_at),
            snapshot_hash,
            event.created_at,
            global_attention_id,
            global_patient_id,
            operational_source_id,
            int(payload.get("version") or 1),
            str(payload.get("origin_device_id") or event.device_id).strip(),
            str(
                payload.get("operational_session_id")
                or event.operational_session_id
            ).strip(),
            _as_int_or_none(payload.get("generation")) or event.generation,
            created_at_device,
            created_at_effective,
            int(
                payload.get("device_local_sequence")
                or event.device_local_sequence
                or 0
            ),
            str(payload.get("origin_user_id") or event.origin_user_id or ""),
            origin_username,
            str(payload.get("reconciliation_status") or "DIRECT").upper(),
            _as_int_or_none(
                payload.get("original_turn_id") or payload.get("turn_id")
            ),
            is_delete,
            str(payload.get("deleted_at") or event.created_at) if is_delete else None,
            str(payload.get("deleted_by_user_id") or event.origin_user_id or "")
            if is_delete
            else None,
            str(payload.get("delete_event_uuid") or event.event_uuid)
            if is_delete
            else None,
            str(payload.get("delete_reason") or "") if is_delete else None,
            int(payload.get("version") or 1),
            json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
        )

    @staticmethod
    def _bulk_materialize_missing_projections(
        raw_connection: Any,
        events: list[SyncEvent],
    ) -> None:
        if not events:
            return
        from psycopg2.extras import execute_values

        rows = [AdmissionCloudRepository._bulk_projection_row(event) for event in events]
        with raw_connection.cursor() as cursor:
            execute_values(
                cursor,
                """INSERT INTO admission_attention_projection(
                       source_instance_id,attention_id,patient_id,turn_id,
                       service_date,service_time,patient_name,coverage_status,
                       canonical_ars,nss_snapshot,cedula_snapshot,service_type,
                       specialty,admission_username,source_status,has_detail_sheet,
                       readiness,readiness_reasons,source_updated_at,snapshot_hash,
                       contract_version,synced_at,global_attention_id,global_patient_id,
                       operational_source_id,version,origin_device_id,
                       operational_session_id,generation,created_at_device,
                       created_at_effective_utc,device_local_sequence,origin_user_id,
                       captured_by_username,reconciliation_status,original_turn_id,
                       is_deleted,deleted_at,deleted_by_user_id,delete_event_uuid,
                       delete_reason,server_revision,latest_payload_json
                   ) VALUES %s
                   ON CONFLICT(source_instance_id,attention_id) DO UPDATE SET
                       patient_id=EXCLUDED.patient_id,
                       turn_id=EXCLUDED.turn_id,
                       service_date=EXCLUDED.service_date,
                       service_time=EXCLUDED.service_time,
                       patient_name=EXCLUDED.patient_name,
                       coverage_status=EXCLUDED.coverage_status,
                       canonical_ars=EXCLUDED.canonical_ars,
                       nss_snapshot=EXCLUDED.nss_snapshot,
                       cedula_snapshot=EXCLUDED.cedula_snapshot,
                       service_type=EXCLUDED.service_type,
                       specialty=EXCLUDED.specialty,
                       admission_username=EXCLUDED.admission_username,
                       source_status=EXCLUDED.source_status,
                       has_detail_sheet=EXCLUDED.has_detail_sheet,
                       readiness=EXCLUDED.readiness,
                       readiness_reasons=EXCLUDED.readiness_reasons,
                       source_updated_at=EXCLUDED.source_updated_at,
                       snapshot_hash=EXCLUDED.snapshot_hash,
                       synced_at=EXCLUDED.synced_at,
                       global_attention_id=EXCLUDED.global_attention_id,
                       global_patient_id=EXCLUDED.global_patient_id,
                       operational_source_id=EXCLUDED.operational_source_id,
                       version=EXCLUDED.version,
                       origin_device_id=EXCLUDED.origin_device_id,
                       operational_session_id=EXCLUDED.operational_session_id,
                       generation=EXCLUDED.generation,
                       created_at_device=EXCLUDED.created_at_device,
                       created_at_effective_utc=EXCLUDED.created_at_effective_utc,
                       device_local_sequence=EXCLUDED.device_local_sequence,
                       origin_user_id=EXCLUDED.origin_user_id,
                       captured_by_username=EXCLUDED.captured_by_username,
                       reconciliation_status=EXCLUDED.reconciliation_status,
                       original_turn_id=EXCLUDED.original_turn_id,
                       is_deleted=EXCLUDED.is_deleted,
                       deleted_at=EXCLUDED.deleted_at,
                       deleted_by_user_id=EXCLUDED.deleted_by_user_id,
                       delete_event_uuid=EXCLUDED.delete_event_uuid,
                       delete_reason=EXCLUDED.delete_reason,
                       server_revision=EXCLUDED.server_revision,
                       latest_payload_json=EXCLUDED.latest_payload_json""",
                rows,
                template=(
                    "(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                    "%s,%s::jsonb,%s,%s,1,%s,%s::uuid,%s::uuid,%s::uuid,%s,"
                    "%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::uuid,"
                    "%s,%s,%s::jsonb)"
                ),
                page_size=200,
            )

    def push_events(self, events: list[SyncEvent]) -> dict[str, int]:
        """Publica reconciliaciones homogéneas en una sola transacción."""
        if not events:
            return {}
        processed: dict[str, int] = {}
        deferred: list[SyncEvent] = []
        try:
            from psycopg2.extras import execute_values
        except ImportError:
            deferred = list(events)
        else:
            with self.connection_factory() as con:
                raw_connection = getattr(con, "con", None)
                if raw_connection is None:
                    deferred = list(events)
                else:
                    first = events[0]
                    session = con.execute(
                        """SELECT active_username,generation,status
                           FROM admission_operational_sessions
                           WHERE operational_session_id=%s""",
                        (first.operational_session_id,),
                    ).fetchone()
                    device = con.execute(
                        """SELECT station_role FROM admission_operational_devices
                           WHERE operational_session_id=%s AND device_id=%s""",
                        (first.operational_session_id, first.device_id),
                    ).fetchone()
                    if not session or str(session[2]).upper() != "ACTIVE" or not device:
                        raise AdmissionWriteBlocked(
                            "La sesión operativa o el equipo ya no están activos."
                        )
                    current_generation = int(session[1] or 0)
                    candidates = []
                    echoes = []
                    for event in events:
                        actor = str(event.origin_username or event.payload.get("admission_username") or "")
                        payload_device = str(
                            event.payload.get("origin_device_id") or ""
                        ).replace("-", "").casefold()
                        event_device = str(event.device_id or "").replace(
                            "-", ""
                        ).casefold()
                        is_remote_echo = bool(
                            event.operation.upper() == "RECONCILE"
                            and payload_device
                            and event_device
                            and payload_device != event_device
                        )
                        if is_remote_echo:
                            echoes.append(event)
                            continue
                        compatible = bool(
                            event.operation.upper() == "RECONCILE"
                            and str(
                                event.payload.get("reconciliation_status") or ""
                            ).upper()
                            != "CENTRAL_SEED"
                            and event.operational_session_id == first.operational_session_id
                            and event.device_id == first.device_id
                            and event.generation == current_generation
                            and (
                                not actor
                                or actor.casefold()
                                == str(session[0] or "").strip().casefold()
                            )
                        )
                        (candidates if compatible else deferred).append(event)
                    if echoes:
                        echo_entities = [event.entity_uuid for event in echoes]
                        rows = con.execute(
                            """SELECT DISTINCT ON (entity_uuid)
                                      entity_uuid::TEXT,sequence
                               FROM admission_sync_events
                               WHERE entity_uuid=ANY(%s::uuid[])
                               ORDER BY entity_uuid,sequence DESC""",
                            (echo_entities,),
                        ).fetchall()
                        latest_by_entity = {
                            str(uuid.UUID(str(row[0]))): int(row[1]) for row in rows
                        }
                        for event in echoes:
                            sequence = latest_by_entity.get(
                                str(uuid.UUID(event.entity_uuid)), 0
                            )
                            if sequence:
                                processed[str(uuid.UUID(event.event_uuid))] = sequence
                            else:
                                deferred.append(event)
                    if candidates:
                        entity_ids = [str(uuid.UUID(event.entity_uuid)) for event in candidates]
                        projected_rows = con.execute(
                            """SELECT global_attention_id::TEXT,is_deleted
                               FROM admission_attention_projection
                               WHERE global_attention_id=ANY(%s::uuid[])""",
                            (entity_ids,),
                        ).fetchall()
                        projected = {
                            str(uuid.UUID(str(row[0]))): bool(row[1])
                            for row in projected_rows
                        }
                        ready = []
                        for event in candidates:
                            entity_uuid = str(uuid.UUID(event.entity_uuid))
                            if projected.get(entity_uuid) is True:
                                deferred.append(event)
                                continue
                            ready.append(event)
                        if ready:
                            # A projection is the bootstrap authority, so every
                            # accepted reconciliation refreshes it, not only a
                            # previously missing row.
                            self._bulk_materialize_missing_projections(
                                raw_connection,
                                ready,
                            )
                            values = [
                                (
                                    event.event_uuid,event.entity_type,event.entity_uuid,
                                    event.operation,event.payload_json(),
                                    event.operational_session_id,event.generation,
                                    event.device_id,event.base_version,
                                    event.created_at,event.operational_source_id or None,
                                    event.turn_id,event.origin_user_id,event.origin_username,
                                    event.created_at_device or event.created_at,
                                    event.created_at_effective_utc or event.created_at,
                                    event.device_local_sequence,
                                    event.server_time_offset_ms,
                                    str(event.payload.get("reconciliation_status") or "DIRECT"),
                                )
                                for event in ready
                            ]
                            with raw_connection.cursor() as cursor:
                                execute_values(
                                    cursor,
                                    """INSERT INTO admission_sync_events(
                                       event_uuid,entity_type,entity_uuid,operation,payload_json,
                                       operational_session_id,generation,origin_device_id,
                                       base_version,resulting_version,created_at,received_at,
                                       operational_source_id,turn_id,origin_user_id,origin_username,
                                       created_at_device,created_at_effective_utc,
                                       device_local_sequence,server_time_offset_ms,reconciliation_status
                                   ) VALUES %s
                                   ON CONFLICT(event_uuid) DO NOTHING
                                   RETURNING event_uuid::TEXT,sequence""",
                                    values,
                                    template=(
                                        "(%s::uuid,%s,%s::uuid,%s,%s::jsonb,%s::uuid,%s,%s,%s,1,"
                                        "%s,NOW(),%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s)"
                                    ),
                                    page_size=200,
                                )
                                for event_uuid, sequence in cursor.fetchall():
                                    processed[str(uuid.UUID(str(event_uuid)))] = int(sequence)
                            missing_acks = [
                                event.event_uuid
                                for event in ready
                                if str(uuid.UUID(event.event_uuid)) not in processed
                            ]
                            if missing_acks:
                                rows = con.execute(
                                    """SELECT event_uuid::TEXT,sequence
                                       FROM admission_sync_events
                                       WHERE event_uuid=ANY(%s::uuid[])""",
                                    (missing_acks,),
                                ).fetchall()
                                processed.update(
                                    {
                                        str(uuid.UUID(str(row[0]))): int(row[1])
                                        for row in rows
                                    }
                                )
        for event in deferred:
            processed[str(uuid.UUID(event.event_uuid))] = self.push_event(event)
        return processed

    def events_after(self, cursor: int, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.connection_factory() as con:
            rows = con.execute(
                "SELECT * FROM admission_sync_events WHERE sequence>%s ORDER BY sequence LIMIT %s",
                (max(0,int(cursor)),max(1,min(int(limit),500))),
            ).fetchall()
        return [_mapping(row) for row in rows]

    def event_window(self) -> dict[str, int]:
        """Returns the retained incremental window and its projection checkpoint."""
        with self.connection_factory() as con:
            row = con.execute(
                """SELECT f.minimum_available_sequence,f.checkpoint_sequence,
                          COALESCE(MAX(e.sequence),0) AS latest_sequence
                     FROM admission_replication_event_floors f
                     LEFT JOIN admission_sync_events e ON TRUE
                    WHERE f.stream_name='ATTENTION'
                    GROUP BY f.minimum_available_sequence,f.checkpoint_sequence"""
            ).fetchone()
        data = _mapping(row)
        return {
            "minimum_available_sequence": int(
                data.get("minimum_available_sequence") or 0
            ),
            "checkpoint_sequence": int(data.get("checkpoint_sequence") or 0),
            "latest_sequence": int(data.get("latest_sequence") or 0),
        }

    def projection_snapshot_events(
        self, *, after_global_attention_id: str = "", limit: int = 500
    ) -> list[dict[str, Any]]:
        """Pages a reconstructible projection without depending on retained events."""
        with self.connection_factory() as con:
            rows = con.execute(
                """SELECT p.*
                     FROM admission_attention_projection p
                    WHERE p.global_attention_id IS NOT NULL
                      AND p.global_attention_id::TEXT>%s
                    ORDER BY p.global_attention_id::TEXT
                    LIMIT %s""",
                (
                    str(after_global_attention_id or ""),
                    max(1, min(int(limit), 500)),
                ),
            ).fetchall()
        return [self._readthrough_event(_mapping(row)) for row in rows]

    def rematerialize_attention_events(
        self, entity_uuids: list[str], *, force: bool = False
    ) -> int:
        """Reproduce eventos centrales cuando su proyecciÃ³n falta o fue degradada."""
        normalized = []
        for value in entity_uuids:
            try:
                normalized.append(str(uuid.UUID(str(value))))
            except (ValueError, TypeError, AttributeError):
                continue
        if not normalized:
            return 0
        repaired = 0
        with self.connection_factory() as con:
            rows = con.execute(
                """SELECT DISTINCT ON (e.entity_uuid)
                          e.*,
                          p.global_attention_id AS projected_global_attention_id,
                          p.operational_source_id::TEXT AS projected_operational_source_id,
                          p.operational_session_id::TEXT AS projected_operational_session_id,
                          p.generation AS projected_generation,
                          p.readiness AS projected_readiness,
                          p.created_at_effective_utc AS projected_effective_at,
                          p.device_local_sequence AS projected_local_sequence
                   FROM admission_sync_events e
                   LEFT JOIN admission_attention_projection p
                     ON p.global_attention_id=e.entity_uuid
                   WHERE e.entity_type='attention'
                     AND e.entity_uuid=ANY(%s::uuid[])
                     AND NOT (
                         e.operation='RECONCILE'
                         AND NULLIF(e.payload_json->>'origin_device_id','') IS NOT NULL
                         AND REPLACE(LOWER(e.payload_json->>'origin_device_id'),'-','')
                             <> REPLACE(LOWER(e.origin_device_id),'-','')
                     )
                   ORDER BY
                     e.entity_uuid,
                     COALESCE(
                       e.created_at_effective_utc,
                       NULLIF(e.payload_json->>'created_at_effective_utc','')::timestamptz,
                       e.created_at
                     ) DESC,
                     REPLACE(LOWER(e.origin_device_id),'-','') DESC,
                     COALESCE(e.device_local_sequence,0) DESC,
                     e.sequence DESC""",
                (normalized,),
            ).fetchall()
            for raw in rows:
                data = _mapping(raw)
                payload_value = data.get("payload_json") or {}
                payload = (
                    json.loads(payload_value)
                    if isinstance(payload_value, str)
                    else dict(payload_value)
                )
                expected_source = str(payload.get("operational_source_id") or "").strip()
                expected_session = str(
                    payload.get("operational_session_id")
                    or data.get("operational_session_id")
                    or ""
                ).strip()
                expected_generation = int(
                    payload.get("generation") or data.get("generation") or 0
                )
                needs_repair = (
                    not data.get("projected_global_attention_id")
                    or str(data.get("projected_operational_source_id") or "") != expected_source
                    or str(data.get("projected_operational_session_id") or "") != expected_session
                    or int(data.get("projected_generation") or 0) != expected_generation
                    or str(data.get("projected_readiness") or "") == "READY"
                    or not data.get("projected_effective_at")
                )
                if not force and not needs_repair:
                    continue
                event = SyncEvent(
                    event_uuid=str(data.get("event_uuid") or ""),
                    entity_type="attention",
                    entity_uuid=str(data.get("entity_uuid") or ""),
                    operation=str(data.get("operation") or "UPDATE"),
                    payload=payload,
                    operational_session_id=str(data.get("operational_session_id") or ""),
                    generation=int(data.get("generation") or 0),
                    device_id=str(data.get("origin_device_id") or ""),
                    created_at=str(data.get("created_at") or _timestamp()),
                    base_version=int(data.get("base_version") or 0),
                    operational_source_id=str(
                        data.get("operational_source_id")
                        or payload.get("operational_source_id")
                        or ""
                    ),
                    turn_id=_as_int_or_none(
                        data.get("turn_id") or payload.get("turn_id")
                    ),
                    origin_user_id=str(data.get("origin_user_id") or ""),
                    origin_username=str(data.get("origin_username") or ""),
                    created_at_device=str(
                        data.get("created_at_device")
                        or payload.get("created_at_device")
                        or data.get("created_at")
                        or ""
                    ),
                    created_at_effective_utc=str(
                        data.get("created_at_effective_utc")
                        or payload.get("created_at_effective_utc")
                        or data.get("created_at")
                        or ""
                    ),
                    device_local_sequence=int(
                        data.get("device_local_sequence")
                        or payload.get("device_local_sequence")
                        or 0
                    ),
                    server_time_offset_ms=int(
                        data.get("server_time_offset_ms") or 0
                    ),
                )
                self._materialize_attention(
                    con, event, int(data.get("resulting_version") or 1)
                )
                repaired += 1
        return repaired


class AdmissionSyncService:
    """Push/pull incremental; la aplicaci\u00f3n concreta decide c\u00f3mo aplicar cada evento."""

    def __init__(self, store: OfflineAdmissionStore, cloud: AdmissionCloudRepository):
        self.store = store
        self.cloud = cloud
        self._initial_reconciliation_complete = False
        self._projection_backfill_complete = False
        self._last_pull_metrics = {"fetch_ms": 0.0, "apply_ms": 0.0}

    def bootstrap_from_projection(self, *, batch_size: int = 500) -> int:
        """Rebuilds a stale/new replica before entering the retained event window."""
        page_loader = getattr(self.cloud, "projection_snapshot_events", None)
        window_loader = getattr(self.cloud, "event_window", None)
        if not callable(page_loader) or not callable(window_loader):
            raise SyncConflict("La réplica requiere un snapshot central no disponible.")
        window = dict(window_loader() or {})
        checkpoint = int(
            window.get("checkpoint_sequence")
            or window.get("latest_sequence")
            or 0
        )
        total = 0
        after_id = ""
        safe_batch_size = max(1, min(int(batch_size), 500))
        while True:
            events = list(
                page_loader(
                    after_global_attention_id=after_id,
                    limit=safe_batch_size,
                )
                or []
            )
            if not events:
                break
            total += self.store.hydrate_remote_events(events)
            unresolved = [
                event
                for event in events
                if not self.store.is_remote_event_materialized(event)
            ]
            if unresolved:
                raise SyncConflict(
                    "El snapshot central no pudo materializarse completamente."
                )
            after_id = max(str(event.get("entity_uuid") or "") for event in events)
            if not after_id:
                raise SyncConflict("El snapshot central contiene una identidad inválida.")
            if len(events) < safe_batch_size:
                break
        self.store.set_last_cloud_cursor(checkpoint)
        OPERATIONAL_LOG.info(
            "ADMISSION_SYNC_PROJECTION_BOOTSTRAP count=%s checkpoint=%s",
            total,
            checkpoint,
        )
        return total

    def _bootstrap_if_cursor_expired(self, *, batch_size: int) -> int:
        window_loader = getattr(self.cloud, "event_window", None)
        if not callable(window_loader):
            return 0
        window = dict(window_loader() or {})
        floor = int(window.get("minimum_available_sequence") or 0)
        if self.store.last_cloud_cursor() >= floor:
            return 0
        return self.bootstrap_from_projection(batch_size=batch_size)

    def push_outbox(self, *, limit: int = 100) -> dict[str, int]:
        result = {"pushed": 0, "conflicts": 0, "retry": 0}
        events = self.store.pending_events(limit)
        try:
            uploaded = self.cloud.push_events(events)
        except Exception as exc:  # noqa: BLE001 - límite de transporte externo
            if is_temporary_connection_error(exc):
                for event in events:
                    self.store.mark_retry(event.event_uuid, exc)
                result["retry"] = len(events)
                return result
            uploaded = {}
        acknowledged: list[str] = []
        remaining: list[SyncEvent] = []
        for event in events:
            normalized_uuid = str(uuid.UUID(event.event_uuid))
            if normalized_uuid in uploaded:
                acknowledged.append(event.event_uuid)
                result["pushed"] += 1
                continue
            remaining.append(event)
        self.store.mark_uploaded_batch(acknowledged)
        fallback_acknowledged: list[str] = []
        for event in remaining:
            try:
                self.cloud.push_event(event)
            except SyncConflict as exc:
                try:
                    remote = json.loads(str(exc))
                except ValueError:
                    remote = {}
                self.store.record_conflict(event, remote)
                result["conflicts"] += 1
            except AdmissionWriteBlocked as exc:
                self.store.record_conflict(
                    event,
                    {"reason_code": "OPERATIONAL_SESSION_REJECTED", "error": type(exc).__name__},
                )
                result["conflicts"] += 1
            except Exception as exc:
                if is_temporary_connection_error(exc):
                    self.store.mark_retry(event.event_uuid, exc)
                    result["retry"] += 1
                    break
                raise
            else:
                fallback_acknowledged.append(event.event_uuid)
                result["pushed"] += 1
        self.store.mark_uploaded_batch(fallback_acknowledged)
        return result

    def pull_cloud_changes(
        self,
        apply_remote_event: Callable[[Mapping[str, Any]], None] | None = None,
        *,
        limit: int = 200,
    ) -> int:
        bootstrapped = self._bootstrap_if_cursor_expired(batch_size=limit)
        cursor = self.store.last_cloud_cursor()
        fetch_started = perf_counter()
        events = self.cloud.events_after(cursor, limit=limit)
        fetch_ms = (perf_counter() - fetch_started) * 1000.0
        apply_started = perf_counter()
        if apply_remote_event is None:
            applied_count = self.store.apply_remote_events(events)
            self._last_pull_metrics = {
                "fetch_ms": fetch_ms,
                "apply_ms": (perf_counter() - apply_started) * 1000.0,
            }
            return bootstrapped + applied_count
        apply_event = apply_remote_event
        applied = 0
        for event in events:
            event_uuid = str(event.get("event_uuid") or "")
            sequence = int(event.get("sequence") or cursor)
            already = bool(event_uuid and self.store.already_applied(event_uuid))
            if already and not self.store.is_remote_event_materialized(event):
                self.store.discard_applied_event(event_uuid)
                already = False
                OPERATIONAL_LOG.warning(
                    "ADMISSION_SYNC_STALE_ACK_REPAIRED event_uuid=%s sequence=%s",
                    event_uuid,
                    sequence,
                )
            if event_uuid and not already:
                try:
                    apply_event(event)
                except SyncConflict as exc:
                    OPERATIONAL_LOG.warning(
                        "ADMISSION_SYNC_EVENT_APPLY_FAILED event_uuid=%s sequence=%s reason=%s",
                        event_uuid,
                        sequence,
                        type(exc).__name__,
                    )
                    break
                if not self.store.is_remote_event_materialized(event):
                    OPERATIONAL_LOG.warning(
                        "ADMISSION_SYNC_EVENT_APPLY_FAILED event_uuid=%s sequence=%s reason=NOT_MATERIALIZED",
                        event_uuid,
                        sequence,
                    )
                    break
                self.store.mark_applied_and_advance(event_uuid, sequence)
                applied += 1
                OPERATIONAL_LOG.info(
                    "ADMISSION_SYNC_EVENT_APPLIED event_uuid=%s sequence=%s",
                    event_uuid,
                    sequence,
                )
            elif already:
                self.store.set_last_cloud_cursor(sequence)
                OPERATIONAL_LOG.info(
                    "ADMISSION_SYNC_CURSOR_ADVANCED sequence=%s", sequence
                )
            else:
                OPERATIONAL_LOG.warning(
                    "ADMISSION_SYNC_EVENT_APPLY_FAILED sequence=%s reason=MISSING_EVENT_UUID",
                    sequence,
                )
                break
        self._last_pull_metrics = {
            "fetch_ms": fetch_ms,
            "apply_ms": (perf_counter() - apply_started) * 1000.0,
        }
        return bootstrapped + applied

    def reconcile_current_turn(
        self,
        *,
        operational_source_id: str,
        turn_id: int,
        force: bool = False,
        limit: int = 500,
    ) -> int:
        """Repair a replica missing projection rows for its current central turn."""
        source_id = str(operational_source_id or "").strip()
        effective_turn_id = int(turn_id or 0)
        if not source_id or effective_turn_id <= 0:
            return 0
        identity = (source_id, effective_turn_id)
        previous_identity = getattr(self, "_last_reconciled_turn_identity", None)
        if not force and previous_identity == identity:
            return 0
        OPERATIONAL_LOG.info(
            "CURRENT_TURN_RECONCILE_START source=%s turn_id=%s",
            source_id,
            effective_turn_id,
        )
        loader = getattr(self.cloud, "current_turn_attention_events", None)
        if not callable(loader):
            return 0
        events = list(
            loader(
                operational_source_id=source_id,
                turn_id=effective_turn_id,
                limit=limit,
            )
            or []
        )
        missing_before = sum(
            not self.store.is_remote_event_materialized(event) for event in events
        )
        if missing_before:
            OPERATIONAL_LOG.info(
                "CURRENT_TURN_RECONCILE_MISSING source=%s turn_id=%s count=%s",
                source_id,
                effective_turn_id,
                missing_before,
            )
        materialized = self.store.hydrate_remote_events(events) if events else 0
        unresolved = sum(
            not self.store.is_remote_event_materialized(event) for event in events
        )
        if unresolved:
            OPERATIONAL_LOG.warning(
                "CURRENT_TURN_RECONCILE_PENDING source=%s turn_id=%s unresolved=%s",
                source_id,
                effective_turn_id,
                unresolved,
            )
            return materialized
        self._last_reconciled_turn_identity = identity
        OPERATIONAL_LOG.info(
            "CURRENT_TURN_RECONCILE_DONE source=%s turn_id=%s count=%s",
            source_id,
            effective_turn_id,
            materialized,
        )
        return materialized

    def get_attention_by_global_id(
        self,
        global_attention_id: str,
        *,
        online: bool,
        force_central: bool = False,
        include_deleted: bool = True,
    ) -> dict[str, Any] | None:
        """Read-through cache: local first, central on miss/stale request."""
        local = self.store.get_attention_by_global_id(
            global_attention_id, include_deleted=include_deleted
        )
        if local is not None and not force_central:
            return local
        if not online:
            return local
        central = self.cloud.get_attention_by_global_id(
            global_attention_id, include_deleted=include_deleted
        )
        if central is None:
            return None
        event = dict(central.get("event") or {})
        if event:
            self.store.hydrate_remote_events([event])
        return self.store.get_attention_by_global_id(
            global_attention_id, include_deleted=include_deleted
        )

    def cancel_attention(
        self,
        global_attention_id: str,
        *,
        current_user: Mapping[str, Any] | Any,
        reason: str,
        operational_session: OperationalSession,
        device_id: str,
        online: bool,
    ) -> dict[str, Any] | None:
        """Canonical online-central/offline-outbox cancellation workflow."""
        if online:
            central = self.cloud.cancel_attention(
                global_attention_id,
                current_user=current_user,
                reason=reason,
                operational_session=operational_session,
                device_id=device_id,
            )
            if central is None:
                return None
            event = dict(central.get("event") or {})
            if event:
                self.store.hydrate_remote_events([event])
            return self.store.get_attention_by_global_id(
                global_attention_id, include_deleted=True
            )
        return self.store.cancel_attention_local(
            global_attention_id,
            current_user=current_user,
            reason=reason,
        )

    def bootstrap_replica(
        self,
        *,
        batch_size: int = 500,
        progress: Callable[[int], None] | None = None,
        max_batches: int = 100_000,
    ) -> int:
        """Download the retained central event history in bounded local batches."""
        total = self._bootstrap_if_cursor_expired(batch_size=batch_size)
        for _batch in range(max(1, int(max_batches))):
            cursor = self.store.last_cloud_cursor()
            events = self.cloud.events_after(cursor, limit=batch_size)
            if not events:
                break
            total += self.store.apply_remote_events(events)
            if progress is not None:
                progress(total)
            if len(events) < max(1, min(int(batch_size), 500)):
                break
        return total

    def synchronize_once(self, *, push_limit: int = 100, pull_limit: int = 200) -> dict[str, Any]:
        """Ejecuta un ciclo incremental sin recargar el historial completo."""
        cycle_started = perf_counter()
        # Remote-first prevents stale local copies from reviving cloud tombstones.
        replayed = 0
        backfilled = 0
        clock = self.store.update_server_time_offset(self.cloud.server_time())
        if not self._projection_backfill_complete:
            event_backfilled = self.cloud.backfill_projection_events(limit=pull_limit)
            payload_loader = getattr(self.cloud, "backfill_projection_payloads", None)
            payload_backfilled = (
                int(payload_loader(limit=pull_limit)) if callable(payload_loader) else 0
            )
            backfilled = event_backfilled + payload_backfilled
            self._projection_backfill_complete = bool(
                event_backfilled < max(1, min(int(pull_limit), 500))
                and payload_backfilled < max(1, min(int(pull_limit), 500))
            )
        pulled_before_push = self.pull_cloud_changes(limit=pull_limit)
        pull_fetch_ms = float(self._last_pull_metrics["fetch_ms"])
        pull_apply_ms = float(self._last_pull_metrics["apply_ms"])
        # Legacy recovery is bounded and only queues records that have a durable
        # global identity and no prior outbox event.
        recovered = self.store.queue_missing_attention_events(limit=push_limit)
        if not self._initial_reconciliation_complete:
            replayed = self.cloud.rematerialize_attention_events(
                self.store.recent_attention_entity_ids(limit=push_limit)
            )
            self._initial_reconciliation_complete = True
        push_started = perf_counter()
        pushed = self.push_outbox(limit=push_limit)
        push_ms = (perf_counter() - push_started) * 1000.0
        pulled_after_push = self.pull_cloud_changes(limit=pull_limit)
        pull_fetch_ms += float(self._last_pull_metrics["fetch_ms"])
        pull_apply_ms += float(self._last_pull_metrics["apply_ms"])
        total_ms = (perf_counter() - cycle_started) * 1000.0
        return {
            **pushed,
            "pulled": pulled_before_push + pulled_after_push,
            "recovered": recovered,
            "replayed": replayed,
            "backfilled": backfilled,
            "server_time_offset_ms": int(clock["server_time_offset_ms"]),
            "clock_drift_detected": int(bool(clock["drift_detected"])),
            "sync_push_ms": round(push_ms, 3),
            "sync_pull_ms": round(pull_fetch_ms, 3),
            "sync_apply_ms": round(pull_apply_ms, 3),
            "sync_total_ms": round(total_ms, 3),
        }


class AdmissionSeedService:
    """One-time, idempotent import of structured V15 history into central events."""

    SCHEMA_VERSION = 1

    def __init__(self, sync_service: AdmissionSyncService):
        self.sync_service = sync_service

    def seed_local_history(
        self,
        *,
        origin_device_id: str,
        batch_size: int = 200,
        progress: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        store = self.sync_service.store
        cloud = self.sync_service.cloud
        source_id = store.legacy_source_instance_id()
        total = store.local_attention_count()
        fingerprint = hashlib.sha256(
            f"{source_id}:v{self.SCHEMA_VERSION}".encode()
        ).hexdigest()
        seed_id = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"hospital-admission-central-seed:{fingerprint}",
            )
        )
        should_run = cloud.begin_seed(
            central_seed_id=seed_id,
            legacy_source_instance_id=source_id,
            source_fingerprint=fingerprint,
            origin_device_id=str(origin_device_id),
            schema_version=self.SCHEMA_VERSION,
        )
        if not should_run:
            return {"central_seed_id": seed_id, "imported": 0, "already_completed": True}
        store.prepare_seed_resume(seed_id)
        imported = 0
        while True:
            queued = store.queue_missing_attention_events(
                limit=batch_size,
                central_seed_id=seed_id,
            )
            result = self.sync_service.push_outbox(limit=batch_size)
            imported = store.seed_entity_count(seed_id)
            if progress is not None:
                progress(imported, total)
            if queued == 0 and store.pending_count() == 0:
                break
            if int(result.get("retry") or 0) or int(result.get("conflicts") or 0):
                raise AdmissionHybridError(
                    "El seed central quedó pendiente por conectividad o conflicto."
                )
        cloud.complete_seed(central_seed_id=seed_id, imported_records=total)
        store.record_seed_state(
            central_seed_id=seed_id,
            legacy_source_instance_id=source_id,
            status="COMPLETED",
            imported_records=total,
            schema_version=self.SCHEMA_VERSION,
        )
        return {"central_seed_id": seed_id, "imported": total, "already_completed": False}


def evaluate_attention_billing_eligibility(
    attention: Mapping[str, Any] | None, user_context: Mapping[str, Any] | None = None,
    *, ars_billing_enabled: bool | None = None, receipt: Mapping[str, Any] | None = None,
    in_operational_scope: bool = True,
) -> dict[str, Any]:
    """La \u00fanica regla de elegibilidad. La c\u00e9dula nunca es una condici\u00f3n."""
    data = dict(attention or {})
    attention_id = data.get("global_attention_id") or data.get("attention_id") or data.get("id")
    result = {
        "eligible": False, "reason_code": "INVALID_ATTENTION", "reason": "La atenci\u00f3n no existe.",
        "billing_status": "SIN_RECIBO", "receipt_id": None, "attention_id": attention_id,
        "origin": data.get("operational_source_id") or data.get("source_instance_id") or "",
        "turn_id": data.get("turn_id"),
    }
    if not attention_id:
        return result
    status = str(data.get("source_status") or data.get("estado") or "ACTIVA").strip().upper()
    if status not in {"ACTIVA", "PENDIENTE"}:
        result.update(reason_code="CANCELLED",reason="La atenci\u00f3n est\u00e1 anulada o inactiva.",billing_status=status)
        return result
    if not in_operational_scope:
        result.update(reason_code="NOT_IN_OPERATIONAL_SCOPE",reason="La atenci\u00f3n no pertenece al turno autorizado.")
        return result
    service_type = str(data.get("service_type") or data.get("attention_type") or "EMERGENCIA").strip().upper()
    if service_type != "EMERGENCIA":
        result.update(reason_code="INVALID_ATTENTION",reason="Solo las atenciones de Emergencia pueden facturarse en este flujo.")
        return result
    ars = str(data.get("canonical_ars") or data.get("ars") or "").strip()
    normalized_ars = "".join(ch for ch in ars.upper() if ch.isalnum())
    if not ars or normalized_ars in {"SINSEGURO", "NINGUNO"}:
        result.update(reason_code="ARS_NOT_BILLABLE",reason="La atenci\u00f3n no tiene una cobertura facturable.")
        return result
    if normalized_ars.startswith("SENASASUBSIDIADO"):
        result.update(reason_code="SUBSIDIZED_EXCLUDED",reason="SENASA SUBSIDIADO no es facturable en este flujo.")
        return result
    if ars_billing_enabled is False or data.get("billing_enabled") is False:
        result.update(reason_code="ARS_NOT_BILLABLE",reason="La ARS no est\u00e1 habilitada para facturaci\u00f3n.")
        return result
    receipt_data = dict(receipt or {})
    if receipt_data:
        billing_status = str(receipt_data.get("estado_facturacion") or receipt_data.get("billing_status") or "PENDIENTE").upper()
        result.update(receipt_id=receipt_data.get("id") or receipt_data.get("receipt_id"),billing_status=billing_status)
        if billing_status in {"COMPLETO", "FACTURADO", "FINAL"} or str(receipt_data.get("estado_documento") or "").upper() == "FINAL":
            result.update(reason_code="ALREADY_BILLED",reason="Esta atenci\u00f3n ya fue facturada completamente.")
            return result
    role = str((user_context or {}).get("role") or "").casefold()
    if role in {"", "auxiliar"} and bool(data.get("role_restricted")):
        result.update(reason_code="NOT_ALLOWED_FOR_ROLE",reason="Tu rol no puede facturar esta atenci\u00f3n.")
        return result
    result.update(eligible=True,reason_code="ELIGIBLE_PENDING",reason="Atenci\u00f3n disponible para facturaci\u00f3n.",billing_status=result["billing_status"] or "PENDIENTE")
    return result


__all__ = [
    "ADMISSION_IMPORT_BATCH_COLUMNS",
    "ADMISSION_IMPORT_PROGRESS_MIGRATION_ID",
    "ADMISSION_IMPORT_STAGING_COLUMNS",
    "ADMISSION_ROLE_ADMINISTRATOR",
    "ADMISSION_ROLE_AUDIT",
    "ADMISSION_ROLE_AUXILIARY",
    "MAX_ACTIVE_SESSION_DEVICES",
    "SYNC_TICK_SECONDS",
    "AdmissionAccessDecision",
    "AdmissionCloudRepository",
    "AdmissionHybridError",
    "AdmissionIdentity",
    "AdmissionSeedService",
    "AdmissionSyncService",
    "AdmissionWriteBlocked",
    "AdmissionWriteGuard",
    "ConnectionSupervisor",
    "ConnectivityState",
    "DatabaseConfigurationMissing",
    "DatabaseTemporarilyOffline",
    "DeviceAttachment",
    "OfflineAdmissionStore",
    "OperationalSession",
    "OperationalSessionService",
    "StationRole",
    "SyncConflict",
    "SyncEvent",
    "WriteDecision",
    "build_admission_order_key",
    "canonical_role",
    "connection_state_from_error",
    "deterministic_event_order_key",
    "ensure_admission_import_progress_schema",
    "evaluate_admission_access",
    "evaluate_attention_billing_eligibility",
    "install_central_hybrid_schema",
    "is_temporary_connection_error",
    "select_effective_turn_interval",
    "user_can_be_assigned_admission_operator",
    "user_can_operate_admission",
]
