"""Durable, at-most-once automatic submission of outgoing-turn workbooks."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from excel_artifact import artifact_lock


def _connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE IF NOT EXISTS turn_print_jobs(
        transition_id TEXT PRIMARY KEY,context TEXT NOT NULL,copies INTEGER NOT NULL,
        state TEXT NOT NULL DEFAULT 'PENDING',file_path TEXT NOT NULL DEFAULT '')""")
    con.commit()
    return con


def enqueue(path, context, copies):
    identity = str(context.get("transition_id") or "")
    if not identity:
        raise ValueError("La impresión requiere una transición confirmada.")
    with closing(_connect(path)) as con, con:
        con.execute(
            "INSERT OR IGNORE INTO turn_print_jobs(transition_id,context,copies) VALUES(?,?,?)",
            (identity, json.dumps(context, default=str), max(1, int(copies))),
        )


def jobs(path, state="PENDING"):
    with closing(_connect(path)) as con:
        rows = con.execute(
            "SELECT transition_id,context,copies,file_path FROM turn_print_jobs WHERE state=?",
            (state,),
        ).fetchall()
    return [
        dict(
            transition_id=row[0],
            context=json.loads(row[1]),
            copies=row[2],
            file_path=row[3],
        )
        for row in rows
    ]


def _set_state(path, identity, state, file_path=""):
    with closing(_connect(path)) as con, con:
        con.execute(
            "UPDATE turn_print_jobs SET state=?,file_path=? WHERE transition_id=?",
            (state, str(file_path), identity),
        )


def deliver(path, identity, generate, printer):
    # No SQLite transaction remains open while generating or printing files.
    with artifact_lock(str(path) + ".delivery"):
        job = next(
            (item for item in jobs(path) if item["transition_id"] == identity), None
        )
        if job is None:
            return False
        file_path = generate(job["context"])
        if not file_path:
            _set_state(path, identity, "EMPTY")
            return True
        _set_state(path, identity, "SUBMITTING", file_path)
        # A crash or uncertain spooler result must never trigger duplicate copies.
        # SUBMITTING survives restart and requires review instead of silent loss.
        if not printer(file_path, job["copies"]):
            return False
        _set_state(path, identity, "SUBMITTED", file_path)
        return True


def repair_unconfirmed_artifacts(path, generate, is_valid):
    """Recover missing artifacts without repeating an uncertain printer submission."""
    repaired = 0
    with artifact_lock(str(path) + ".delivery"):
        for job in jobs(path, "SUBMITTING"):
            if is_valid(job["file_path"]):
                continue
            file_path = generate(job["context"])
            if file_path and is_valid(file_path):
                _set_state(path, job["transition_id"], "SUBMITTING", file_path)
                repaired += 1
    return repaired
