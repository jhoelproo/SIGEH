from __future__ import annotations

from dataclasses import dataclass, field
import logging
import re
import unicodedata
from typing import Any, Callable, Mapping

from PySide6.QtCore import QObject, Signal


def canonical_role(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").strip().casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = re.sub(r"\s+", " ", text)
    aliases = {
        "admin": "administrador",
        "administrator": "administrador",
        "auditoria": "facturador de auditoria",
        "auditor": "facturador de auditoria",
        "medico auditor": "auditoria medica y cuentas",
    }
    return aliases.get(text, text or "auxiliar")


class SharedEventBus(QObject):
    attention_created = Signal(object)
    attention_updated = Signal(object)
    attention_cancelled = Signal(object)
    detail_sheet_generated = Signal(object)
    shift_changed = Signal(object)
    shift_closed = Signal(object)
    history_refresh_requested = Signal()


@dataclass(slots=True)
class AppContext:
    """Contexto recibido desde la aplicación principal; no crea sesión ni BD."""

    connection_factory: Callable[[], Any]
    user: Mapping[str, Any]
    session_id: str
    device_id: str
    device_name: str = ""
    current_shift: Mapping[str, Any] | None = None
    permission_resolver: Callable[[Mapping[str, Any], str], bool] | None = None
    configuration: Mapping[str, Any] = field(default_factory=dict)
    operational_session: Any | None = None
    station_role: Any = "NONE"
    write_guard: Any | None = None
    offline: bool = False
    offline_lease_valid: bool = False
    sync_store: Any | None = None
    sync_service: Any | None = None
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("hospital.admission")
    )
    event_bus: SharedEventBus | Any | None = field(default_factory=SharedEventBus)

    def __post_init__(self) -> None:
        if not callable(self.connection_factory):
            raise TypeError("connection_factory debe reutilizar el proveedor central.")
        if not str(self.session_id or "").strip():
            raise ValueError("AppContext requiere la sesión principal vigente.")
        if not str(self.device_id or "").strip():
            raise ValueError("AppContext requiere el dispositivo principal vigente.")
        self.user = dict(self.user or {})
        self.configuration = dict(self.configuration or {})
        self.current_shift = dict(self.current_shift or {})
        if self.event_bus is None:
            self.event_bus = SharedEventBus()

    @property
    def username(self) -> str:
        return str(self.user.get("username") or "").strip()

    @property
    def user_id(self) -> Any:
        return self.user.get("id", self.user.get("user_id"))

    @property
    def role(self) -> str:
        return canonical_role(self.user.get("role"))

    def has_permission(self, permission: str) -> bool:
        if self.permission_resolver is None:
            permissions = self.user.get("permissions") or ()
            return str(permission) in set(permissions)
        return bool(self.permission_resolver(self.user, str(permission)))

    def connection(self) -> Any:
        """Obtiene una conexión del mismo pool central suministrado por la app."""
        return self.connection_factory()

    def set_shift(self, shift: Mapping[str, Any] | None) -> None:
        self.current_shift = dict(shift or {})
        self.event_bus.shift_changed.emit(dict(self.current_shift))

    def can_write_admission(self):
        """Consulta el guardia también desde el servicio, no solo desde la UI."""
        if self.write_guard is None:
            return None
        generation = getattr(self.operational_session, "generation", None)
        return self.write_guard.can_write_admission(
            login_user=self.username,
            device_id=self.device_id,
            session=self.operational_session,
            generation=generation,
            role=self.station_role,
            offline=bool(self.offline),
            offline_lease_valid=bool(self.offline_lease_valid),
        )
