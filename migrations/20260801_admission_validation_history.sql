-- Validación e historial central de Admisión para Facturación.
-- Idempotente; no elimina ni modifica datos clínicos de origen.
ALTER TABLE admission_attention_projection
    ADD COLUMN IF NOT EXISTS has_detail_sheet BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS verification_bypassed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS verification_bypass_reason TEXT;
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS verification_bypass_by TEXT;
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS verification_bypass_role TEXT;
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS verification_bypass_device TEXT;
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS verification_bypass_at TIMESTAMPTZ;
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS receipt_origin TEXT NOT NULL DEFAULT 'ADMISSION_LINKED';

CREATE TABLE IF NOT EXISTS admission_quick_list_dismissals(
    source_instance_id TEXT NOT NULL,
    attention_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    dismissed_by TEXT NOT NULL,
    dismissed_role TEXT NOT NULL,
    device_name TEXT,
    dismissed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY(source_instance_id,attention_id)
);

UPDATE recibos
SET receipt_origin='LEGACY_UNLINKED'
WHERE admission_atencion_id IS NULL
  AND receipt_origin='ADMISSION_LINKED'
  AND COALESCE(verification_bypassed,FALSE)=FALSE;

CREATE INDEX IF NOT EXISTS idx_admission_projection_history_date
    ON admission_attention_projection(service_date DESC, attention_id DESC);
CREATE INDEX IF NOT EXISTS idx_admission_projection_history_turn
    ON admission_attention_projection(source_instance_id, turn_id DESC, attention_id DESC);
CREATE INDEX IF NOT EXISTS idx_admission_projection_history_ars
    ON admission_attention_projection(canonical_ars, service_date DESC);
CREATE INDEX IF NOT EXISTS idx_admission_projection_history_sheet
    ON admission_attention_projection(has_detail_sheet, service_date DESC);
CREATE INDEX IF NOT EXISTS idx_admission_projection_history_nss_digits
    ON admission_attention_projection((REGEXP_REPLACE(COALESCE(nss_snapshot,''), '[^0-9]', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_admission_projection_history_cedula_digits
    ON admission_attention_projection((REGEXP_REPLACE(COALESCE(cedula_snapshot,''), '[^0-9]', '', 'g')));
CREATE INDEX IF NOT EXISTS idx_recibos_origin_bypass
    ON recibos(receipt_origin, verification_bypassed, estado_facturacion)
    WHERE is_deleted=0;
CREATE INDEX IF NOT EXISTS idx_admission_quick_dismissals_active
    ON admission_quick_list_dismissals(is_active,dismissed_at DESC);
