"""Document presentation derives from confirmed billing, with local drafts kept visible."""


def receipt_document_state(
    document_state, billing_status, *, local_request_id=""
) -> str:
    if str(local_request_id or "").strip():
        return "PRELIMINAR"
    if str(billing_status or "").strip().upper() == "FACTURADO":
        return "FINAL"
    return str(document_state or "PRELIMINAR").strip().upper()


def billed_snapshot_needs_refresh(receipt, document_record) -> bool:
    if str(receipt.get("estado_facturacion") or "").upper() != "FACTURADO":
        return False
    snapshot = document_record.get("snapshot") or {}
    header = snapshot.get("header") or {}
    document = snapshot.get("document") or {}
    return any(
        section.get("document_state") != "FINAL"
        or section.get("billing_status") != "FACTURADO"
        for section in (header, document)
    )
