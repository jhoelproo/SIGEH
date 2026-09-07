"""Recover clinical specialty from projection or durable admission payload."""

import json
from collections.abc import Mapping


def _explicit_specialty(row: Mapping) -> str:
    for key in ("specialty", "hoja_normalizada", "hoja", "detail_sheet"):
        value = str(row.get(key) or "").strip().upper()
        if value and value not in {"GENERADA", "SIN ESPECIALIDAD"}:
            return value
    return ""


def resolve_specialty(row: Mapping) -> str:
    direct = _explicit_specialty(row)
    if direct:
        return direct
    payload = row.get("latest_payload_json") or row.get("latest_payload") or {}
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return ""
    return _explicit_specialty(payload) if isinstance(payload, Mapping) else ""


def with_resolved_specialty(row: Mapping) -> dict:
    result = dict(row)
    specialty = resolve_specialty(row)
    result.update(specialty=specialty, hoja_normalizada=specialty)
    if row.get("has_detail_sheet"):
        result["hoja"] = specialty
    return result
