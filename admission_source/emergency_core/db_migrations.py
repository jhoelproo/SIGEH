"""Copy-based SQLite schema migration for clinical data."""

from __future__ import annotations

import json
import os
import re
import socket
import sqlite3
import uuid
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from .backup import BackupManager

LATEST_SCHEMA_VERSION = 19


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def valid_nss(value: Any) -> bool:
    cleaned = _digits(value)
    return len(cleaned) >= 6 and set(cleaned) != {"0"}


def valid_cedula(value: Any) -> bool:
    cleaned = _digits(value)
    return len(cleaned) == 11 and cleaned != "00000000000"


def parse_clinical_datetime(fecha: Any, hora: Any, created_at: Any = None) -> datetime:
    raw = f"{str(fecha or '').strip()} {str(hora or '').strip()}".strip()
    for fmt in ("%d/%m/%Y %I:%M %p", "%d/%m/%Y %H:%M", "%d/%m/%Y %I:%M:%S %p"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    created = str(created_at or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(created[:19], fmt)
        except ValueError:
            pass
    raise ValueError(f"Fecha/hora clinica no interpretable: {raw!r}")


def operational_base_day(moment: datetime) -> date:
    return (moment - timedelta(days=1)).date() if moment.time() < time(8, 0) else moment.date()


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(
    conn: sqlite3.Connection,
    table: str,
    definitions: tuple[tuple[str, str], ...],
) -> None:
    existing = _columns(conn, table)
    for column, definition in definitions:
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    if not _table_exists(conn, table):
        return []
    conn.row_factory = sqlite3.Row
    return [dict(row) for row in conn.execute(f"SELECT rowid AS _rowid_, * FROM {table}")]


def _execute_schema_statements(conn: sqlite3.Connection, script: str) -> None:
    """Execute this schema without ``executescript``'s implicit COMMIT.

    The schema intentionally contains only ordinary DDL statements (no
    triggers), so splitting at statement terminators is safe and preserves the
    transaction opened by the migration caller.
    """
    for raw_statement in script.split(";"):
        statement = raw_statement.strip()
        if statement:
            conn.execute(statement)


def create_latest_schema(conn: sqlite3.Connection) -> None:
    _execute_schema_statements(
        conn,
        """
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS pacientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL,
            cedula TEXT,
            telefono TEXT,
            direccion TEXT,
            nacionalidad TEXT,
            ars TEXT,
            nss TEXT,
            nss_clean TEXT,
            cedula_clean TEXT,
            telefono_clean TEXT,
            estado TEXT NOT NULL DEFAULT 'ACTIVO' CHECK (estado IN ('ACTIVO','INACTIVO','PURGADO')),
            provisional INTEGER NOT NULL DEFAULT 0 CHECK (provisional IN (0,1)),
            requiere_revision INTEGER NOT NULL DEFAULT 0 CHECK (requiere_revision IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT
            ,global_patient_id TEXT
            ,server_revision INTEGER NOT NULL DEFAULT 0
            ,sync_state TEXT NOT NULL DEFAULT 'SYNCED'
            ,is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS paciente_identificadores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK (tipo IN ('NSS','CEDULA')),
            valor_normalizado TEXT NOT NULL,
            activo INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0,1)),
            conflicto INTEGER NOT NULL DEFAULT 0 CHECK (conflicto IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE CASCADE,
            UNIQUE (paciente_id, tipo, valor_normalizado)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_cedula_activa
            ON paciente_identificadores(tipo, valor_normalizado)
            WHERE tipo='CEDULA' AND activo=1 AND conflicto=0;
        CREATE INDEX IF NOT EXISTS idx_paciente_identificadores_lookup
            ON paciente_identificadores(tipo, valor_normalizado, activo, conflicto, paciente_id);
        CREATE INDEX IF NOT EXISTS idx_pacientes_nombre ON pacientes(nombre);
        CREATE INDEX IF NOT EXISTS idx_pacientes_nss_clean ON pacientes(nss_clean);
        CREATE INDEX IF NOT EXISTS idx_pacientes_cedula_clean ON pacientes(cedula_clean);

        CREATE TABLE IF NOT EXISTS dias_operativos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_base TEXT NOT NULL UNIQUE,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','CERRADO')),
            origen TEXT NOT NULL DEFAULT 'OPERATIVO',
            requiere_revision INTEGER NOT NULL DEFAULT 0 CHECK (requiere_revision IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS turnos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dia_operativo_id INTEGER NOT NULL,
            fecha_inicio TEXT NOT NULL,
            fecha_fin TEXT NOT NULL,
            fecha_inicio_real TEXT,
            representante TEXT NOT NULL,
            tipo_turno TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'ABIERTO' CHECK (estado IN ('ABIERTO','CERRADO')),
            fecha_cierre TEXT,
            origen TEXT NOT NULL DEFAULT 'OPERATIVO',
            requiere_revision INTEGER NOT NULL DEFAULT 0 CHECK (requiere_revision IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT,
            FOREIGN KEY (dia_operativo_id) REFERENCES dias_operativos(id) ON DELETE RESTRICT,
            UNIQUE(dia_operativo_id, fecha_inicio, tipo_turno)
        );
        CREATE INDEX IF NOT EXISTS idx_turnos_estado ON turnos(estado);
        CREATE INDEX IF NOT EXISTS idx_turnos_dia ON turnos(dia_operativo_id);

        CREATE TABLE IF NOT EXISTS atenciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id INTEGER NOT NULL,
            dia_operativo_id INTEGER NOT NULL,
            turno_id INTEGER NOT NULL,
            nss TEXT,
            nombre TEXT NOT NULL,
            sexo TEXT,
            edad_num INTEGER,
            unidad TEXT,
            cedula TEXT,
            telefono TEXT,
            direccion TEXT,
            nacionalidad TEXT,
            ars TEXT,
            hoja TEXT,
            fecha TEXT,
            hora TEXT,
            tipo_atencion TEXT NOT NULL DEFAULT 'EMERGENCIA',
            estado TEXT NOT NULL DEFAULT 'ACTIVA' CHECK (estado IN ('ACTIVA','ANULADA')),
            es_reingreso INTEGER NOT NULL DEFAULT 0 CHECK (es_reingreso IN (0,1)),
            atencion_origen_id INTEGER,
            motivo_reingreso TEXT,
            autorizado_por TEXT,
            identidad_estado TEXT NOT NULL DEFAULT 'VALIDADA',
            requiere_revision INTEGER NOT NULL DEFAULT 0 CHECK (requiere_revision IN (0,1)),
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT,
            anulada_at TEXT,
            anulada_por TEXT,
            anulada_motivo TEXT,
            nss_clean TEXT,
            cedula_clean TEXT,
            telefono_clean TEXT,
            correction_reason TEXT,
            correction_actor TEXT,
            correction_at TEXT,
            correction_before_json TEXT,
            correction_after_json TEXT,
            correction_changed_fields_json TEXT,
            FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE RESTRICT,
            FOREIGN KEY (dia_operativo_id) REFERENCES dias_operativos(id) ON DELETE RESTRICT,
            FOREIGN KEY (turno_id) REFERENCES turnos(id) ON DELETE RESTRICT,
            FOREIGN KEY (atencion_origen_id) REFERENCES atenciones(id) ON DELETE SET NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS uq_atencion_dia_paciente
            ON atenciones(dia_operativo_id, paciente_id)
            WHERE estado='ACTIVA' AND es_reingreso=0;
        CREATE INDEX IF NOT EXISTS idx_atenciones_fecha ON atenciones(fecha);
        CREATE INDEX IF NOT EXISTS idx_atenciones_nombre ON atenciones(nombre);
        CREATE INDEX IF NOT EXISTS idx_atenciones_nss_clean ON atenciones(nss_clean);
        CREATE INDEX IF NOT EXISTS idx_atenciones_cedula_clean ON atenciones(cedula_clean);
        CREATE INDEX IF NOT EXISTS idx_atenciones_turno_id ON atenciones(turno_id);
        CREATE INDEX IF NOT EXISTS idx_atenciones_dia_id ON atenciones(dia_operativo_id);
        CREATE INDEX IF NOT EXISTS idx_atenciones_created_at ON atenciones(created_at);

        CREATE TABLE IF NOT EXISTS atenciones_auditoria (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            atencion_id INTEGER,
            accion TEXT NOT NULL,
            motivo TEXT,
            usuario TEXT,
            actor_rol TEXT,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            snapshot_after_json TEXT,
            previous_hash TEXT,
            event_hash TEXT,
            workstation TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (atencion_id) REFERENCES atenciones(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auditoria_atencion ON atenciones_auditoria(atencion_id);

        CREATE TABLE IF NOT EXISTS auditoria_tombstones (
            event_hash TEXT PRIMARY KEY,
            previous_hash TEXT,
            purge_event_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            workstation TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_auditoria_tombstones_purga
            ON auditoria_tombstones(purge_event_hash);

        CREATE TABLE IF NOT EXISTS trabajos_salida (
            atencion_id INTEGER PRIMARY KEY,
            excel_estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            pdf_estado TEXT NOT NULL DEFAULT 'NO_APLICA',
            impresion_estado TEXT NOT NULL DEFAULT 'NO_APLICA',
            pdf_path TEXT,
            pdf_sha256 TEXT,
            intentos INTEGER NOT NULL DEFAULT 0,
            ultimo_error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (atencion_id) REFERENCES atenciones(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_trabajos_pendientes
            ON trabajos_salida(excel_estado, pdf_estado, impresion_estado);
        CREATE TABLE IF NOT EXISTS documentos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            atencion_id INTEGER NOT NULL,
            tipo TEXT NOT NULL,
            ruta TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            plantilla TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (atencion_id) REFERENCES atenciones(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS identidad_conflictos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_id INTEGER,
            atencion_id INTEGER,
            tipo TEXT NOT NULL,
            detalle TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            resolucion TEXT,
            motivo_resolucion TEXT,
            resuelto_por TEXT,
            resuelto_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (paciente_id) REFERENCES pacientes(id) ON DELETE SET NULL,
            FOREIGN KEY (atencion_id) REFERENCES atenciones(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS nss_conflictos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nss_normalizado TEXT NOT NULL,
            paciente_nuevo_id INTEGER,
            paciente_referencia_id INTEGER,
            atencion_id INTEGER,
            detalle TEXT NOT NULL,
            estado TEXT NOT NULL DEFAULT 'PENDIENTE',
            resolucion TEXT,
            motivo_resolucion TEXT,
            resuelto_por TEXT,
            resuelto_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (paciente_nuevo_id) REFERENCES pacientes(id) ON DELETE SET NULL,
            FOREIGN KEY (paciente_referencia_id) REFERENCES pacientes(id) ON DELETE SET NULL,
            FOREIGN KEY (atencion_id) REFERENCES atenciones(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_nss_conflictos_estado
            ON nss_conflictos(estado,created_at);
        CREATE TABLE IF NOT EXISTS purga_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paciente_hash TEXT NOT NULL,
            motivo TEXT NOT NULL,
            actor TEXT NOT NULL,
            actor_rol TEXT NOT NULL,
            backup_path TEXT NOT NULL,
            atenciones_eliminadas INTEGER NOT NULL,
            fichas_eliminadas INTEGER NOT NULL,
            previous_hash TEXT,
            event_hash TEXT NOT NULL,
            workstation TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS app_metadata (
            clave TEXT PRIMARY KEY,
            valor TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS integracion_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uuid TEXT NOT NULL UNIQUE,
            source_instance_id TEXT NOT NULL,
            atencion_id INTEGER NOT NULL,
            tipo TEXT NOT NULL CHECK (
                tipo IN ('ATENCION_LISTA_FACTURACION','ATENCION_REQUIERE_CORRECCION')
            ),
            estado_flujo TEXT NOT NULL CHECK (
                estado_flujo IN ('INCOMPLETO','LISTO_PARA_FACTURAR','FACTURADO','REQUIERE_CORRECCION')
            ),
            campos_faltantes_json TEXT NOT NULL DEFAULT '[]',
            actor TEXT NOT NULL,
            actor_rol TEXT NOT NULL,
            session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (atencion_id) REFERENCES atenciones(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_integracion_eventos_atencion
            ON integracion_eventos(atencion_id,id DESC);
        CREATE INDEX IF NOT EXISTS idx_integracion_eventos_estado
            ON integracion_eventos(estado_flujo,id DESC);
        CREATE TABLE IF NOT EXISTS turno_cierre_eventos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_uuid TEXT NOT NULL UNIQUE,
            source_instance_id TEXT NOT NULL,
            turno_id INTEGER NOT NULL,
            dia_operativo_id INTEGER NOT NULL,
            fecha_base TEXT NOT NULL,
            fecha_inicio TEXT NOT NULL,
            fecha_fin_programada TEXT NOT NULL,
            fecha_cierre_real TEXT NOT NULL,
            representante TEXT NOT NULL,
            tipo_turno TEXT NOT NULL,
            actor TEXT NOT NULL,
            actor_rol TEXT NOT NULL,
            session_id TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            FOREIGN KEY (turno_id) REFERENCES turnos(id) ON DELETE RESTRICT,
            FOREIGN KEY (dia_operativo_id) REFERENCES dias_operativos(id) ON DELETE RESTRICT,
            UNIQUE(source_instance_id,turno_id)
        );
        CREATE INDEX IF NOT EXISTS idx_turno_cierre_eventos_created
            ON turno_cierre_eventos(id,created_at);
        """
    )
    # Versiones anteriores imponían unicidad global también al NSS.
    conn.execute("DROP INDEX IF EXISTS uq_identificador_activo")
    conn.execute(
        """
        INSERT OR IGNORE INTO app_metadata(clave,valor,updated_at)
        VALUES(
            'integration.source_instance_id',
            ?,
            datetime('now','localtime')
        )
        """,
        (str(uuid.uuid4()),),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO app_metadata(clave,valor,updated_at)
        VALUES(
            'integration.contract_version',
            '1',
            datetime('now','localtime')
        )
        """
    )


def _retire_identity_conflicts(conn: sqlite3.Connection) -> None:
    """Close the retired review queue without deleting clinical history."""
    if not _table_exists(conn, "identidad_conflictos"):
        return
    conn.execute(
        """
        UPDATE atenciones SET identidad_estado='VALIDADA',requiere_revision=0,
            updated_at=COALESCE(updated_at,datetime('now','localtime'))
        WHERE id IN (
            SELECT atencion_id FROM identidad_conflictos
            WHERE estado='PENDIENTE' AND atencion_id IS NOT NULL
        )
        """
    )
    conn.execute(
        """
        UPDATE pacientes SET requiere_revision=0,
            updated_at=COALESCE(updated_at,datetime('now','localtime'))
        WHERE id IN (
            SELECT paciente_id FROM identidad_conflictos
            WHERE estado='PENDIENTE' AND paciente_id IS NOT NULL
        )
        """
    )
    conn.execute(
        """
        UPDATE identidad_conflictos
        SET estado='CERRADO',resolucion=COALESCE(resolucion,'FUNCION_RETIRADA'),
            motivo_resolucion=COALESCE(motivo_resolucion,'Cola retirada en la version 4.1.5'),
            resuelto_por=COALESCE(resuelto_por,'SISTEMA'),
            resuelto_at=COALESCE(resuelto_at,datetime('now','localtime'))
        WHERE estado='PENDIENTE'
        """
    )


def _insert_patient(
    conn: sqlite3.Connection, row: dict, *, patient_id: int | None = None, provisional: int = 0, review: int = 0
) -> int:
    nss = str(row.get("nss") or "").strip()
    cedula = str(row.get("cedula") or "").strip()
    phone = str(row.get("telefono") or "").strip()
    values = (
        patient_id,
        str(row.get("nombre") or "SIN NOMBRE").strip() or "SIN NOMBRE",
        cedula or None,
        phone or None,
        row.get("direccion"),
        row.get("nacionalidad"),
        row.get("ars"),
        nss or None,
        _digits(nss) if valid_nss(nss) else None,
        _digits(cedula) if valid_cedula(cedula) else None,
        _digits(phone) or None,
        int(provisional),
        int(review),
        row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        row.get("updated_at"),
    )
    conn.execute(
        """
        INSERT INTO pacientes (
            id,nombre,cedula,telefono,direccion,nacionalidad,ars,nss,
            nss_clean,cedula_clean,telefono_clean,provisional,requiere_revision,created_at,updated_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        values,
    )
    return int(patient_id or conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def _copy_legacy_database(source: sqlite3.Connection, target: sqlite3.Connection) -> dict:
    create_latest_schema(target)
    source_patients = _rows(source, "pacientes")
    source_attentions = sorted(_rows(source, "atenciones"), key=lambda row: int(row.get("id") or 0))

    nss_counts = Counter(_digits(row.get("nss")) for row in source_patients if valid_nss(row.get("nss")))
    ced_counts = Counter(_digits(row.get("cedula")) for row in source_patients if valid_cedula(row.get("cedula")))
    nss_to_patients: dict[str, list[int]] = defaultdict(list)
    ced_to_patients: dict[str, list[int]] = defaultdict(list)

    for row in source_patients:
        patient_id = int(row.get("_rowid_") or 0)
        nss = _digits(row.get("nss"))
        ced = _digits(row.get("cedula"))
        review = int((valid_nss(nss) and nss_counts[nss] > 1) or (valid_cedula(ced) and ced_counts[ced] > 1))
        _insert_patient(target, row, patient_id=patient_id, review=review)
        if valid_nss(nss):
            nss_to_patients[nss].append(patient_id)
            target.execute(
                "INSERT INTO paciente_identificadores(paciente_id,tipo,valor_normalizado,conflicto) VALUES (?,?,?,?)",
                (patient_id, "NSS", nss, int(nss_counts[nss] > 1)),
            )
        if valid_cedula(ced):
            ced_to_patients[ced].append(patient_id)
            target.execute(
                "INSERT INTO paciente_identificadores(paciente_id,tipo,valor_normalizado,conflicto) VALUES (?,?,?,?)",
                (patient_id, "CEDULA", ced, int(ced_counts[ced] > 1)),
            )

    next_patient_id = max([int(row.get("_rowid_") or 0) for row in source_patients] or [0]) + 1
    synthetic_by_key: dict[tuple[str, str], int] = {}
    days: dict[date, tuple[int, int]] = {}
    first_by_day_patient: dict[tuple[int, int], int] = {}
    conflict_attention_count = 0
    provisional_count = 0

    def create_synthetic(row: dict, key: tuple[str, str] | None, review: int) -> int:
        nonlocal next_patient_id, provisional_count
        if key is not None and key in synthetic_by_key:
            return synthetic_by_key[key]
        patient_id = next_patient_id
        next_patient_id += 1
        provisional_count += 1
        _insert_patient(target, row, patient_id=patient_id, provisional=1, review=review)
        if key is not None:
            synthetic_by_key[key] = patient_id
        for kind, raw, valid in (("NSS", row.get("nss"), valid_nss), ("CEDULA", row.get("cedula"), valid_cedula)):
            cleaned = _digits(raw)
            if valid(cleaned):
                target.execute(
                    "INSERT OR IGNORE INTO paciente_identificadores(paciente_id,tipo,valor_normalizado,conflicto) VALUES (?,?,?,?)",
                    (patient_id, kind, cleaned, int(review)),
                )
        return patient_id

    def resolve_patient(row: dict) -> tuple[int, int, str]:
        nss = _digits(row.get("nss"))
        ced = _digits(row.get("cedula"))
        nss_ids = set(nss_to_patients.get(nss, ())) if valid_nss(nss) else set()
        ced_ids = set(ced_to_patients.get(ced, ())) if valid_cedula(ced) else set()
        candidates = nss_ids & ced_ids if nss_ids and ced_ids and (nss_ids & ced_ids) else nss_ids | ced_ids
        if len(candidates) == 1:
            return next(iter(candidates)), 0, "VALIDADA"
        if valid_nss(nss) or valid_cedula(ced):
            key = (nss if valid_nss(nss) else "", ced if valid_cedula(ced) else "")
            return create_synthetic(row, key, 1), 1, "LEGACY_PENDIENTE_REVISION"
        return create_synthetic(row, None, 1), 1, "SIN_IDENTIFICADOR_FUERTE"

    def resolve_day(moment: datetime) -> tuple[int, int]:
        base = operational_base_day(moment)
        if base in days:
            return days[base]
        start = datetime.combine(base, time(8, 0))
        end = start + timedelta(days=1)
        target.execute(
            """
            INSERT INTO dias_operativos(fecha_base,fecha_inicio,fecha_fin,estado,origen,requiere_revision)
            VALUES (?,?,?,'CERRADO','MIGRADO',1)
            """,
            (base.isoformat(), start.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S")),
        )
        day_id = int(target.execute("SELECT last_insert_rowid()").fetchone()[0])
        target.execute(
            """
            INSERT INTO turnos(
                dia_operativo_id,fecha_inicio,fecha_fin,fecha_inicio_real,representante,
                tipo_turno,estado,fecha_cierre,origen,requiere_revision
            ) VALUES (?,?,?,?,?,'HISTORICO_24H','CERRADO',?,'MIGRADO',1)
            """,
            (
                day_id,
                start.strftime("%Y-%m-%d %H:%M:%S"),
                end.strftime("%Y-%m-%d %H:%M:%S"),
                start.strftime("%Y-%m-%d %H:%M:%S"),
                "NO DISPONIBLE",
                end.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        shift_id = int(target.execute("SELECT last_insert_rowid()").fetchone()[0])
        days[base] = (day_id, shift_id)
        return day_id, shift_id

    attention_fields = (
        "id,paciente_id,dia_operativo_id,turno_id,nss,nombre,sexo,edad_num,unidad,cedula,telefono,"
        "direccion,nacionalidad,ars,hoja,fecha,hora,tipo_atencion,estado,es_reingreso,"
        "atencion_origen_id,motivo_reingreso,autorizado_por,identidad_estado,requiere_revision,"
        "created_at,updated_at,nss_clean,cedula_clean,telefono_clean"
    )
    placeholders = ",".join("?" for _ in attention_fields.split(","))
    for row in source_attentions:
        moment = parse_clinical_datetime(row.get("fecha"), row.get("hora"), row.get("created_at"))
        day_id, shift_id = resolve_day(moment)
        patient_id, identity_review, identity_state = resolve_patient(row)
        key = (day_id, patient_id)
        origin_id = first_by_day_patient.get(key)
        is_reentry = int(origin_id is not None)
        if origin_id is None:
            first_by_day_patient[key] = int(row["id"])
        else:
            conflict_attention_count += 1
        requires_review = int(identity_review or is_reentry)
        target.execute(
            f"INSERT INTO atenciones({attention_fields}) VALUES ({placeholders})",
            (
                int(row["id"]),
                patient_id,
                day_id,
                shift_id,
                row.get("nss"),
                row.get("nombre") or "SIN NOMBRE",
                row.get("sexo"),
                row.get("edad_num"),
                row.get("unidad"),
                row.get("cedula"),
                row.get("telefono"),
                row.get("direccion"),
                row.get("nacionalidad"),
                row.get("ars"),
                row.get("hoja"),
                row.get("fecha"),
                row.get("hora"),
                str(row.get("tipo_atencion") or "EMERGENCIA").upper(),
                "ACTIVA",
                is_reentry,
                origin_id,
                "Migrado: posible duplicado o reingreso" if is_reentry else None,
                None,
                "LEGACY_PENDIENTE_REVISION" if is_reentry else identity_state,
                requires_review,
                row.get("created_at") or moment.strftime("%Y-%m-%d %H:%M:%S"),
                row.get("updated_at"),
                _digits(row.get("nss")) or None,
                _digits(row.get("cedula")) or None,
                _digits(row.get("telefono")) or None,
            ),
        )
        if requires_review:
            conflict_type = "POSIBLE_REINGRESO" if is_reentry else identity_state
            target.execute(
                "INSERT INTO identidad_conflictos(paciente_id,atencion_id,tipo,detalle) VALUES (?,?,?,?)",
                (patient_id, int(row["id"]), conflict_type, "Registro historico conservado para revision manual"),
            )
        target.execute(
            "INSERT INTO trabajos_salida(atencion_id,excel_estado,pdf_estado,impresion_estado) VALUES (?,'COMPLETADO','DESCONOCIDO','DESCONOCIDO')",
            (int(row["id"]),),
        )

    migrated_attention_ids = {int(row["id"]) for row in source_attentions}
    for row in _rows(source, "atenciones_auditoria"):
        legacy_attention_id = row.get("atencion_id")
        attention_id = (
            int(legacy_attention_id)
            if legacy_attention_id is not None and int(legacy_attention_id) in migrated_attention_ids
            else None
        )
        target.execute(
            """
            INSERT INTO atenciones_auditoria(
                id,atencion_id,accion,motivo,usuario,snapshot_json,created_at,workstation
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                row.get("id"),
                attention_id,
                row.get("accion") or "MIGRADO",
                row.get("motivo"),
                row.get("usuario"),
                row.get("snapshot_json") or "{}",
                row.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                socket.gethostname(),
            ),
        )

    target.execute("INSERT OR REPLACE INTO schema_version(id,version) VALUES (1,?)", (LATEST_SCHEMA_VERSION,))
    details = {
        "source_patients": len(source_patients),
        "source_attentions": len(source_attentions),
        "operational_days": len(days),
        "provisional_patients": provisional_count,
        "legacy_attention_conflicts": conflict_attention_count,
    }
    target.execute(
        "INSERT INTO schema_migrations(version,name,applied_at,details_json) VALUES (?,?,?,?)",
        (
            LATEST_SCHEMA_VERSION,
            "copy_to_relational_schema",
            datetime.now().isoformat(timespec="seconds"),
            json.dumps(details, sort_keys=True),
        ),
    )
    return details


def validate_database(path: str | os.PathLike[str], expected_attentions: int | None = None) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        count = conn.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0]
        null_links = conn.execute(
            "SELECT COUNT(*) FROM atenciones WHERE paciente_id IS NULL OR dia_operativo_id IS NULL OR turno_id IS NULL"
        ).fetchone()[0]
        version = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0]
    if integrity != "ok" or fk_rows or null_links:
        raise sqlite3.DatabaseError(
            f"Validacion fallida: integrity={integrity}, foreign_keys={len(fk_rows)}, null_links={null_links}"
        )
    if expected_attentions is not None and count != expected_attentions:
        raise sqlite3.DatabaseError(f"Se esperaban {expected_attentions} atenciones y se obtuvieron {count}")
    return {"integrity": integrity, "foreign_key_violations": 0, "attentions": count, "version": version}


def _initialize_database(path: Path) -> dict:
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        create_latest_schema(conn)
        conn.execute("INSERT OR REPLACE INTO schema_version(id,version) VALUES (1,?)", (LATEST_SCHEMA_VERSION,))
        conn.execute(
            "INSERT INTO schema_migrations(version,name,applied_at,details_json) VALUES (?,?,?,?)",
            (LATEST_SCHEMA_VERSION, "new_database", datetime.now().isoformat(timespec="seconds"), "{}"),
        )
        conn.commit()
    return {"created": True, **validate_database(path, 0)}


def migrate_database(db_path: str | os.PathLike[str], backup_manager: BackupManager, logger=None) -> dict:
    target_path = Path(db_path)
    if backup_manager.db_path.resolve() != target_path.resolve():
        raise ValueError("El administrador de respaldos no corresponde a la base que se migrara")
    if not target_path.exists():
        return _initialize_database(target_path)

    with closing(sqlite3.connect(target_path)) as conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        version = 0
        if _table_exists(conn, "schema_version"):
            row = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()
            version = int(row[0]) if row else 0
        has_legacy = _table_exists(conn, "atenciones")
        attention_count = conn.execute("SELECT COUNT(*) FROM atenciones").fetchone()[0] if has_legacy else 0
        user_tables = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        }
    if version > LATEST_SCHEMA_VERSION:
        raise sqlite3.DatabaseError(
            f"La base usa el esquema futuro v{version}; esta aplicacion solo admite hasta v{LATEST_SCHEMA_VERSION}"
        )
    if version == LATEST_SCHEMA_VERSION:
        # A normal client startup must not perform DDL, an integrity scan or a
        # full WAL checkpoint.  Those operations can briefly monopolize the
        # local writer and are reserved for a real schema migration or an
        # explicit maintenance/self-test action.
        return {
            "created": False,
            "migrated": False,
            "version": version,
            "startup_checked": True,
        }
    if version in (11, 12, 13, 14, 15, 16, 17, 18):
        backup_folder = backup_manager.create(
            f"antes_migracion_v{version}_a_v{LATEST_SCHEMA_VERSION}"
        )
        with closing(sqlite3.connect(target_path)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            create_latest_schema(conn)
            _add_missing_columns(
                conn,
                "pacientes",
                (
                    ("global_patient_id", "TEXT"),
                    ("server_revision", "INTEGER NOT NULL DEFAULT 0"),
                    ("sync_state", "TEXT NOT NULL DEFAULT 'SYNCED'"),
                    ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
                ),
            )
            _add_missing_columns(
                conn,
                "atenciones",
                (
                    ("correction_reason", "TEXT"),
                    ("correction_actor", "TEXT"),
                    ("correction_at", "TEXT"),
                    ("correction_before_json", "TEXT"),
                    ("correction_after_json", "TEXT"),
                    ("correction_changed_fields_json", "TEXT"),
                ),
            )
            _add_missing_columns(
                conn,
                "identidad_conflictos",
                (
                    ("resolucion", "TEXT"),
                    ("motivo_resolucion", "TEXT"),
                    ("resuelto_por", "TEXT"),
                    ("resuelto_at", "TEXT"),
                ),
            )
            _retire_identity_conflicts(conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version(id,version) VALUES (1,?)",
                (LATEST_SCHEMA_VERSION,),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO schema_migrations(version,name,applied_at,details_json)
                VALUES (?,?,?,?)
                """,
                (
                    LATEST_SCHEMA_VERSION,
                    "attention_rectification_audit_metadata",
                    datetime.now().isoformat(timespec="seconds"),
                    json.dumps({"from_version": version}, sort_keys=True),
                ),
            )
            conn.commit()
        result = {
            "created": False,
            "migrated": True,
            "from_version": version,
            "to_version": LATEST_SCHEMA_VERSION,
            "backup": str(backup_folder),
            **validate_database(target_path),
        }
        if logger:
            logger.info("Migracion de revision de identidades completada: %s", result)
        return result
    if not has_legacy:
        if not user_tables:
            return _initialize_database(target_path)
        raise sqlite3.DatabaseError(
            "La base existente no contiene la tabla atenciones y no se modificara automaticamente"
        )

    backup_folder = backup_manager.create(f"antes_migracion_v{version}_a_v{LATEST_SCHEMA_VERSION}")
    temp_path = target_path.with_suffix(target_path.suffix + ".migrating")
    temp_path.unlink(missing_ok=True)
    try:
        with (
            closing(sqlite3.connect(f"file:{target_path}?mode=ro", uri=True)) as source,
            closing(sqlite3.connect(temp_path)) as target,
        ):
            target.execute("PRAGMA journal_mode=DELETE")
            target.execute("PRAGMA synchronous=FULL")
            target.execute("PRAGMA foreign_keys=ON")
            target.execute("BEGIN IMMEDIATE")
            details = _copy_legacy_database(source, target)
            _retire_identity_conflicts(target)
            target.commit()
        validation = validate_database(temp_path, attention_count)
        for suffix in ("-wal", "-shm"):
            Path(str(target_path) + suffix).unlink(missing_ok=True)
        os.replace(temp_path, target_path)
        validation = validate_database(target_path, attention_count)
        result = {
            "created": False,
            "migrated": True,
            "from_version": version,
            "to_version": LATEST_SCHEMA_VERSION,
            "backup": str(backup_folder),
            **details,
            **validation,
        }
        if logger:
            logger.info("Migracion completada: %s", result)
        return result
    except Exception:
        temp_path.unlink(missing_ok=True)
        if logger:
            logger.exception("Fallo la migracion; la base original no fue reemplazada")
        raise
