-- Identidad global del puente Admisión central -> Facturación.
-- Idempotente: no elimina registros ni altera datos clínicos de Admisión.
ALTER TABLE recibos
    ADD COLUMN IF NOT EXISTS admission_global_attention_id UUID;

CREATE INDEX IF NOT EXISTS idx_recibos_admission_global_attention
    ON recibos(admission_global_attention_id)
    WHERE admission_global_attention_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_admission_projection_billing_scope
    ON admission_attention_projection(
        operational_source_id,
        turn_id,
        readiness,
        created_at_effective_utc DESC
    )
    WHERE COALESCE(is_deleted,FALSE)=FALSE
      AND UPPER(TRIM(COALESCE(source_status,'ACTIVA')))
          IN ('ACTIVA','PENDIENTE');

CREATE INDEX IF NOT EXISTS idx_admission_projection_billing_history
    ON admission_attention_projection(created_at_effective_utc DESC)
    WHERE COALESCE(is_deleted,FALSE)=FALSE
      AND UPPER(TRIM(COALESCE(source_status,'ACTIVA')))
          IN ('ACTIVA','PENDIENTE');

UPDATE recibos receipt
SET admission_global_attention_id=projection.global_attention_id
FROM admission_attention_projection projection
WHERE receipt.admission_global_attention_id IS NULL
  AND projection.global_attention_id IS NOT NULL
  AND receipt.admission_atencion_id=projection.attention_id
  AND COALESCE(receipt.admission_source_instance_id,'LEGACY')=
      COALESCE(NULLIF(projection.source_instance_id,''),'LEGACY');
