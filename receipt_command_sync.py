"""Central idempotency for durable local receipt commands."""

import re
import uuid

RECEIPT_SYNC_SCHEMA = """
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS local_request_id UUID;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS local_request_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_recibos_local_request_id
    ON recibos(local_request_id) WHERE local_request_id IS NOT NULL;
"""


def validate_request(identity: str, digest: str) -> str:
    normalized = str(uuid.UUID(identity))
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("La huella del recibo local no es válida.")
    return normalized


def find_confirmed_request(connection, identity: str, digest: str, username: str):
    normalized = validate_request(identity, digest)
    row = connection.execute(
        """SELECT id,numero,username,local_request_hash FROM recibos
           WHERE local_request_id=%s""",
        (normalized,),
    ).fetchone()
    if row is None:
        return None
    if row["username"] != username:
        raise PermissionError("El recibo local pertenece a otro usuario.")
    if row["local_request_hash"] != digest:
        raise ValueError("El contenido local no coincide con el confirmado.")
    return int(row["id"]), int(row["numero"])


def lock_receipt_request(connection, identity: str, digest: str, username: str):
    normalized = validate_request(identity, digest)
    connection.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
        (normalized,),
    )
    return find_confirmed_request(connection, normalized, digest, username)


def confirm_receipt_request(connection, identity: str, digest: str, receipt_id: int):
    normalized = validate_request(identity, digest)
    connection.execute(
        "UPDATE recibos SET local_request_id=%s,local_request_hash=%s WHERE id=%s",
        (normalized, digest, receipt_id),
    )
