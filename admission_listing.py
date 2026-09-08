"""Shared classification for the operational list and its counters."""

from collections.abc import Mapping

from admission_specialty import resolve_specialty


def attention_type(row: Mapping) -> str:
    return (
        str(row.get("tipo_atencion") or row.get("service_type") or "EMERGENCIA")
        .strip()
        .upper()
    )


def listing_classification(row: Mapping) -> str:
    kind = attention_type(row)
    return resolve_specialty(row) if kind == "EMERGENCIA" else kind
