"""Identity-only handoff; eligibility remains owned by central billing."""

from uuid import UUID


def has_inherited_receipt(row: dict) -> bool:
    return bool(
        row.get("linked_receipt_id")
        and row.get("receipt_inheritance_state") == "HEREDADA_PROCESADA"
        and row.get("receipt_origin_turn") is not None
        and row.get("receipt_origin_turn") == row.get("turn_id")
    )


def exclude_current_draft(attentions: list, current: dict) -> list:
    global_id = str(current.get("global_attention_id") or "")
    if not global_id:
        return attentions
    return [item for item in attentions if str(item.global_attention_id) != global_id]


def history_identity(row: dict) -> dict:
    """Never resolve a visible local number against a different station."""
    try:
        global_id = str(UUID(str(row.get("global_attention_id") or "")))
    except ValueError as exc:
        raise ValueError(
            "La atención todavía no tiene identidad central. "
            "Sincroniza Admisión y actualiza el Historial antes de enviarla."
        ) from exc
    return {
        "global_attention_id": global_id,
        "attention_id": 0,
        "source_instance_id": "",
    }


def _projection_blocks_handoff(projection: dict) -> bool:
    return bool(
        projection.get("is_deleted")
        or projection.get("claimed_elsewhere")
        or str(projection.get("source_status") or "").upper() == "ANULADA"
    )


def billing_destination(result: dict) -> str:
    """Select an action only from a freshly evaluated central result."""
    if _projection_blocks_handoff(result.get("_projection") or {}):
        return "blocked"
    if result.get("eligible"):
        return "claim"
    receipt_allowed = (
        result.get("reason_code") == "RECEIPT_PENDING"
        and result.get("can_continue_receipt")
    ) or (
        result.get("reason_code") == "ALREADY_BILLED" and result.get("can_open_receipt")
    )
    if result.get("receipt_id") and receipt_allowed:
        return "receipt"
    return "blocked"
