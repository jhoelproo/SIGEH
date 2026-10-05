"""Atomic removal of list membership; receipts and admissions remain untouched."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any


def _positive_id(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Identificador de paciente inválido.")
    try:
        identifier = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Identificador de paciente inválido.") from exc
    if identifier <= 0 or str(value).strip() != str(identifier):
        raise ValueError("Identificador de paciente inválido.")
    return identifier


def _entry_identity(
    entry: Mapping[str, Any],
) -> tuple[int | None, str | None, int | None]:
    receipt_id = entry.get("recibo_id")
    if receipt_id is not None:
        return _positive_id(receipt_id), None, None
    source_id = str(entry.get("admission_source_instance_id") or "LEGACY").strip()
    attention_id = _positive_id(entry.get("admission_attention_id"))
    if not source_id:
        raise ValueError("La atención no tiene un origen válido.")
    return None, source_id, attention_id


def _record_removal(
    connection: Any,
    batch_id: int,
    entry: Mapping[str, Any],
    username: str,
    stamp: str,
    reason: str,
) -> None:
    receipt_id = entry["recibo_id"]
    event_type = "RECIBO_RETIRADO" if receipt_id is not None else "ADMISION_RETIRADA"
    details = (
        reason
        if receipt_id is not None
        else json.dumps(
            {
                "source_instance_id": entry["admission_source_instance_id"],
                "attention_id": entry["admission_attention_id"],
                "reason": reason,
            },
            ensure_ascii=False,
        )
    )
    connection.execute(
        """INSERT INTO billing_batch_events(
               batch_id,recibo_id,event_type,performed_at,performed_by,details
           ) VALUES(%s,%s,%s,%s,%s,%s)""",
        (batch_id, receipt_id, event_type, stamp, username, details),
    )


def _resolve_entries(
    connection: Any,
    batch_id: int,
    identities: Iterable[tuple[int | None, str | None, int | None]],
) -> dict[int, Mapping[str, Any]]:
    resolved: dict[int, Mapping[str, Any]] = {}
    for identity in identities:
        entry = connection.execute(
            """SELECT id,recibo_id,admission_source_instance_id,admission_attention_id
               FROM billing_batch_receipts WHERE batch_id=%s AND included=1
                 AND (recibo_id=%s OR (admission_source_instance_id=%s
                                      AND admission_attention_id=%s)) FOR UPDATE""",
            (batch_id, *identity),
        ).fetchone()
        if not entry:
            raise ValueError("Un paciente ya no está incluido. Actualiza el listado.")
        resolved[entry["id"]] = entry
    return resolved


def remove_batch_entries(
    connection: Any,
    batch_id: int,
    entries: Iterable[Mapping[str, Any]],
    *,
    username: str,
    stamp: str,
    reason: str,
    is_editable: Callable[[Any], bool],
) -> int:
    """Use the caller's transaction to remove all selections or roll back all."""
    batch_id = _positive_id(batch_id)
    reason = str(reason or "").strip()
    if not reason:
        raise ValueError("Indica por qué se retiran los pacientes del listado.")
    identities = dict.fromkeys(_entry_identity(entry) for entry in entries)
    if not identities:
        raise ValueError("Selecciona al menos un paciente.")
    batch = connection.execute(
        "SELECT status FROM billing_batches WHERE id=%s FOR UPDATE", (batch_id,)
    ).fetchone()
    if not batch or not is_editable(batch["status"]):
        raise ValueError("El listado no está disponible para edición.")
    resolved = _resolve_entries(connection, batch_id, identities)
    updated = connection.execute(
        """UPDATE billing_batch_receipts
           SET included=0,removed_at=%s,removed_by=%s,removal_reason=%s
           WHERE batch_id=%s AND id=ANY(%s) AND included=1""",
        (stamp, username, reason, batch_id, list(resolved)),
    )
    if updated.rowcount != len(resolved):
        raise ValueError(
            "Cambió la selección. Actualiza el listado e intenta nuevamente."
        )
    for entry in resolved.values():
        _record_removal(connection, batch_id, entry, username, stamp, reason)
    connection.execute(
        "UPDATE billing_batches SET updated_at=%s,updated_by=%s WHERE id=%s",
        (stamp, username, batch_id),
    )
    return len(resolved)
