"""Local outbox events consumed read-only by the Billing application."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid


EVENT_READY = "ATENCION_LISTA_FACTURACION"
EVENT_CORRECTION = "ATENCION_REQUIERE_CORRECCION"
STATUS_READY = "LISTO_PARA_FACTURAR"
STATUS_INCOMPLETE = "INCOMPLETO"

_UNINSURED = {"SINSEGURO", "NS", "NO", "NOTIENE", "NINGUNO", "NINGUNA", "NA", "SN"}
_PENDING_ARS = {"", "PEND", "PENDIENTE", "INACTIVO", "INACTIVA", "NOVIGENTE", "VENCIDO"}


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _key(value: object) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").strip().upper())


def _source_instance_id(connection: sqlite3.Connection) -> str:
    row = connection.execute(
        "SELECT valor FROM app_metadata WHERE clave='integration.source_instance_id'"
    ).fetchone()
    return str(row[0] or "") if row else ""


def build_attention_event_ref(
    connection: sqlite3.Connection,
    *,
    attention_id: int,
    event_type: str,
    event_uuid: str = "",
) -> dict:
    """Build the ID-only contract emitted after the local commit succeeds."""
    return {
        "event_uuid": str(event_uuid or ""),
        "source_instance_id": _source_instance_id(connection),
        "attention_id": int(attention_id),
        "event_type": str(event_type or ""),
    }


def build_shift_event_ref(
    connection: sqlite3.Connection,
    *,
    turn_id: int,
    event_uuid: str = "",
    closed_at: str = "",
) -> dict:
    """Build the documented shift reference without transporting UI state."""
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT t.id,t.dia_operativo_id,t.tipo_turno,t.representante,
                  d.fecha_base
           FROM turnos t
           JOIN dias_operativos d ON d.id=t.dia_operativo_id
           WHERE t.id=? LIMIT 1""",
        (int(turn_id),),
    ).fetchone()
    if not row:
        raise ValueError("El turno no existe para crear el evento de integración")
    return {
        "event_uuid": str(event_uuid or ""),
        "source_instance_id": _source_instance_id(connection),
        "turn_id": int(row["id"]),
        "operational_day_id": int(row["dia_operativo_id"]),
        "operational_date": str(row["fecha_base"] or ""),
        "shift_type": str(row["tipo_turno"] or ""),
        "representative": str(row["representante"] or ""),
        "closed_at": str(closed_at or ""),
    }


def billing_missing_fields(attention: dict) -> tuple[str, ...]:
    """Return specific fields Admission must correct before Billing."""
    missing: list[str] = []
    if not str(attention.get("nombre") or "").strip():
        missing.append("nombre")
    if str(attention.get("tipo_atencion") or "").strip().upper() != "EMERGENCIA":
        missing.append("tipo de atención")

    ars_key = _key(attention.get("ars"))
    uninsured = ars_key in _UNINSURED
    if not uninsured:
        if ars_key in _PENDING_ARS:
            missing.append("ARS")
        nss = _digits(attention.get("nss"))
        if len(nss) < 6 or set(nss) == {"0"}:
            missing.append("NSS")
        cedula = _digits(attention.get("cedula"))
        if len(cedula) != 11 or cedula == "00000000000":
            missing.append("cédula")
    return tuple(missing)


def enqueue_billing_event(
    connection: sqlite3.Connection,
    *,
    attention_id: int,
    actor: str,
    actor_role: str,
    session_id: str,
) -> dict:
    connection.row_factory = sqlite3.Row
    attention_row = connection.execute(
        "SELECT * FROM atenciones WHERE id=?", (int(attention_id),)
    ).fetchone()
    if not attention_row:
        raise ValueError("La atención no existe para crear el evento de Facturación")
    attention = dict(attention_row)
    missing = billing_missing_fields(attention)
    status = STATUS_INCOMPLETE if missing else STATUS_READY
    event_type = EVENT_CORRECTION if missing else EVENT_READY
    source_instance_id = _source_instance_id(connection)
    event_uuid = str(uuid.uuid4())
    connection.execute(
        """
        INSERT INTO integracion_eventos(
            event_uuid,source_instance_id,atencion_id,tipo,estado_flujo,
            campos_faltantes_json,actor,actor_rol,session_id
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            event_uuid,
            source_instance_id,
            int(attention_id),
            event_type,
            status,
            json.dumps(missing, ensure_ascii=False),
            str(actor or "")[:160],
            str(actor_role or "")[:80],
            str(session_id or "")[:160],
        ),
    )
    return {
        "event_uuid": event_uuid,
        "source_instance_id": source_instance_id,
        "attention_id": int(attention_id),
        "event_type": event_type,
        "workflow_status": status,
        "missing_fields": missing,
    }


def enqueue_shift_closure_event(
    connection: sqlite3.Connection,
    *,
    shift: dict,
    closed_at: str,
    actor: str,
    actor_role: str,
    session_id: str,
) -> dict:
    """Persist one durable closing event for a completed Admission shift."""
    source_instance_id = _source_instance_id(connection)
    event_uuid = str(uuid.uuid4())
    values = (
        event_uuid,
        source_instance_id,
        int(shift["turno_id"]),
        int(shift["dia_operativo_id"]),
        str(shift.get("fecha_base") or ""),
        str(shift.get("fecha_inicio") or ""),
        str(shift.get("fecha_fin") or ""),
        str(closed_at or ""),
        str(shift.get("representante") or actor or "")[:160],
        str(shift.get("tipo_turno") or "")[:80],
        str(actor or "")[:160],
        str(actor_role or "")[:80],
        str(session_id or "")[:160],
    )
    connection.execute(
        """INSERT INTO turno_cierre_eventos(
               event_uuid,source_instance_id,turno_id,dia_operativo_id,
               fecha_base,fecha_inicio,fecha_fin_programada,fecha_cierre_real,
               representante,tipo_turno,actor,actor_rol,session_id
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source_instance_id,turno_id) DO NOTHING""",
        values,
    )
    reference = build_shift_event_ref(
        connection,
        turn_id=int(shift["turno_id"]),
        event_uuid=event_uuid,
        closed_at=closed_at,
    )
    reference["source_instance_id"] = source_instance_id
    return reference
