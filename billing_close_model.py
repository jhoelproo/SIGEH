"""Versioned quantitative closure computed from one central database snapshot."""

from collections import Counter, defaultdict
from datetime import datetime
import re
from billing_inheritance_scope import HOSPITAL_TIMEZONE
from billing_money import money

REPORTING_BASELINE_AT = "2026-09-23T00:00:00-04:00"
SCHEMA_VERSION = 3
HOSPITAL_ZONE = HOSPITAL_TIMEZONE


def instant(value):
    parsed = (
        value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    )
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=HOSPITAL_ZONE)


def authorized(receipt):
    number = str(receipt.get("authorization") or "").strip()
    digits = re.sub(r"[^0-9]", "", number)
    return len(digits) >= 4 and bool(digits.strip("0"))


def effective_receipts(receipts):
    """The caller supplies current canonical receipt rows, not revision history."""
    result: dict[str, dict] = {}
    for receipt in receipts:
        if receipt.get("deleted") or receipt.get("invalid"):
            continue
        identity = str(receipt["id"])
        if identity in result and result[identity] != receipt:
            raise ValueError("Dos versiones efectivas distintas del mismo recibo.")
        result[identity] = dict(receipt)
    return list(result.values())


def pending_group(admission, current_turn, previous_turn):
    turn = int(admission["turn_id"])
    if turn == int(current_turn):
        return "pending_current"
    if previous_turn is not None and turn == int(previous_turn):
        return "pending_previous"
    return "pending_historical"


def _count_admissions(rows, linked, turn, previous_turn, cutoff, counts, by_ars):
    pending = {}
    for identity, row in rows.items():
        if row.get("excluded"):
            continue
        if int(row["turn_id"]) == int(turn):
            counts["current_admissions"] += 1
            if identity in linked:
                counts["linked_current"] += 1
        if identity not in linked and instant(row["created_at"]) >= cutoff:
            group = pending_group(row, turn, previous_turn)
            counts[group] += 1
            pending[identity] = group
            by_ars[str(row.get("ars") or "Sin ARS")]["pending"] += 1
    return pending


def _count_authorization(receipt, identity, counts, bucket):
    if not authorized(receipt):
        return
    counts["authorized"] += 1
    bucket["authorized"] += 1
    if not identity:
        counts["historical_authorized"] += 1


def _count_receipts(period, rows, counts, by_ars):
    worked = []
    amount = money(0)
    for receipt in period:
        identity = str(receipt.get("attention_id") or "")
        if receipt.get("attention_excluded") or rows.get(identity, {}).get("excluded"):
            continue
        kind = "linked_billed" if identity else "historical_billed"
        counts[kind] += 1
        bucket = by_ars[str(receipt.get("ars") or "Sin ARS")]
        bucket["worked"] += 1
        _count_authorization(receipt, identity, counts, bucket)
        worked.append(str(receipt["id"]))
        amount += money(receipt.get("total") or 0)
    return worked, amount


def _ars_summary(by_ars):
    return [
        {
            "ars": ars,
            "label": ars,
            "worked": value["worked"],
            "authorized": value["authorized"],
            "pending": value["pending"],
        }
        for ars, value in sorted(by_ars.items())
    ]


def _inherited_movement(rows, applicable, turn, previous_turn, start, cutoff):
    before = _linked_ids(_before(applicable, start))
    saved = _linked_ids(applicable)
    counts: Counter[str] = Counter()
    for identity, row in rows.items():
        if (
            row.get("excluded")
            or identity in before
            or instant(row["created_at"]) < cutoff
        ):
            continue
        group = pending_group(row, turn, previous_turn).replace("pending_", "")
        if group == "current":
            continue
        counts[group + "_received"] += 1
        if identity in saved:
            counts[group + "_resolved"] += 1
    return {
        key: counts[key]
        for key in (
            "previous_received",
            "previous_resolved",
            "historical_received",
            "historical_resolved",
        )
    }


def _before(rows, boundary):
    return [row for row in rows if instant(row["created_at"]) < boundary]


def _linked_ids(receipts):
    return {str(row["attention_id"]) for row in receipts if row.get("attention_id")}


def _summary_counts(counts):
    keys = (
        "current_admissions",
        "linked_current",
        "linked_billed",
        "historical_billed",
        "historical_authorized",
        "authorized",
        "pending_current",
        "pending_previous",
        "pending_historical",
    )
    result = {key: counts[key] for key in keys}
    result["total_pending"] = sum(
        counts[key] for key in keys if key.startswith("pending_")
    )
    return result


def build_close_snapshot(
    *,
    source,
    turn,
    previous_turn,
    started_at,
    closed_at,
    admissions,
    receipts,
    baseline=REPORTING_BASELINE_AT,
    transition_id="",
    canonical_receipt_count=None,
):
    start, end, cutoff = instant(started_at), instant(closed_at), instant(baseline)
    if end < start:
        raise ValueError("El cierre no puede preceder al inicio del turno.")
    rows = {str(row["id"]): dict(row) for row in _before(admissions, end)}
    applicable = _before(effective_receipts(receipts), end)
    linked = _linked_ids(applicable)
    period = [row for row in applicable if instant(row["created_at"]) >= start]
    counts: Counter[str] = Counter()
    by_ars: defaultdict[str, Counter[str]] = defaultdict(Counter)
    pending = _count_admissions(
        rows, linked, turn, previous_turn, cutoff, counts, by_ars
    )
    worked, amount = _count_receipts(period, rows, counts, by_ars)
    result = _summary_counts(counts)
    result.update(
        _inherited_movement(rows, applicable, turn, previous_turn, start, cutoff)
    )
    result.update(
        {
            "closure_report_schema_version": SCHEMA_VERSION,
            "source": str(source),
            "turn": int(turn),
            "transition_id": str(transition_id),
            "started_at": start.isoformat(),
            "closed_at": end.isoformat(),
            "reporting_baseline": cutoff.isoformat(),
            "receipt_ids": sorted(worked),
            "receipt_count": len(worked),
            "pending_ids": pending,
            "amount": str(amount),
            "by_ars": _ars_summary(by_ars),
        }
    )
    if canonical_receipt_count is not None and len(worked) != int(
        canonical_receipt_count
    ):
        raise ValueError(
            "Los recibos del cierre no concuerdan con la cantidad central."
        )
    return result
