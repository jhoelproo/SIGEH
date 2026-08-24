from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import traceback
import uuid
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import CALCULOS_QT as app
from report_documents import (
    REPORT_TEMPLATE_VERSION,
    STORAGE_HYBRID,
    STORAGE_LEGACY,
    STORAGE_SNAPSHOT,
    apply_report_document_migration,
    calculate_report_snapshot_hash,
    default_archive_root,
    load_current_report_snapshot,
    render_report_snapshot_pdf,
    save_report_document_snapshot,
    source_key,
)


DATABASE_LIMIT_BYTES = 500 * 1024 * 1024
MIGRATION_LOCK_KEY = 0x484F5350
MIGRATION_ACTOR = "DOCUMENT_MIGRATION"


class MigrationStopped(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def json_safe(value):
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return f"<binary:{len(value)}>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def bytes_sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _rows(con, sql: str, params=()):
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def capture_capacity() -> dict[str, Any]:
    with app.db_connect() as con:
        database_size = int(
            con.execute(
                "SELECT pg_database_size(current_database()) AS bytes"
            ).fetchone()["bytes"]
        )
        largest = _rows(
            con,
            """SELECT c.relname AS relation,
                      pg_total_relation_size(c.oid) AS total_bytes,
                      pg_relation_size(c.oid) AS data_bytes,
                      pg_indexes_size(c.oid) AS index_bytes,
                      CASE WHEN c.reltoastrelid<>0
                           THEN pg_total_relation_size(c.reltoastrelid)
                           ELSE 0 END AS toast_bytes
               FROM pg_class c
               JOIN pg_namespace n ON n.oid=c.relnamespace
               WHERE n.nspname='public' AND c.relkind IN('r','m')
               ORDER BY total_bytes DESC LIMIT 10""",
        )
        dead_rows = _rows(
            con,
            """SELECT relname,n_live_tup,n_dead_tup,last_autovacuum,last_autoanalyze
               FROM pg_stat_user_tables ORDER BY n_dead_tup DESC,relname LIMIT 20""",
        )
        pdf_size = dict(
            con.execute(
                """SELECT pg_total_relation_size('public.pdf_storage'::regclass)
                            AS total_bytes,
                          pg_relation_size('public.pdf_storage'::regclass)
                            AS data_bytes,
                          pg_indexes_size('public.pdf_storage'::regclass)
                            AS index_bytes,
                          CASE WHEN c.reltoastrelid<>0
                               THEN pg_total_relation_size(c.reltoastrelid)
                               ELSE 0 END AS toast_bytes
                   FROM pg_class c
                   WHERE c.oid='public.pdf_storage'::regclass"""
            ).fetchone()
        )
        logical = dict(
            con.execute(
                """SELECT COUNT(*) AS rows,
                          COALESCE(SUM(OCTET_LENGTH(file_data)),0) AS logical_bytes
                   FROM pdf_storage"""
            ).fetchone()
        )
        counts = {
            "receipts": int(
                con.execute("SELECT COUNT(*) AS value FROM recibos").fetchone()[
                    "value"
                ]
            ),
            "receipt_items": int(
                con.execute(
                    "SELECT COUNT(*) AS value FROM recibo_items"
                ).fetchone()["value"]
            ),
            "report_history": int(
                con.execute(
                    "SELECT COUNT(*) AS value FROM report_history"
                ).fetchone()["value"]
            ),
            "daily_reports": int(
                con.execute(
                    "SELECT COUNT(*) AS value FROM daily_reports"
                ).fetchone()["value"]
            ),
            "shift_reports": int(
                con.execute(
                    """SELECT COUNT(*) AS value FROM billing_shift_closures
                       WHERE report_filename IS NOT NULL"""
                ).fetchone()["value"]
            ),
            "receipt_snapshots": int(
                con.execute(
                    "SELECT COUNT(*) AS value FROM recibo_document_versions"
                ).fetchone()["value"]
            ),
            "report_snapshots": int(
                con.execute(
                    """SELECT COUNT(*) AS value
                       FROM information_schema.tables
                       WHERE table_schema='public'
                         AND table_name='report_document_versions'"""
                ).fetchone()["value"]
            ),
        }
        if counts["report_snapshots"]:
            counts["report_snapshots"] = int(
                con.execute(
                    "SELECT COUNT(*) AS value FROM report_document_versions"
                ).fetchone()["value"]
            )
        receipt_amount = float(
            con.execute(
                "SELECT COALESCE(SUM(total::numeric),0) AS value FROM recibos"
            ).fetchone()["value"]
        )
    return {
        "captured_at": utc_now(),
        "database_size_bytes": database_size,
        "database_limit_bytes": DATABASE_LIMIT_BYTES,
        "usage_percent": database_size / DATABASE_LIMIT_BYTES * 100,
        "largest_relations": largest,
        "dead_rows": dead_rows,
        "pdf_storage": {**pdf_size, **logical},
        "counts": counts,
        "receipt_amount": receipt_amount,
    }


def save_capacity_sample(capacity: dict, note: str) -> None:
    with app.db_connect() as con:
        con.execute(
            """INSERT INTO database_capacity_history(
                 database_size_bytes,database_limit_bytes,usage_percent,
                 largest_relations_jsonb,dead_rows_jsonb,captured_by,notes
               ) VALUES(%s,%s,%s,%s,%s,%s,%s)""",
            (
                int(capacity["database_size_bytes"]),
                int(capacity["database_limit_bytes"]),
                float(capacity["usage_percent"]),
                app.psycopg2.extras.Json(
                    json_safe(capacity["largest_relations"])
                ),
                app.psycopg2.extras.Json(json_safe(capacity["dead_rows"])),
                MIGRATION_ACTOR,
                note,
            ),
        )


@contextmanager
def migration_lock():
    wrapper = app.db_connect()
    con = wrapper.__enter__()
    acquired = False
    try:
        acquired = bool(
            con.execute(
                "SELECT pg_try_advisory_lock(%s) AS acquired",
                (MIGRATION_LOCK_KEY,),
            ).fetchone()["acquired"]
        )
        if not acquired:
            raise MigrationStopped(
                "Otra estación está ejecutando la migración documental."
            )
        yield
    finally:
        if acquired:
            try:
                con.execute(
                    "SELECT pg_advisory_unlock(%s)", (MIGRATION_LOCK_KEY,)
                )
            except Exception:
                pass
        wrapper.__exit__(None, None, None)


def apply_migrations_twice() -> None:
    for _ in range(2):
        with app.db_connect() as con:
            apply_report_document_migration(con)


def logical_backup(run_dir: Path) -> Path:
    tables = (
        "recibos",
        "recibo_items",
        "recibo_facturacion_history",
        "recibo_document_versions",
        "recibo_document_migration",
        "report_history",
        "daily_reports",
        "billing_shift_closures",
        "billing_shift_closure_details",
        "active_sessions",
        "session_control",
    )
    destination = run_dir / "logical_metadata_backup.jsonl.gz"
    with app.db_connect() as con, gzip.open(
        destination, "wt", encoding="utf-8"
    ) as target:
        for table in tables:
            exists = con.execute(
                """SELECT to_regclass(%s) IS NOT NULL AS exists""",
                (f"public.{table}",),
            ).fetchone()["exists"]
            if not exists:
                continue
            for row in con.execute(f"SELECT * FROM {table}").fetchall():
                target.write(
                    json.dumps(
                        {"table": table, "row": json_safe(dict(row))},
                        ensure_ascii=False,
                    )
                    + "\n"
                )
    return destination


def _parse_json(value) -> dict:
    if isinstance(value, dict):
        return json_safe(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return json_safe(parsed) if isinstance(parsed, dict) else {}


def report_records() -> list[dict]:
    with app.db_connect() as con:
        mode_tables = {
            str(row["table_name"])
            for row in con.execute(
                """SELECT table_name FROM information_schema.columns
                   WHERE table_schema='public'
                     AND column_name='document_storage_mode'
                     AND table_name IN(
                       'report_history','daily_reports',
                       'billing_shift_closures'
                     )"""
            ).fetchall()
        }
        history_mode = (
            "document_storage_mode"
            if "report_history" in mode_tables
            else "NULL::text AS document_storage_mode"
        )
        daily_mode = (
            "document_storage_mode"
            if "daily_reports" in mode_tables
            else "NULL::text AS document_storage_mode"
        )
        closure_mode = (
            "document_storage_mode"
            if "billing_shift_closures" in mode_tables
            else "NULL::text AS document_storage_mode"
        )
        records = _rows(
            con,
            f"""SELECT id,'report_history' AS source_table,report_type,
                      start_date,end_date,generated_at,generated_by,filepath,
                      totals_json,{history_mode}
               FROM report_history ORDER BY id""",
        )
        records.extend(
            _rows(
                con,
                f"""SELECT id,'daily_reports' AS source_table,
                          'Diario' AS report_type,report_date AS start_date,
                          report_date AS end_date,generated_at,generated_by,
                          filepath,totals_json,{daily_mode}
                   FROM daily_reports ORDER BY id""",
            )
        )
        records.extend(
            _rows(
                con,
                f"""SELECT turn_id AS id,source_instance_id,turn_id,
                          'billing_shift_closures' AS source_table,
                          'Cierre automático de turno' AS report_type,
                          operational_date AS start_date,
                          operational_date AS end_date,generated_at,
                          COALESCE(NULLIF(representative,''),claimed_by,'Sistema')
                            AS generated_by,
                          report_filename AS filepath,totals_json,
                          {closure_mode}
                   FROM billing_shift_closures
                   WHERE report_filename IS NOT NULL
                   ORDER BY source_instance_id,turn_id""",
            )
        )
    return records


def build_historical_report_payload(record: dict) -> tuple[dict, dict]:
    payload = _parse_json(record.get("totals_json"))
    report_type = str(record.get("report_type") or "Reporte")
    start_date = str(record.get("start_date") or "")
    end_date = str(record.get("end_date") or "")
    generated_by = str(record.get("generated_by") or "Sistema")
    generated_at = str(record.get("generated_at") or "")
    source_table = str(record["source_table"])
    if source_table == "billing_shift_closures":
        data = app.build_shift_closure_report_data(record)
        context = {
            "mode": "shift_closure",
            "title": "Reporte automático de cierre de Facturación",
            "subtitle": "Control de autorizaciones de Emergencias por turno",
            "generated_by": generated_by,
            "generated_at": generated_at,
            "data": data,
            "landscape": True,
        }
        return data, context
    if report_type.startswith("Reporte comparativo"):
        if not payload.get("summary") or not payload.get("previous"):
            raise MigrationStopped(
                "El comparativo no conserva el dataset histórico completo."
            )
        context = {
            "mode": "comparison",
            "title": "REPORTE COMPARATIVO",
            "subtitle": report_type.replace("Reporte comparativo:", "").strip(),
            "generated_by": generated_by,
            "generated_at": generated_at,
            "data": payload,
            "landscape": True,
        }
        return payload, context
    if not payload:
        raise MigrationStopped("El reporte no conserva datos estructurados.")
    title = "REPORTE DIARIO" if source_table == "daily_reports" else report_type
    subtitle = (
        f"Período: {start_date}"
        if start_date == end_date
        else f"Período: {start_date} al {end_date}"
    )
    context = app._build_standard_report_context(
        title,
        subtitle,
        payload,
        generated_by,
        generated_at=generated_at,
    )
    return payload, context


def migrate_report_snapshots(
    run_dir: Path,
    *,
    limit: int | None = None,
    output_name: str = "report_snapshot_migration.json",
) -> dict[str, Any]:
    records = report_records()
    if limit is not None:
        records = records[: max(0, int(limit))]
    counters = Counter()
    errors = []
    renderer = None
    for record in records:
        table = str(record["source_table"])
        key = source_key(table, record)
        counters["analyzed"] += 1
        try:
            payload, context = build_historical_report_payload(record)
            data, filters, financial, summary, charts, guided = (
                app._report_snapshot_parts(payload)
            )
            with app.db_connect() as con:
                result = save_report_document_snapshot(
                    con,
                    source_table=table,
                    source_key_value=key,
                    report_id=(
                        int(record["id"])
                        if table == "report_history"
                        else None
                    ),
                    report_type=str(record.get("report_type") or "Reporte"),
                    report_title=str(
                        context.get("title")
                        or record.get("report_type")
                        or "Reporte"
                    ),
                    period_start=str(record.get("start_date") or "") or None,
                    period_end=str(record.get("end_date") or "") or None,
                    generated_at=str(record.get("generated_at") or utc_now()),
                    generated_by=str(record.get("generated_by") or "Sistema"),
                    filters=filters,
                    financial_basis=financial,
                    dataset=data,
                    summary=summary,
                    charts=charts,
                    guided_reading=guided,
                    render_context=context,
                    storage_mode=STORAGE_HYBRID,
                )
                document = load_current_report_snapshot(con, table, key)
            rendered = Path(
                render_report_snapshot_pdf(document, logo_path=app.LOGO_PATH or "")
            )
            if not rendered.is_file() or rendered.stat().st_size < 1000:
                raise MigrationStopped("El reporte reconstruido está vacío.")
            try:
                from pypdf import PdfReader

                pages = len(PdfReader(str(rendered)).pages)
                if pages < 1:
                    raise MigrationStopped(
                        "El reporte reconstruido no contiene páginas."
                    )
            except ImportError:
                pages = 1
            with app.db_connect() as con:
                con.execute(
                    """INSERT INTO report_document_migration(
                         source_table,source_key,migration_status,classification,
                         source_pdf_filename,snapshot_version,snapshot_hash,
                         render_verified,validation_jsonb,attempts,updated_at
                       ) VALUES(%s,%s,'RENDER_VERIFIED','RECONSTRUCTIBLE',
                                %s,%s,%s,TRUE,%s,1,NOW())
                       ON CONFLICT(source_table,source_key) DO UPDATE SET
                         migration_status='RENDER_VERIFIED',
                         classification='RECONSTRUCTIBLE',
                         source_pdf_filename=EXCLUDED.source_pdf_filename,
                         snapshot_version=EXCLUDED.snapshot_version,
                         snapshot_hash=EXCLUDED.snapshot_hash,
                         render_verified=TRUE,
                         validation_jsonb=EXCLUDED.validation_jsonb,
                         attempts=report_document_migration.attempts+1,
                         last_error=NULL,updated_at=NOW()""",
                    (
                        table,
                        key,
                        os.path.basename(str(record.get("filepath") or "")),
                        int(result["version"]),
                        str(result["snapshot_hash"]),
                        app.psycopg2.extras.Json(
                            {
                                "rendered_bytes": rendered.stat().st_size,
                                "pages": pages,
                            }
                        ),
                    ),
                )
            counters["migrated"] += 1
            counters["created"] += int(bool(result["created"]))
            counters["deduplicated"] += int(not result["created"])
        except Exception as exc:
            counters["needs_review"] += 1
            message = f"{type(exc).__name__}: {str(exc)[:500]}"
            errors.append(
                {
                    "source_table": table,
                    "source_key": key,
                    "error": message,
                }
            )
            with app.db_connect() as con:
                con.execute(
                    """INSERT INTO report_document_migration(
                         source_table,source_key,migration_status,classification,
                         source_pdf_filename,attempts,last_error,updated_at
                       ) VALUES(%s,%s,'NEEDS_REVIEW','INCOMPLETE_DATA',%s,1,%s,NOW())
                       ON CONFLICT(source_table,source_key) DO UPDATE SET
                         migration_status='NEEDS_REVIEW',
                         classification='INCOMPLETE_DATA',
                         attempts=report_document_migration.attempts+1,
                         last_error=EXCLUDED.last_error,updated_at=NOW()""",
                    (
                        table,
                        key,
                        os.path.basename(str(record.get("filepath") or "")),
                        message,
                    ),
                )
    result = {"counts": dict(counters), "errors": errors}
    write_json(run_dir / output_name, result)
    return result


def _report_file_map() -> dict[str, list[dict]]:
    mapped: dict[str, list[dict]] = defaultdict(list)
    for record in report_records():
        filename = os.path.basename(
            str(record.get("filepath") or "").replace("\\", "/")
        )
        if filename:
            mapped[filename].append(record)
    return mapped


def backup_all_binary_documents(
    run_dir: Path, archive_root: Path
) -> dict[str, Any]:
    report_map = _report_file_map()
    archive_root.mkdir(parents=True, exist_ok=True)
    manifest_path = run_dir / "binary_backup_manifest.jsonl"
    counters = Counter()
    with app.db_connect() as con:
        receipt_map = {
            str(row["pdf_filename"]): dict(row)
            for row in con.execute(
                """SELECT id,numero,pdf_filename,document_storage_mode
                   FROM recibos WHERE COALESCE(pdf_filename,'')<>''"""
            ).fetchall()
        }
    with manifest_path.open("w", encoding="utf-8") as manifest:
        offset = 0
        while True:
            with app.db_connect() as con:
                batch = con.execute(
                    """SELECT filename,file_data,document_type,owner_receipt_id
                       FROM pdf_storage ORDER BY filename LIMIT 50 OFFSET %s""",
                    (offset,),
                ).fetchall()
            if not batch:
                break
            for raw in batch:
                row = dict(raw)
                filename = os.path.basename(str(row["filename"]))
                payload = bytes(row["file_data"])
                digest = bytes_sha256(payload)
                relative = (
                    Path("documents")
                    / digest[:2]
                    / f"{digest}__{re.sub(r'[^A-Za-z0-9_.-]+', '_', filename)}"
                )
                destination = archive_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not destination.exists():
                    destination.write_bytes(payload)
                if (
                    destination.stat().st_size != len(payload)
                    or file_sha256(destination) != digest
                ):
                    raise MigrationStopped(
                        f"El respaldo no superó la verificación: {filename}"
                    )
                source_table = ""
                source_key_value = ""
                document_type = "ORPHAN"
                if filename in receipt_map:
                    receipt = receipt_map[filename]
                    source_table = "recibos"
                    source_key_value = str(int(receipt["id"]))
                    document_type = "RECEIPT"
                elif filename in report_map:
                    first = report_map[filename][0]
                    source_table = str(first["source_table"])
                    source_key_value = source_key(source_table, first)
                    document_type = "REPORT"
                elif filename.lower().startswith("recibo_"):
                    document_type = "ORPHAN_RECEIPT"
                elif filename.lower().startswith(
                    ("reporte_", "comparacion_", "cierre_")
                ):
                    document_type = "ORPHAN_REPORT"
                with app.db_connect() as con:
                    con.execute(
                        """INSERT INTO document_external_files(
                             filename,document_type,source_table,source_key,sha256,
                             size_bytes,archive_relative_path,verified_at,status
                           ) VALUES(%s,%s,%s,%s,%s,%s,%s,NOW(),'AVAILABLE')
                           ON CONFLICT(filename,sha256) DO UPDATE SET
                             document_type=EXCLUDED.document_type,
                             source_table=COALESCE(NULLIF(EXCLUDED.source_table,''),document_external_files.source_table),
                             source_key=COALESCE(NULLIF(EXCLUDED.source_key,''),document_external_files.source_key),
                             size_bytes=EXCLUDED.size_bytes,
                             archive_relative_path=EXCLUDED.archive_relative_path,
                             verified_at=NOW(),status='AVAILABLE'""",
                        (
                            filename,
                            document_type,
                            source_table or None,
                            source_key_value or None,
                            digest,
                            len(payload),
                            str(relative).replace("\\", "/"),
                        ),
                    )
                    if source_table == "recibos":
                        con.execute(
                            """UPDATE recibos SET legacy_pdf_backup_reference=%s,
                                      legacy_pdf_checksum=%s,legacy_pdf_size=%s
                               WHERE id=%s""",
                            (
                                str(destination.resolve()),
                                digest,
                                len(payload),
                                int(source_key_value),
                            ),
                        )
                    for report in report_map.get(filename, []):
                        table = str(report["source_table"])
                        key = source_key(table, report)
                        if table in ("report_history", "daily_reports"):
                            con.execute(
                                f"""UPDATE {table}
                                    SET legacy_pdf_backup_reference=%s,
                                        legacy_pdf_checksum=%s,
                                        legacy_pdf_size=%s
                                    WHERE id=%s""",
                                (
                                    str(destination.resolve()),
                                    digest,
                                    len(payload),
                                    int(report["id"]),
                                ),
                            )
                        else:
                            con.execute(
                                """UPDATE billing_shift_closures
                                   SET legacy_pdf_backup_reference=%s,
                                       legacy_pdf_checksum=%s,
                                       legacy_pdf_size=%s
                                   WHERE source_instance_id=%s AND turn_id=%s""",
                                (
                                    str(destination.resolve()),
                                    digest,
                                    len(payload),
                                    report["source_instance_id"],
                                    int(report["turn_id"]),
                                ),
                            )
                        con.execute(
                            """UPDATE report_document_migration
                               SET source_pdf_size=%s,source_pdf_hash=%s,
                                   backup_location=%s,backup_hash=%s,
                                   backup_verified=TRUE,updated_at=NOW()
                               WHERE source_table=%s AND source_key=%s""",
                            (
                                len(payload),
                                digest,
                                str(relative).replace("\\", "/"),
                                digest,
                                table,
                                key,
                            ),
                        )
                manifest_entry = {
                    "filename": filename,
                    "document_type": document_type,
                    "source_table": source_table,
                    "source_key": source_key_value,
                    "sha256": digest,
                    "size_bytes": len(payload),
                    "relative_path": str(relative).replace("\\", "/"),
                }
                manifest.write(
                    json.dumps(manifest_entry, ensure_ascii=False) + "\n"
                )
                counters["files"] += 1
                counters["bytes"] += len(payload)
                counters[document_type] += 1
            offset += len(batch)
    result = {
        "counts": dict(counters),
        "manifest": str(manifest_path),
        "archive_root": str(archive_root),
    }
    write_json(run_dir / "binary_backup_summary.json", result)
    return result


def verify_every_binary_is_backed_up(archive_root: Path) -> dict[str, Any]:
    missing = []
    verified = 0
    with app.db_connect() as con:
        rows = con.execute(
            """SELECT p.filename,OCTET_LENGTH(p.file_data) AS size_bytes,
                      ENCODE(DIGEST(p.file_data,'sha256'),'hex') AS sha256,
                      e.archive_relative_path
               FROM pdf_storage p
               LEFT JOIN document_external_files e
                 ON e.filename=p.filename
                AND e.sha256=ENCODE(DIGEST(p.file_data,'sha256'),'hex')
                AND e.status='AVAILABLE'
               ORDER BY p.filename"""
        ).fetchall()
    for raw in rows:
        row = dict(raw)
        relative = str(row.get("archive_relative_path") or "")
        candidate = archive_root / relative if relative else None
        if (
            not candidate
            or not candidate.is_file()
            or candidate.stat().st_size != int(row["size_bytes"])
            or file_sha256(candidate) != str(row["sha256"])
        ):
            missing.append(str(row["filename"]))
        else:
            verified += 1
    if missing:
        raise MigrationStopped(
            f"{len(missing)} binarios no tienen respaldo verificable."
        )
    return {"verified": verified, "missing": 0}


def remove_exact_binaries_and_reclaim(
    run_dir: Path, archive_root: Path
) -> dict[str, Any]:
    verification = verify_every_binary_is_backed_up(archive_root)
    deleted = 0
    deleted_bytes = 0
    batch_id = str(uuid.uuid4())
    while True:
        with app.db_connect() as con:
            rows = con.execute(
                """SELECT filename,file_data
                   FROM pdf_storage ORDER BY filename LIMIT 25 FOR UPDATE"""
            ).fetchall()
            if not rows:
                break
            for raw in rows:
                filename = str(raw["filename"])
                payload = bytes(raw["file_data"])
                digest = bytes_sha256(payload)
                external = con.execute(
                    """SELECT archive_relative_path,size_bytes
                       FROM document_external_files
                       WHERE filename=%s AND sha256=%s AND status='AVAILABLE'
                       ORDER BY verified_at DESC LIMIT 1""",
                    (filename, digest),
                ).fetchone()
                if not external:
                    raise MigrationStopped(
                        f"No existe respaldo registrado para {filename}."
                    )
                candidate = archive_root / str(
                    external["archive_relative_path"]
                )
                if (
                    not candidate.is_file()
                    or candidate.stat().st_size != len(payload)
                    or file_sha256(candidate) != digest
                ):
                    raise MigrationStopped(
                        f"El respaldo cambió antes de eliminar {filename}."
                    )
                removed = con.execute(
                    """DELETE FROM pdf_storage
                       WHERE filename=%s
                       RETURNING OCTET_LENGTH(file_data) AS bytes""",
                    (filename,),
                ).fetchone()
                if not removed:
                    raise MigrationStopped(
                        f"No se eliminó el binario exacto {filename}."
                    )
                deleted += 1
                deleted_bytes += int(removed["bytes"] or 0)
    with app.db_connect() as con:
        remaining = int(
            con.execute(
                "SELECT COUNT(*) AS value FROM pdf_storage"
            ).fetchone()["value"]
        )
        if remaining:
            raise MigrationStopped(
                f"Quedan {remaining} binarios; no se recuperará el TOAST."
            )
        con.execute(
            """UPDATE report_history r
               SET document_storage_mode=CASE
                 WHEN EXISTS(
                   SELECT 1 FROM report_document_versions v
                   WHERE v.source_table='report_history'
                     AND v.source_key=r.id::text AND v.is_current=TRUE
                 ) THEN 'SNAPSHOT' ELSE 'LEGACY_PDF' END"""
        )
        con.execute(
            """UPDATE daily_reports r
               SET document_storage_mode=CASE
                 WHEN EXISTS(
                   SELECT 1 FROM report_document_versions v
                   WHERE v.source_table='daily_reports'
                     AND v.source_key=r.id::text AND v.is_current=TRUE
                 ) THEN 'SNAPSHOT' ELSE 'LEGACY_PDF' END"""
        )
        con.execute(
            """UPDATE billing_shift_closures r
               SET document_storage_mode=CASE
                 WHEN EXISTS(
                   SELECT 1 FROM report_document_versions v
                   WHERE v.source_table='billing_shift_closures'
                     AND v.source_key=(
                       r.source_instance_id || '|' || r.turn_id::text
                     ) AND v.is_current=TRUE
                 ) THEN 'SNAPSHOT' ELSE 'LEGACY_PDF' END
               WHERE report_filename IS NOT NULL"""
        )
        con.execute(
            """UPDATE report_document_migration
               SET migration_status=CASE
                     WHEN render_verified AND backup_verified THEN 'VERIFIED'
                     WHEN backup_verified THEN 'NEEDS_REVIEW'
                     ELSE migration_status END,
                   deletion_batch_id=%s,pdf_deleted_at=NOW(),updated_at=NOW()
               WHERE backup_verified=TRUE""",
            (batch_id,),
        )
    result = {
        "pre_delete_verified": verification["verified"],
        "deleted_files": deleted,
        "deleted_bytes": deleted_bytes,
        "remaining": 0,
        "truncated_after_exact_delete": False,
        "batch_id": batch_id,
    }
    write_json(run_dir / "binary_deletion_summary.json", result)
    return result


def cleanup_expired_sessions(run_dir: Path, retention_days: int = 90):
    cutoff = datetime.now() - timedelta(days=max(1, int(retention_days)))
    with app.db_connect() as con:
        active_before = int(
            con.execute(
                "SELECT COUNT(*) AS value FROM active_sessions WHERE is_active=1"
            ).fetchone()["value"]
        )
        candidates = _rows(
            con,
            """SELECT * FROM active_sessions
               WHERE is_active<>1
                 AND COALESCE(NULLIF(logout_at,''),NULLIF(last_seen,'')) ~
                     '^\\d{4}-\\d{2}-\\d{2}'
                 AND COALESCE(NULLIF(logout_at,''),NULLIF(last_seen,''))::timestamp
                     < %s
               ORDER BY last_seen""",
            (cutoff,),
        )
    backup_path = run_dir / "expired_sessions_backup.json"
    write_json(backup_path, candidates)
    deleted = 0
    for row in candidates:
        with app.db_connect() as con:
            result = con.execute(
                """DELETE FROM active_sessions
                   WHERE session_id=%s AND is_active<>1 RETURNING session_id""",
                (row["session_id"],),
            ).fetchone()
            deleted += int(result is not None)
    with app.db_connect() as con:
        active_after = int(
            con.execute(
                "SELECT COUNT(*) AS value FROM active_sessions WHERE is_active=1"
            ).fetchone()["value"]
        )
    if active_before != active_after:
        raise MigrationStopped("La limpieza alteró sesiones activas.")
    return {
        "retention_days": retention_days,
        "candidates": len(candidates),
        "deleted": deleted,
        "active_unchanged": active_before == active_after,
        "backup": str(backup_path),
    }


def exact_duplicate_indexes() -> list[dict]:
    with app.db_connect() as con:
        rows = _rows(
            con,
            """SELECT schemaname,tablename,indexname,indexdef
               FROM pg_indexes WHERE schemaname='public'
               ORDER BY tablename,indexname""",
        )
    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        definition = re.sub(
            r"^CREATE (UNIQUE )?INDEX \\S+ ON ",
            lambda match: "CREATE " + (match.group(1) or "") + "INDEX ON ",
            str(row["indexdef"]),
            flags=re.IGNORECASE,
        )
        grouped[(str(row["tablename"]), definition)].append(row)
    return [
        {"table": key[0], "indexes": [row["indexname"] for row in values]}
        for key, values in grouped.items()
        if len(values) > 1
    ]


def reconcile(initial: dict, final: dict) -> dict[str, Any]:
    checks = {
        "receipt_count": (
            initial["counts"]["receipts"] == final["counts"]["receipts"]
        ),
        "receipt_item_count": (
            initial["counts"]["receipt_items"]
            == final["counts"]["receipt_items"]
        ),
        "receipt_amount": (
            round(initial["receipt_amount"], 2)
            == round(final["receipt_amount"], 2)
        ),
        "report_history_count": (
            initial["counts"]["report_history"]
            == final["counts"]["report_history"]
        ),
        "daily_report_count": (
            initial["counts"]["daily_reports"]
            == final["counts"]["daily_reports"]
        ),
        "shift_report_count": (
            initial["counts"]["shift_reports"]
            == final["counts"]["shift_reports"]
        ),
        "binary_storage_empty": final["pdf_storage"]["rows"] == 0,
    }
    if not all(checks.values()):
        raise MigrationStopped(
            "La reconciliación detectó una diferencia funcional."
        )
    return {"checks": checks, "ok": True}


def projection() -> dict[str, Any]:
    with app.db_connect() as con:
        samples = _rows(
            con,
            """SELECT captured_at,database_size_bytes
               FROM database_capacity_history
               ORDER BY captured_at DESC LIMIT 13""",
        )
    if len(samples) < 2:
        return {
            "available": False,
            "reason": "Se requieren al menos dos muestras mensuales.",
        }
    newest, oldest = samples[0], samples[-1]
    months = max(
        1.0,
        (
            newest["captured_at"] - oldest["captured_at"]
        ).total_seconds()
        / (30.4375 * 86400),
    )
    growth = (
        int(newest["database_size_bytes"])
        - int(oldest["database_size_bytes"])
    ) / months
    free = DATABASE_LIMIT_BYTES - int(newest["database_size_bytes"])
    return {
        "available": growth > 0,
        "monthly_growth_bytes": growth,
        "estimated_months_to_limit": free / growth if growth > 0 else None,
        "note": "Estimación basada en muestras históricas de capacidad.",
    }


def run_all() -> dict[str, Any]:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_root = default_archive_root()
    run_dir = archive_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    with migration_lock():
        initial = capture_capacity()
        write_json(run_dir / "capacity_initial.json", initial)
        logical = logical_backup(run_dir)
        apply_migrations_twice()
        save_capacity_sample(initial, f"Antes de migración documental {run_id}")
        report_sample = migrate_report_snapshots(
            run_dir,
            limit=5,
            output_name="report_snapshot_sample.json",
        )
        if report_sample["counts"].get("needs_review", 0):
            raise MigrationStopped(
                "El lote piloto de reportes no superó la validación."
            )
        report_result = migrate_report_snapshots(run_dir)
        backup_result = backup_all_binary_documents(run_dir, archive_root)
        backup_verification = verify_every_binary_is_backed_up(archive_root)
        deletion_result = remove_exact_binaries_and_reclaim(
            run_dir, archive_root
        )
        session_result = cleanup_expired_sessions(run_dir)
        duplicate_indexes = exact_duplicate_indexes()
        final = capture_capacity()
        save_capacity_sample(final, f"Después de migración documental {run_id}")
        reconciliation = reconcile(initial, final)
        growth = projection()
        summary = {
            "run_id": run_id,
            "run_dir": str(run_dir),
            "initial": initial,
            "logical_backup": str(logical),
            "reports": report_result,
            "report_sample": report_sample,
            "binary_backup": backup_result,
            "backup_verification": backup_verification,
            "binary_deletion": deletion_result,
            "sessions": session_result,
            "duplicate_indexes": duplicate_indexes,
            "indexes_deleted": 0,
            "reconciliation": reconciliation,
            "final": final,
            "projection": growth,
        }
        write_json(run_dir / "migration_final_summary.json", summary)
        return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mide y clasifica sin modificar datos.",
    )
    args = parser.parse_args()
    try:
        if args.dry_run:
            payload = {
                "capacity": capture_capacity(),
                "report_records": len(report_records()),
                "duplicate_indexes": exact_duplicate_indexes(),
            }
            print(json.dumps(json_safe(payload), ensure_ascii=False, indent=2))
            return 0
        summary = run_all()
        compact = {
            "run_dir": summary["run_dir"],
            "initial_bytes": summary["initial"]["database_size_bytes"],
            "final_bytes": summary["final"]["database_size_bytes"],
            "reports": summary["reports"]["counts"],
            "backup": summary["binary_backup"]["counts"],
            "deleted": summary["binary_deletion"],
            "sessions": summary["sessions"],
            "reconciliation": summary["reconciliation"],
        }
        print(json.dumps(json_safe(compact), ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": traceback.format_exc(),
                },
                ensure_ascii=False,
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
