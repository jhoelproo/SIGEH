"""Report comparisons, saved filter values and centrally linked receipt evidence."""

from collections import Counter, defaultdict
import json
from uuid import UUID
from psycopg2 import sql

from admission_statistical_reports import ARS_MODES, COVERAGE_MODES
from authorization_validation import numeric_authorization


VIEW_PERIODS = ("Diario", "Semanal", "Mensual", "Anual", "Rango")
VIEW_SPECIALTIES = ("TODAS", "GENERAL", "PEDIATRIA", "GINECOLOGIA", "OTRAS")
VIEW_NAME_LIMIT = 80


def normalize_view(value):
    if not isinstance(value, dict):
        raise ValueError("El formato de la vista no es válido.")
    name = str(value.get("name") or "").strip()
    if not name or len(name) > VIEW_NAME_LIMIT:
        raise ValueError("Escribe un nombre de hasta 80 caracteres para la vista.")
    result = {
        "name": name,
        "period": value.get("period", "Diario"),
        "specialty": value.get("specialty", "TODAS"),
        "coverage": value.get("coverage", "TODAS"),
        "ars_mode": value.get("ars_mode", "TODAS"),
        "selected_ars": _view_ars(value.get("selected_ars", [])),
    }
    _validate_view_choices(result)
    if result["ars_mode"] != "TODAS" and not result["selected_ars"]:
        raise ValueError("Selecciona al menos una ARS para guardar esta vista.")
    return result


def _view_ars(selected_ars):
    if not isinstance(selected_ars, (list, tuple)):
        raise ValueError("La selección de ARS de la vista no es válida.")
    return list(dict.fromkeys(str(v).strip() for v in selected_ars if str(v).strip()))


def _validate_view_choices(result):
    allowed = {
        "period": VIEW_PERIODS,
        "specialty": VIEW_SPECIALTIES,
        "coverage": COVERAGE_MODES,
        "ars_mode": ARS_MODES,
    }
    if any(
        not isinstance(result[key], str) or result[key] not in choices
        for key, choices in allowed.items()
    ):
        raise ValueError("La vista contiene filtros que ya no son compatibles.")


def _comparison_row(label, actual, reference):
    difference = actual - reference
    percentage = (
        f"{difference * 100 / reference:.2f}%"
        if reference
        else ("Sin base" if actual else "0.00%")
    )
    return label, actual, reference, difference, percentage


def comparison_rows(current, reference):
    summary_keys = (
        ("Total pacientes", "total_patients"),
        ("Asegurados", "insured_patients"),
        ("Sin seguro", "uninsured_patients"),
    )
    rows = [
        _comparison_row(label, current.summary[key], reference.summary[key])
        for label, key in summary_keys
    ]
    for label, key in (("ARS", "by_ars"), ("Especialidad", "by_specialty")):
        actual, previous = dict(current.summary[key]), dict(reference.summary[key])
        rows.extend(
            _comparison_row(
                f"{label} · {name}", actual.get(name, 0), previous.get(name, 0)
            )
            for name in sorted(actual.keys() | previous.keys())
        )
    return rows


def report_record_key(row):
    global_id = str(row.get("global_attention_id") or "").strip()
    if global_id:
        return str(UUID(global_id))
    return f"{row.get('source_instance_id', '')}/{row.get('attention_id', 0)}"


def _requested_identity(row):
    raw_global = str(row.get("global_attention_id") or "").strip()
    identity = {
        "global_id": str(UUID(raw_global)) if raw_global else None,
        "source_id": str(row.get("source_instance_id") or ""),
        "attention_id": int(row.get("attention_id") or 0),
    }
    if not raw_global and (not identity["source_id"] or identity["attention_id"] <= 0):
        return None
    return identity


