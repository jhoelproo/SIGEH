"""External context contract for the embeddable Admisión V15 widget."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any, Callable, Mapping


@dataclass(frozen=True, slots=True)
class ExternalAdmissionSession:
    username: str
    full_name: str
    role: str
    session_id: str
    launched_from_billing: bool = True
    permission_resolver: Callable[[str], bool] | None = field(
        default=None, repr=False, compare=False
    )

    @property
    def display_name(self) -> str:
        return self.full_name or self.username or "Usuario"

    @property
    def audit_actor(self) -> str:
        return self.username or self.display_name

    def allows(self, capability: str) -> bool:
        if self.permission_resolver is None:
            return False
        return bool(self.permission_resolver(str(capability)))


@dataclass(slots=True)
class AdmissionContext:
    """Dependencies supplied by the host; it never authenticates or opens a pool."""

    connection_factory: Callable[[], Any] | None
    user: Mapping[str, Any]
    session_id: str
    device_id: str
    admission_database_factory: Callable[[Any], Any]
    main_app_gateway: Any
    device_name: str = ""
    configuration: Mapping[str, Any] = field(default_factory=dict)
    current_shift: Mapping[str, Any] | None = None
    logger: logging.Logger = field(
        default_factory=lambda: logging.getLogger("hospital.admission.v15")
    )
    event_bus: Any = None
    permission_resolver: Callable[[str], bool] | None = None
    session_context: Any = None
    embedded: bool = True

    def __post_init__(self):
        self.user = dict(self.user or {})
        self.configuration = dict(self.configuration or {})
        self.current_shift = dict(self.current_shift or {})
        self.session_id = str(self.session_id or "").strip()
        self.device_id = str(self.device_id or "").strip()
        self.device_name = str(self.device_name or "").strip()

        if not callable(self.admission_database_factory):
            raise TypeError("AdmissionContext requiere admission_database_factory.")
        if self.main_app_gateway is None:
            raise ValueError("AdmissionContext requiere el gateway del sistema principal.")
        if self.embedded:
            if not callable(self.connection_factory):
                raise TypeError("AdmissionContext requiere la connection_factory central.")
            if not self.session_id:
                raise ValueError("AdmissionContext requiere la sesión principal vigente.")
            if not self.device_id:
                raise ValueError("AdmissionContext requiere el device_id principal.")

        if self.session_context is None:
            self.session_context = ExternalAdmissionSession(
                username=self.username,
                full_name=self.full_name,
                role=self.role,
                session_id=self.session_id,
                launched_from_billing=self.embedded,
                permission_resolver=self.permission_resolver,
            )

    @property
    def username(self) -> str:
        return str(self.user.get("username") or "").strip()

    @property
    def user_id(self) -> Any:
        return self.user.get("id", self.user.get("user_id"))

    @property
    def full_name(self) -> str:
        return str(
            self.user.get("full_name")
            or self.user.get("nombre_completo")
            or self.username
        ).strip()

    @property
    def role(self) -> str:
        return str(self.user.get("role") or "auxiliar").strip().casefold()

    def connection(self) -> Any:
        if not callable(self.connection_factory):
            raise RuntimeError("El modo standalone no dispone de conexión central.")
        return self.connection_factory()

    def create_admission_database(self) -> Any:
        return self.admission_database_factory(self.session_context)


def create_standalone_context(
    *,
    session_context: Any,
    main_app_gateway: Any,
    admission_database_factory: Callable[[Any], Any],
    logger: logging.Logger | None = None,
) -> AdmissionContext:
    """Build local development context only from the standalone wrapper."""
    return AdmissionContext(
        connection_factory=None,
        user={
            "id": None,
            "username": getattr(session_context, "username", ""),
            "full_name": getattr(session_context, "full_name", ""),
            "role": getattr(session_context, "role", "auxiliar"),
        },
        session_id=getattr(session_context, "session_id", ""),
        device_id="standalone",
        admission_database_factory=admission_database_factory,
        main_app_gateway=main_app_gateway,
        logger=logger or logging.getLogger("hospital.admission.v15.standalone"),
        session_context=session_context,
        embedded=False,
    )


__all__ = ["AdmissionContext", "ExternalAdmissionSession", "create_standalone_context"]
