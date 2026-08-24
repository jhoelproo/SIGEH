BEGIN;

SELECT pg_advisory_xact_lock(hashtext('billing-batches-sent-snapshot-v1'));

ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS last_export_signature TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_receipt_count INTEGER;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_total NUMERIC(14,2);
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_invoice_number TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_ncf TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_ars TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_period_year INTEGER;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS sent_period_month INTEGER;

UPDATE billing_batches SET status='ENVIADO' WHERE status='CERRADO';

WITH totals AS (
    SELECT batch_id,
           COUNT(*) FILTER (WHERE included=1) AS receipt_count,
           COALESCE(SUM(total_snapshot) FILTER (WHERE included=1),0) AS total
    FROM billing_batch_receipts
    GROUP BY batch_id
)
UPDATE billing_batches b
SET sent_receipt_count=COALESCE(b.sent_receipt_count,t.receipt_count,0),
    sent_total=COALESCE(b.sent_total,t.total,0),
    sent_invoice_number=COALESCE(b.sent_invoice_number,b.invoice_number,''),
    sent_ncf=COALESCE(b.sent_ncf,b.ncf,''),
    sent_ars=COALESCE(b.sent_ars,b.ars),
    sent_period_year=COALESCE(b.sent_period_year,b.period_year),
    sent_period_month=COALESCE(b.sent_period_month,b.period_month)
FROM totals t
WHERE b.id=t.batch_id AND b.status IN ('ENVIADO','CERRADO');

UPDATE billing_batches b
SET sent_receipt_count=COALESCE(b.sent_receipt_count,0),
    sent_total=COALESCE(b.sent_total,0),
    sent_invoice_number=COALESCE(b.sent_invoice_number,b.invoice_number,''),
    sent_ncf=COALESCE(b.sent_ncf,b.ncf,''),
    sent_ars=COALESCE(b.sent_ars,b.ars),
    sent_period_year=COALESCE(b.sent_period_year,b.period_year),
    sent_period_month=COALESCE(b.sent_period_month,b.period_month)
WHERE b.status IN ('ENVIADO','CERRADO');

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname='billing_batches_sent_snapshot_check'
          AND conrelid='public.billing_batches'::regclass
    ) THEN
        ALTER TABLE billing_batches
            ADD CONSTRAINT billing_batches_sent_snapshot_check CHECK (
                status NOT IN ('ENVIADO','CERRADO') OR (
                    sent_receipt_count IS NOT NULL AND sent_receipt_count >= 0
                    AND sent_total IS NOT NULL AND sent_total >= 0
                    AND sent_invoice_number IS NOT NULL
                    AND sent_ncf IS NOT NULL
                    AND NULLIF(TRIM(sent_ars),'') IS NOT NULL
                    AND sent_period_year IS NOT NULL
                    AND sent_period_month BETWEEN 1 AND 12
                )
            );
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_billing_batches_status_period
    ON billing_batches(status,period_year,period_month);
CREATE INDEX IF NOT EXISTS idx_billing_batches_status_ars_period
    ON billing_batches(status,ars,period_year,period_month);
CREATE INDEX IF NOT EXISTS idx_billing_batches_sent_at
    ON billing_batches(sent_at DESC) WHERE status='ENVIADO';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM billing_batch_receipts
        WHERE included=1 AND recibo_id IS NOT NULL
        GROUP BY recibo_id HAVING COUNT(DISTINCT batch_id)>1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_batch_receipts_receipt_global
            ON billing_batch_receipts(recibo_id)
            WHERE included=1 AND recibo_id IS NOT NULL;
    ELSE
        RAISE WARNING 'Listados ARS: hay recibos duplicados históricos; no se creó la restricción global por recibo';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM billing_batch_receipts
        WHERE included=1 AND admission_attention_id IS NOT NULL
        GROUP BY admission_source_instance_id,admission_attention_id
        HAVING COUNT(DISTINCT batch_id)>1
    ) THEN
        CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_batch_receipts_admission_global
            ON billing_batch_receipts(admission_source_instance_id,admission_attention_id)
            WHERE included=1 AND admission_attention_id IS NOT NULL;
    ELSE
        RAISE WARNING 'Listados ARS: hay atenciones duplicadas históricas; no se creó la restricción global por atención';
    END IF;
END
$$;

COMMIT;
