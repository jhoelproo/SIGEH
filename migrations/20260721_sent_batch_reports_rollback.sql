BEGIN;

DROP INDEX IF EXISTS idx_billing_batches_sent_at;
DROP INDEX IF EXISTS idx_billing_batches_status_period;
ALTER TABLE billing_batches DROP CONSTRAINT IF EXISTS billing_batches_sent_snapshot_check;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_period_month;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_period_year;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_ars;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_ncf;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_invoice_number;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_total;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS sent_receipt_count;
ALTER TABLE billing_batches DROP COLUMN IF EXISTS last_export_signature;

COMMIT;
