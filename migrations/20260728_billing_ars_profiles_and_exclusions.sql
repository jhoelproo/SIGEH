BEGIN;

ALTER TABLE ars
    ADD COLUMN IF NOT EXISTS billing_enabled BOOLEAN NOT NULL DEFAULT TRUE;

UPDATE ars
SET billing_enabled = FALSE
WHERE UPPER(REGEXP_REPLACE(COALESCE(nombre, ''), '[^A-Za-z0-9]+', '', 'g'))
      ~ '^SENASASUB';

ALTER TABLE billing_ars_profiles
    ADD COLUMN IF NOT EXISTS ars_phone TEXT,
    ADD COLUMN IF NOT EXISTS ars_email TEXT,
    ADD COLUMN IF NOT EXISTS administrative_notes TEXT;

ALTER TABLE billing_batches
    ADD COLUMN IF NOT EXISTS ars_phone TEXT,
    ADD COLUMN IF NOT EXISTS ars_email TEXT,
    ADD COLUMN IF NOT EXISTS administrative_notes TEXT;

CREATE INDEX IF NOT EXISTS idx_ars_billing_enabled_active
    ON ars (billing_enabled, is_active);

COMMIT;
