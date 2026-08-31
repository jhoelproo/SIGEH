-- SIGEH v1.1.0: separate receipt completeness from bypass authorization review.
-- Idempotent and non-destructive; no clinical or operational identity is changed.

ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS review_status TEXT NOT NULL DEFAULT 'NOT_APPLICABLE';
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS review_reason TEXT;

UPDATE recibos
   SET review_status = CASE
         WHEN COALESCE(verification_bypassed,FALSE)=FALSE THEN 'NOT_APPLICABLE'
         WHEN NULLIF(BTRIM(COALESCE(numero_autorizacion,'')),'') IS NULL
           THEN 'NOT_APPLICABLE'
         WHEN BTRIM(numero_autorizacion) ~ '^[0-9]{6,}$' THEN 'CLEAR'
         ELSE 'PENDING_REVIEW'
       END,
       review_reason = CASE
         WHEN COALESCE(verification_bypassed,FALSE)=FALSE THEN NULL
         WHEN NULLIF(BTRIM(COALESCE(numero_autorizacion,'')),'') IS NULL
           THEN 'AUTHORIZATION_MISSING'
         WHEN BTRIM(numero_autorizacion) !~ '^[0-9]+$'
           THEN 'INVALID_AUTHORIZATION_FORMAT'
         WHEN LENGTH(BTRIM(numero_autorizacion)) < 6
           THEN 'AUTHORIZATION_TOO_SHORT'
         ELSE NULL
       END
 WHERE COALESCE(review_status,'NOT_APPLICABLE')='NOT_APPLICABLE'
   AND COALESCE(verification_bypassed,FALSE)=TRUE;

-- LISTO_AUDITORIA is the complete-document state in the receipt workflow:
-- the official PDF/export can be emitted and the receipt can enter audit.
-- FINAL remains reserved for the later audit validation. A bypass receipt
-- with an authorization is therefore complete even when the value requires
-- review. Keep empty bypass receipts preliminary and never downgrade FINAL.
UPDATE recibos
   SET estado_documento='LISTO_AUDITORIA'
 WHERE COALESCE(verification_bypassed,FALSE)=TRUE
   AND NULLIF(BTRIM(COALESCE(numero_autorizacion,'')),'') IS NOT NULL
   AND estado_documento='PRELIMINAR';

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conname='ck_recibos_review_status'
       AND conrelid='recibos'::regclass
  ) THEN
    ALTER TABLE recibos
      ADD CONSTRAINT ck_recibos_review_status
      CHECK(review_status IN ('NOT_APPLICABLE','CLEAR','PENDING_REVIEW'));
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_recibos_bypass_pending_review
  ON recibos(id DESC)
  WHERE verification_bypassed=TRUE
    AND review_status='PENDING_REVIEW'
    AND is_deleted=0;
