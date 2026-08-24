BEGIN;

ALTER TABLE billing_batches
    ALTER COLUMN status SET DEFAULT 'PENDIENTE';

ALTER TABLE billing_batches
    DROP CONSTRAINT IF EXISTS billing_batches_status_check;

ALTER TABLE billing_batches
    ADD CONSTRAINT billing_batches_status_check
    CHECK (status IN ('PENDIENTE', 'ENVIADO', 'BORRADOR', 'CERRADO', 'CANCELADO'));

CREATE INDEX IF NOT EXISTS idx_recibos_ars_service_billing
    ON recibos (ars, fecha, estado_facturacion)
    WHERE is_deleted = 0;

CREATE INDEX IF NOT EXISTS idx_recibos_admission_attention_lookup
    ON recibos (admission_atencion_id)
    WHERE admission_atencion_id IS NOT NULL AND is_deleted = 0;

CREATE INDEX IF NOT EXISTS idx_billing_batches_status_ars_period
    ON billing_batches (status, ars, period_year, period_month);

CREATE INDEX IF NOT EXISTS idx_billing_batch_receipts_receipt_active
    ON billing_batch_receipts (recibo_id, batch_id)
    WHERE included = 1;

COMMIT;
