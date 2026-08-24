BEGIN;

ALTER TABLE ars
    ADD COLUMN IF NOT EXISTS honorarium_prompt_updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS honorarium_prompt_updated_by TEXT;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM information_schema.columns
         WHERE table_schema = CURRENT_SCHEMA()
           AND table_name = 'ars'
           AND column_name = 'suppress_honorarium_prompt'
    ) THEN
        ALTER TABLE ars
            ADD COLUMN suppress_honorarium_prompt BOOLEAN NOT NULL DEFAULT FALSE;

        -- Preserve only explicit choices from the former opt-in setting. Rows
        -- never edited keep the new safe default: show the prompt.
        IF EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_schema = CURRENT_SCHEMA()
               AND table_name = 'ars'
               AND column_name = 'honorarium_prompt_enabled'
        ) THEN
            UPDATE ars
               SET suppress_honorarium_prompt = CASE
                       WHEN honorarium_prompt_updated_at IS NOT NULL
                       THEN NOT honorarium_prompt_enabled
                       ELSE FALSE
                   END;
        END IF;
    END IF;
END $$;

ALTER TABLE ars
    ADD COLUMN IF NOT EXISTS honorarium_prompt_enabled BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE ars
    ALTER COLUMN honorarium_prompt_enabled SET DEFAULT TRUE;

-- Compatibility projection for previous packaged clients. The suppression
-- column is authoritative; the former enabled flag is its exact inverse.
UPDATE ars
   SET honorarium_prompt_enabled = NOT suppress_honorarium_prompt;

COMMIT;
