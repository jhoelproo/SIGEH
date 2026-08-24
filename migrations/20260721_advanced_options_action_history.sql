BEGIN;

SELECT pg_advisory_xact_lock(hashtext('action-history-v2'));

ALTER TABLE action_history ADD COLUMN IF NOT EXISTS role_snapshot TEXT;
ALTER TABLE action_history ADD COLUMN IF NOT EXISTS module TEXT;
ALTER TABLE action_history ADD COLUMN IF NOT EXISTS entity_type TEXT;
ALTER TABLE action_history ADD COLUMN IF NOT EXISTS entity_id TEXT;

CREATE OR REPLACE FUNCTION enrich_action_history()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    action_text TEXT := lower(COALESCE(NEW.action, ''));
BEGIN
    IF NULLIF(btrim(COALESCE(NEW.role_snapshot, '')), '') IS NULL THEN
        SELECT role INTO NEW.role_snapshot
        FROM public.users
        WHERE username=NEW.username
        LIMIT 1;
        NEW.role_snapshot := COALESCE(NEW.role_snapshot, 'Sistema');
    END IF;
    IF NULLIF(btrim(COALESCE(NEW.module, '')), '') IS NULL THEN
        NEW.module := CASE
            WHEN action_text LIKE '%listado%' OR action_text LIKE '%ars%' THEN 'Listados ARS'
            WHEN action_text LIKE '%recibo%' OR action_text LIKE '%factur%' THEN 'Facturación'
            WHEN action_text LIKE '%usuario%' OR action_text LIKE '%sesión%' OR action_text LIKE '%login%' THEN 'Seguridad'
            WHEN action_text LIKE '%admisión%' OR action_text LIKE '%paciente%' THEN 'Emergencias'
            WHEN action_text LIKE '%reporte%' THEN 'Reportes'
            ELSE 'General'
        END;
    END IF;
    IF NULLIF(btrim(COALESCE(NEW.entity_type, '')), '') IS NULL THEN
        NEW.entity_type := CASE
            WHEN action_text LIKE '%listado%' THEN 'listado_ars'
            WHEN action_text LIKE '%recibo%' OR action_text LIKE '%factur%' THEN 'recibo'
            WHEN action_text LIKE '%usuario%' THEN 'usuario'
            WHEN action_text LIKE '%sesión%' OR action_text LIKE '%login%' THEN 'sesion'
            WHEN action_text LIKE '%reporte%' THEN 'reporte'
            ELSE 'sistema'
        END;
    END IF;
    IF NULLIF(btrim(COALESCE(NEW.entity_id, '')), '') IS NULL THEN
        NEW.entity_id := CASE
            WHEN NEW.entity_type='recibo' THEN substring(
                COALESCE(NEW.details,'') FROM '(?i)recibo(?:\s+n[.º°]*)?(?:\s+id)?\s+([0-9]+)'
            )
            WHEN NEW.entity_type='listado_ars' THEN substring(
                COALESCE(NEW.details,'') FROM '(?i)listado\s+([0-9]+)'
            )
            ELSE NEW.entity_id
        END;
    END IF;
    RETURN NEW;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname='trg_action_history_enrich'
          AND tgrelid='public.action_history'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_action_history_enrich
        BEFORE INSERT ON action_history
        FOR EACH ROW EXECUTE FUNCTION enrich_action_history();
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS idx_action_history_created_at
    ON action_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_history_username_created
    ON action_history(username, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_history_module_created
    ON action_history(module, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_action_history_action_created
    ON action_history(action, created_at DESC);

COMMIT;
