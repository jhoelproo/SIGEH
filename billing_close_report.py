"""Adapt the versioned canonical snapshot to existing report consumers."""

import json
import logging


def log_snapshot(snapshot, generated_at):
    keys = (
        "transition_id",
        "source",
        "turn",
        "reporting_baseline",
        "current_admissions",
        "linked_billed",
        "historical_billed",
        "historical_authorized",
        "pending_current",
        "pending_previous",
        "pending_historical",
        "total_pending",
        "receipt_count",
        "closure_report_schema_version",
    )
    fields = {key: snapshot[key] for key in keys}
    fields["generated_at"] = str(generated_at)
    logging.getLogger("hospital.billing.close").info(
        "CLOSE_REPORT_SNAPSHOT %s", json.dumps(fields, ensure_ascii=False)
    )


def report_data(snapshot, header):
    count = snapshot["receipt_count"]
    authorized = snapshot["authorized"]
    rate = 100 * authorized / count if count else 0.0
    return {
        **snapshot,
        "classification_version": 3,
        "closure": dict(header),
        "eligible_current": snapshot["current_admissions"],
        "authorized_current": snapshot["linked_current"],
        "new_pending": snapshot["pending_current"],
        "inherited_received": snapshot["previous_received"],
        "inherited_authorized": snapshot["previous_resolved"],
        "inherited_pending": snapshot["pending_previous"],
        "historical_pending": snapshot["pending_historical"],
        "pending_next": snapshot["total_pending"],
        "worked_applicable": count,
        "authorized_applicable": authorized,
        "authorization_rate": rate,
        "admitted": snapshot["current_admissions"],
        "invoiced": count,
        "pending": snapshot["total_pending"],
        "correction": 0,
        "amount": float(snapshot["amount"]),
        "completion_rate": rate,
        "productivity": [],
        "details": [],
    }


def persist_header(connection, event, snapshot):
    data = report_data(snapshot, {})
    columns = (
        "eligible_current",
        "authorized_current",
        "new_pending",
        "inherited_received",
        "inherited_authorized",
        "inherited_pending",
        "pending_next",
        "worked_applicable",
        "authorized_applicable",
        "authorization_rate",
    )
    return connection.execute(
        """UPDATE billing_shift_closures SET eligible_current=%s,authorized_current=%s,
            new_pending=%s,inherited_received=%s,inherited_authorized=%s,
            inherited_pending=%s,pending_next=%s,worked_applicable=%s,
            authorized_applicable=%s,authorization_rate=%s,
            classification_version=3,snapshot_created_at=%s
            WHERE source_instance_id=%s AND turn_id=%s RETURNING *""",
        tuple(data[column] for column in columns)
        + (snapshot["closed_at"], event.source_instance_id, int(event.turn_id)),
    ).fetchone()


def spreadsheet_summary(data):
    """Export quantities from the same frozen data, without patient lists."""
    labels = {
        "Recibos guardados durante el turno": "receipt_count",
        "Atenciones del turno": "current_admissions",
        "Atenciones del turno facturadas": "linked_current",
        "Recibos vinculados guardados durante el turno": "linked_billed",
        "Recibos con autorización válida": "authorized",
        "Importe de recibos del turno": "amount",
        "Pendientes del turno actual": "pending_current",
        "Pendientes del turno anterior": "pending_previous",
        "Heredadas históricas pendientes": "pending_historical",
        "Total pendiente al cierre": "total_pending",
        "Heredadas del turno anterior resueltas": "previous_resolved",
        "Heredadas históricas resueltas": "historical_resolved",
        "Recibos históricos sin vínculo": "historical_billed",
        "Históricos sin vínculo autorizados": "historical_authorized",
    }
    return {label: data[key] for label, key in labels.items()}