def load_billing_followup(connection, records):
    from CALCULOS_QT import admission_receipt_identity_sql

    wanted = {}
    for row in records:
        identity = _requested_identity(row)
        if identity is not None:
            wanted[report_record_key(row)] = identity
    if not wanted:
        return []
    identity_sql = admission_receipt_identity_sql("r", "p")
    query = sql.SQL("""
        WITH wanted AS (
            SELECT * FROM jsonb_to_recordset(%s::jsonb)
            AS w(global_id UUID,source_id TEXT,attention_id BIGINT)
        )
        SELECT p.global_attention_id::TEXT AS global_attention_id,
               p.source_instance_id,p.attention_id,p.readiness,p.readiness_reasons,
               p.is_deleted,p.source_status,
               r.id AS receipt_id,r.numero,r.estado_facturacion,r.dx,
               r.numero_autorizacion,r.tipo_cobertura,r.ars
        FROM wanted w JOIN admission_attention_projection p ON (
            (w.global_id IS NOT NULL AND p.global_attention_id=w.global_id)
            OR (w.global_id IS NULL AND p.source_instance_id=w.source_id
                AND p.attention_id=w.attention_id)
        )
        LEFT JOIN recibos r ON {identity} AND COALESCE(r.is_deleted,0)=0
        ORDER BY p.attention_id,r.id
    """).format(identity=sql.SQL(identity_sql))
    return [
        dict(row)
        for row in connection.execute(
            query, (json.dumps(list(wanted.values())),)
        ).fetchall()
    ]


def _readiness_issues(evidence):
    if not evidence:
        return ("La atención todavía no se ha confirmado en el servidor.",)
    if evidence.get("is_deleted") or str(
        evidence.get("source_status") or "ACTIVA"
    ).upper() not in ("ACTIVA", "PENDIENTE"):
        return ("La atención está anulada o inactiva en el servidor.",)
    if evidence.get("readiness") == "LISTA":
        return ()
    reasons = _readiness_reasons(evidence.get("readiness_reasons"))
    return reasons or ("Revisar los datos de la atención.",)


def _readiness_reasons(reasons):
    if isinstance(reasons, str):
        try:
            reasons = json.loads(reasons)
        except (ValueError, TypeError):
            reasons = ()
    if not isinstance(reasons, (tuple, list)):
        reasons = ()
    return tuple(str(reason) for reason in reasons)


def _receipt_issues(receipts):
    issues = []
    for receipt in receipts:
        label = f"Recibo {receipt['numero']}"
        if not str(receipt.get("dx") or "").strip():
            issues.append(f"{label} sin diagnóstico.")
        if _authorization_missing(receipt):
            issues.append(f"{label} sin autorización numérica.")
    return issues


def _authorization_missing(receipt):
    if receipt.get("tipo_cobertura") == "NO_ASEGURADO":
        return False
    try:
        numeric_authorization(receipt.get("numero_autorizacion"), allow_empty=False)
    except ValueError:
        return True
    return False


def _billing_state(evidence, receipts):
    if not evidence:
        return "Sin confirmar"
    if not receipts:
        return "Sin recibo"
    statuses = {row.get("estado_facturacion") for row in receipts}
    if "FACTURADO" in statuses:
        return "Facturado"
    return "No facturado" if statuses == {"NO_FACTURADO"} else "Recibo pendiente"


def followup_rows(records, evidence):
    grouped = defaultdict(list)
    for item in evidence:
        grouped[report_record_key(item)].append(item)
    result = []
    for record in records:
        items = grouped[report_record_key(record)]
        receipts = [
            dict(item, id=item["receipt_id"])
            for item in items
            if item.get("receipt_id") is not None
        ]
        issues = (
            *_readiness_issues(items[0] if items else None),
            *_receipt_issues(receipts),
        )
        result.append(
            dict(
                record,
                billing_state=_billing_state(items, receipts),
                receipts=receipts,
                issues=tuple(dict.fromkeys(issues)),
            )
        )
    return result


def followup_summary(rows):
    counts = Counter(row["billing_state"] for row in rows)
    return {
        "patients": len(rows),
        "invoiced": counts["Facturado"],
        "missing_receipt": counts["Sin recibo"],
        "needs_correction": sum(bool(row["issues"]) for row in rows),
    }
