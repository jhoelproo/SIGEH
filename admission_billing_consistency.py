"""Shared central identity and claim predicates for Admission/Billing reads."""

CURRENT_OPERATIONAL_SHIFT_SQL = """
    SELECT session.operational_source_id, session.turn_id
    FROM admission_operational_sessions session
    JOIN sigeh_product_state product
      ON product.singleton=1
     AND product.production_epoch_id=session.production_epoch_id
    WHERE session.status='ACTIVE'
    ORDER BY session.updated_at DESC
    LIMIT 1
"""


def ars_enabled_sql(alias="p"):
    """Mirror the catalog rule used by final eligibility, before pagination."""
    if alias != "p":
        raise ValueError("Unsupported projection alias")
    return """COALESCE((
        SELECT ars.billing_enabled FROM ars
        WHERE UPPER(TRIM(ars.nombre))=UPPER(TRIM(p.canonical_ars))
        ORDER BY ars.id LIMIT 1
    ),TRUE)"""


def foreign_claim_sql(alias):
    """Three parameters: login session, station, authenticated username.

    An unprocessed claim can resume on its original station/user. Privilege
    alone never steals a live claim. Expiration/reclaim is enforced atomically
    by the existing UPSERT, not by deleting reservations.
    """
    if alias not in {"c", "claim"}:
        raise ValueError("Unsupported claim alias")
    return f"""{alias}.expires_at>NOW()
        AND {alias}.receipt_id IS NULL AND {alias}.processed_at IS NULL
        AND {alias}.session_id<>%s
        AND NOT (COALESCE({alias}.station_id,'')=%s AND {alias}.claimed_by=%s)"""


def normalized_name_sql(column):
    if column not in {"p.patient_name", "%s"}:
        raise ValueError("Unsupported search expression")
    return (
        "TRANSLATE(UPPER(REGEXP_REPLACE(TRIM(COALESCE("
        + column
        + ",'')), '\\s+', ' ', 'g')), 'ÁÉÍÓÚÜÑáéíóúüñ', 'AEIOUUNAEIOUUN')"
    )
