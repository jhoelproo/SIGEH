ALTER TABLE billing_batch_receipts
    DROP CONSTRAINT IF EXISTS billing_batch_receipts_pkey;

ALTER TABLE billing_batch_receipts
    ALTER COLUMN recibo_id DROP NOT NULL;

ALTER TABLE billing_batch_receipts
    ADD COLUMN IF NOT EXISTS admission_source_instance_id TEXT;

ALTER TABLE billing_batch_receipts
    ADD COLUMN IF NOT EXISTS admission_attention_id BIGINT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_batch_receipts_receipt
    ON billing_batch_receipts (batch_id, recibo_id)
    WHERE recibo_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_batch_receipts_admission
    ON billing_batch_receipts (
        batch_id, admission_source_instance_id, admission_attention_id
    )
    WHERE admission_attention_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_billing_batch_receipts_admission_sent
    ON billing_batch_receipts (
        admission_source_instance_id, admission_attention_id, batch_id
    )
    WHERE included=1 AND admission_attention_id IS NOT NULL;
