"""Stable nominal contracts for backwards-compatible historical documents.

This module is deliberately independent from the UI and the database layer.  It
normalizes records returned by different schema generations and materializes an
already-stored PDF blob without attempting to recreate historical business data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        return dict(value or {})
    except (TypeError, ValueError):
        return {}


def _integer(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class NormalizedReceiptRecord:
    receipt_id: int
    receipt_number: int | None = None
    patient: str = ""
    attention_id: int | None = None
    generated_at: str = ""
    service_date: str = ""
    ars: str = ""
    authorization: str = ""
    generated_by: str = ""
    billing_status: str = ""
    storage_mode: str = ""
    pdf_filename: str = ""
    legacy_backup_reference: str = ""
    snapshot_version: int | None = None
    snapshot_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_receipt_record(value: Any) -> NormalizedReceiptRecord:
    row = _mapping(value)
    receipt_id = _integer(row.get("receipt_id") or row.get("id"))
    if receipt_id is None or receipt_id <= 0:
        raise ValueError("El registro de recibo no contiene un identificador válido.")
    snapshot = row.get("snapshot_data") or row.get("snapshot_jsonb") or {}
    return NormalizedReceiptRecord(
        receipt_id=receipt_id,
        receipt_number=_integer(row.get("receipt_number") or row.get("numero")),
        patient=str(row.get("patient") or row.get("nombre") or ""),
        attention_id=_integer(
            row.get("attention_id") or row.get("admission_atencion_id")
        ),
        generated_at=str(row.get("generated_at") or row.get("created_at") or ""),
        service_date=str(row.get("service_date") or row.get("fecha") or ""),
        ars=str(row.get("ars") or ""),
        authorization=str(
            row.get("authorization") or row.get("numero_autorizacion") or ""
        ),
        generated_by=str(row.get("generated_by") or row.get("username") or ""),
        billing_status=str(row.get("billing_status") or row.get("estado_facturacion") or ""),
        storage_mode=str(row.get("storage_mode") or row.get("document_storage_mode") or ""),
        pdf_filename=os.path.basename(str(row.get("pdf_filename") or "")),
        legacy_backup_reference=str(row.get("legacy_backup_reference") or row.get("legacy_pdf_backup_reference") or ""),
        snapshot_version=_integer(row.get("snapshot_version") or row.get("version")),
        snapshot_data=dict(snapshot) if isinstance(snapshot, Mapping) else {},
        metadata=row,
    )


@dataclass(frozen=True, slots=True)
class NormalizedReportRecord:
    report_id: int | None
    source_table: str
    source_key: str
    report_type: str = ""
    period_type: str = ""
    period_label: str = ""
    date_from: str = ""
    date_to: str = ""
    generated_at: str = ""
    generated_by: str = ""
    storage_mode: str = ""
    pdf_filename: str = ""
    snapshot_version: int | None = None
    snapshot_data: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def normalize_report_record(value: Any) -> NormalizedReportRecord:
    row = _mapping(value)
    table = str(row.get("source_table") or row.get("source") or "").strip()
    report_id = _integer(row.get("report_id") or row.get("record_id") or row.get("id"))
    key = str(row.get("source_key") or "").strip()
    if not key and report_id is not None:
        key = str(report_id)
    if not table:
        raise ValueError("El registro de reporte no identifica su fuente.")
    if not key:
        raise ValueError("El registro de reporte no contiene una clave documental.")
    snapshot = row.get("snapshot_data") or row.get("snapshot_jsonb") or {}
    return NormalizedReportRecord(
        report_id=report_id,
        source_table=table,
        source_key=key,
        report_type=str(row.get("report_type") or ""),
        period_type=str(row.get("period_type") or ""),
        period_label=str(row.get("period_label") or ""),
        date_from=str(row.get("date_from") or row.get("start_date") or ""),
        date_to=str(row.get("date_to") or row.get("end_date") or ""),
        generated_at=str(row.get("generated_at") or ""),
        generated_by=str(row.get("generated_by") or ""),
        storage_mode=str(row.get("storage_mode") or row.get("document_storage_mode") or ""),
        pdf_filename=os.path.basename(str(row.get("pdf_filename") or row.get("filename") or row.get("filepath") or "")),
        snapshot_version=_integer(row.get("snapshot_version") or row.get("version")),
        snapshot_data=dict(snapshot) if isinstance(snapshot, Mapping) else {},
        metadata=row,
    )


def parse_shift_closure_source_key(value: Any) -> tuple[str, int]:
    text = str(value or "").strip()
    if "|" not in text:
        raise ValueError("La clave del cierre de turno no incluye su instancia de origen.")
    instance_id, turn_value = text.rsplit("|", 1)
    turn_id = _integer(turn_value)
    if not instance_id.strip() or turn_id is None or turn_id <= 0:
        raise ValueError("La clave del cierre de turno está incompleta.")
    return instance_id.strip(), turn_id


def materialize_pdf_blob(
    blob: Any,
    cache_root: str | os.PathLike[str],
    *,
    namespace: str,
    identity: str,
) -> str:
    """Persist an existing PDF blob into an application cache atomically."""
    if isinstance(blob, memoryview):
        data = blob.tobytes()
    elif isinstance(blob, bytearray):
        data = bytes(blob)
    elif isinstance(blob, bytes):
        data = blob
    else:
        raise ValueError("El documento histórico almacenado no contiene datos binarios válidos.")
    if len(data) < 5 or not data.startswith(b"%PDF-"):
        raise ValueError("El documento histórico almacenado no es un PDF válido.")
    digest = hashlib.sha256(data).hexdigest()
    safe_identity = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(identity))
    root = Path(cache_root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{namespace}_{safe_identity}_{digest[:16]}.pdf"
    if target.is_file() and target.stat().st_size == len(data):
        return str(target)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(data)
    os.replace(str(temporary), str(target))
    return str(target)
