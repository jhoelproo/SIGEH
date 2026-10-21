SET LOCAL lock_timeout = '3s';
SET LOCAL statement_timeout = '30s';
ALTER TABLE public.users ADD COLUMN IF NOT EXISTS
    permission_overrides JSONB NOT NULL DEFAULT '{}'::jsonb;
