BEGIN;

SELECT pg_advisory_xact_lock(hashtext('receipt-dedup-v1'));

ALTER TABLE recibos ADD COLUMN IF NOT EXISTS dedup_key TEXT;

CREATE OR REPLACE FUNCTION set_recibo_dedup_key()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $$
DECLARE
    normalized_value TEXT;
    normalized_source TEXT;
BEGIN
    normalized_source := regexp_replace(
        upper(translate(btrim(COALESCE(NEW.admission_source_instance_id, '')),
            'áéíóúüñ', 'ÁÉÍÓÚÜÑ')),
        '\s+', ' ', 'g'
    );
    IF NEW.admission_atencion_id IS NOT NULL THEN
        NEW.dedup_key := 'A:'
            || CASE WHEN normalized_source <> '' THEN normalized_source || ':' ELSE '' END
            || NEW.admission_atencion_id::text;
    ELSIF btrim(COALESCE(NEW.admission_nss_snapshot, '')) <> '' THEN
        normalized_value := regexp_replace(
            upper(translate(btrim(NEW.admission_nss_snapshot),
                'áéíóúüñ', 'ÁÉÍÓÚÜÑ')), '\s+', ' ', 'g'
        );
        NEW.dedup_key := 'N:' || normalized_value;
    ELSIF btrim(COALESCE(NEW.admission_cedula_snapshot, '')) <> '' THEN
        normalized_value := regexp_replace(
            upper(translate(btrim(NEW.admission_cedula_snapshot),
                'áéíóúüñ', 'ÁÉÍÓÚÜÑ')), '\s+', ' ', 'g'
        );
        NEW.dedup_key := 'C:' || normalized_value;
    ELSIF btrim(COALESCE(NEW.nombre, '')) <> '' THEN
        normalized_value := regexp_replace(
            upper(translate(btrim(NEW.nombre),
                'áéíóúüñ', 'ÁÉÍÓÚÜÑ')), '\s+', ' ', 'g'
        );
        NEW.dedup_key := 'M:' || normalized_value;
    ELSE
        NEW.dedup_key := NULL;
    END IF;
    RETURN NEW;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger
        WHERE tgname='trg_recibos_set_dedup_key'
          AND tgrelid='public.recibos'::regclass
          AND NOT tgisinternal
    ) THEN
        CREATE TRIGGER trg_recibos_set_dedup_key
        BEFORE INSERT OR UPDATE OF admission_atencion_id,
            admission_source_instance_id, admission_nss_snapshot,
            admission_cedula_snapshot, nombre
        ON recibos
        FOR EACH ROW
        EXECUTE FUNCTION set_recibo_dedup_key();
    END IF;
END
$$;

UPDATE recibos SET dedup_key = NULL WHERE dedup_key IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM recibos
        WHERE is_deleted=0
          AND NULLIF(btrim(COALESCE(fecha,'')),'') IS NOT NULL
          AND NULLIF(dedup_key,'') IS NOT NULL
        GROUP BY fecha, dedup_key
        HAVING COUNT(*) > 1
    ) THEN
        RAISE WARNING 'Recibos: existen duplicados históricos activos; no se creó el índice único y no se eliminó ningún registro.';
    ELSE
        CREATE UNIQUE INDEX IF NOT EXISTS uq_recibos_active_patient_service_date
            ON recibos(fecha, dedup_key)
            WHERE is_deleted=0
              AND NULLIF(btrim(COALESCE(fecha,'')),'') IS NOT NULL
              AND NULLIF(dedup_key,'') IS NOT NULL;
    END IF;
END
$$;

COMMIT;
