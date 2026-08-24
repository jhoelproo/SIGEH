from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping


class AdmissionFormState(str, Enum):
    NEW = "NEW"
    EDITING = "EDITING"
    SAVING = "SAVING"
    READ_ONLY = "READ_ONLY"
    ERROR = "ERROR"


@dataclass(slots=True)
class AdmissionInput:
    name: str = ""
    age: int | None = None
    age_unit: str = "Años"
    sex: str = "Femenino"
    attention_type: str = "EMERGENCIA"
    cedula: str = ""
    phone: str = ""
    address: str = ""
    nationality: str = "Dominicana"
    ars: str = ""
    nss: str = ""
    specialty: str = "GENERAL"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "AdmissionInput":
        known = {field.name for field in cls.__dataclass_fields__.values()}
        values = {key: item for key, item in dict(value or {}).items() if key in known}
        return cls(**values)

    def as_mapping(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AdmissionResult:
    ok: bool
    state: AdmissionFormState
    data: Any = None
    message: str = ""
    errors: tuple[str, ...] = ()
