BEGIN;

ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_at TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_by TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS id BIGSERIAL;

ALTER TABLE billing_batch_receipts
    DROP CONSTRAINT IF EXISTS billing_batch_receipts_pkey;
ALTER TABLE billing_batch_receipts
    ADD CONSTRAINT billing_batch_receipts_pkey PRIMARY KEY (id);

ALTER TABLE billing_batches ALTER COLUMN status SET DEFAULT 'PENDIENTE';

UPDATE billing_batches SET status = 'PENDIENTE' WHERE status = 'BORRADOR';
UPDATE billing_batches
SET sent_at = COALESCE(sent_at, last_exported_at, updated_at),
    sent_by = COALESCE(sent_by, updated_by)
WHERE status IN ('ENVIADO', 'CERRADO');

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM billing_batch_receipts
        WHERE included = 1 AND recibo_id IS NOT NULL
        GROUP BY recibo_id
        HAVING COUNT(DISTINCT batch_id) > 1
    ) THEN
        RAISE WARNING 'Listados ARS: existen recibos activos en más de un listado; no se creó la restricción global por recibo.';
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_batch_receipts_receipt_global
            ON billing_batch_receipts (recibo_id)
            WHERE included = 1 AND recibo_id IS NOT NULL;
    END IF;
END
$$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM billing_batch_receipts
        WHERE included = 1 AND admission_attention_id IS NOT NULL
        GROUP BY admission_source_instance_id, admission_attention_id
        HAVING COUNT(DISTINCT batch_id) > 1
    ) THEN
        RAISE WARNING 'Listados ARS: existen atenciones activas en más de un listado; no se creó la restricción global por atención.';
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_batch_receipts_admission_global
            ON billing_batch_receipts (
                admission_source_instance_id,
                admission_attention_id
            )
            WHERE included = 1 AND admission_attention_id IS NOT NULL;
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_billing_batches_saved_filters
    ON billing_batches (status, period_year DESC, period_month DESC, ars);

ALTER TABLE billing_batches ENABLE ROW LEVEL SECURITY;
ALTER TABLE billing_batch_receipts ENABLE ROW LEVEL SECURITY;

COMMIT;
