"""Session and role contract shared with the Billing launcher.

Only non-secret identity metadata is accepted.  Admission deliberately starts
with the least privileged role when it is opened outside Billing.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import re
from typing import Mapping


ROLE_AUXILIARY = "auxiliar"
ROLE_BILLING_AUDIT = "facturador de auditoria"
ROLE_MEDICAL_AUDIT = "auditoria medica y cuentas"
ROLE_ADMIN = "administrador"

CAP_VIEW_REPORTS = "reports.view"
CAP_OPEN_EXCEL = "excel.open"
CAP_EDIT_PATIENT = "patients.edit"
CAP_EDIT_RECORDS = "records.edit"
CAP_VOID_RECORDS = "records.void"
CAP_INTERNAL_CONFIG = "configuration.manage"

_KNOWN_ROLES = {
    ROLE_AUXILIARY,
    ROLE_BILLING_AUDIT,
    ROLE_MEDICAL_AUDIT,
    ROLE_ADMIN,
}

_OPERATIONAL_CAPABILITIES = frozenset(
    {
        CAP_VIEW_REPORTS,
        CAP_OPEN_EXCEL,
        CAP_EDIT_PATIENT,
        CAP_EDIT_RECORDS,
        CAP_VOID_RECORDS,
        CAP_INTERNAL_CONFIG,
    }
)

_ROLE_CAPABILITIES = {
    ROLE_ADMIN: _OPERATIONAL_CAPABILITIES,
    ROLE_AUXILIARY: frozenset(
        {
            CAP_VIEW_REPORTS,
            CAP_OPEN_EXCEL,
            CAP_EDIT_PATIENT,
            CAP_EDIT_RECORDS,
            CAP_VOID_RECORDS,
        }
    ),
    ROLE_BILLING_AUDIT: frozenset({CAP_VIEW_REPORTS, CAP_OPEN_EXCEL, CAP_EDIT_PATIENT}),
    ROLE_MEDICAL_AUDIT: frozenset({CAP_VIEW_REPORTS, CAP_OPEN_EXCEL, CAP_EDIT_PATIENT}),
}


def _clean(value: object, *, maximum: int) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    return text[:maximum]


def normalize_role(value: object) -> str:
    role = _clean(value, maximum=80).casefold()
    return role if role in _KNOWN_ROLES else ROLE_AUXILIARY


@dataclass(frozen=True)
class AdmissionSessionContext:
    username: str
    full_name: str
    role: str
    session_id: str
    launched_from_billing: bool

    @property
    def display_name(self) -> str:
        return self.full_name or self.username or "Auxiliar local"

    @property
    def audit_actor(self) -> str:
        return self.username or self.display_name

    def allows(self, capability: str) -> bool:
        return capability in _ROLE_CAPABILITIES[self.role]


def load_session_context(
    env: Mapping[str, str] | None = None,
) -> AdmissionSessionContext:
    values = os.environ if env is None else env
    launched = _clean(
        values.get("HOSPITAL_LAUNCHED_FROM_BILLING", ""), maximum=8
    ).casefold() in {"1", "true", "yes", "si", "sí"}

    # A direct launch must never inherit administrative behavior by default.
    role = normalize_role(values.get("HOSPITAL_ROLE", ""))
    if not launched:
        role = ROLE_AUXILIARY

    return AdmissionSessionContext(
        username=_clean(values.get("HOSPITAL_USERNAME", ""), maximum=80),
        full_name=_clean(values.get("HOSPITAL_FULL_NAME", ""), maximum=160),
        role=role,
        session_id=_clean(values.get("HOSPITAL_SESSION_ID", ""), maximum=160),
        launched_from_billing=launched,
    )
