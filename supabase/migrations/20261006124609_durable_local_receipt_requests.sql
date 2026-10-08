-- Idempotent confirmation of durable local receipts.

ALTER TABLE recibos ADD COLUMN IF NOT EXISTS local_request_id UUID;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS local_request_hash TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS uq_recibos_local_request_id
    ON recibos(local_request_id) WHERE local_request_id IS NOT NULL;
