SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '30s';

DO $maintenance$
DECLARE
    pair RECORD;
    duplicate_oid OID;
BEGIN
    FOR pair IN
        SELECT * FROM (VALUES
            ('idx_admission_sync_events_cursor', 'admission_sync_events_pkey'),
            ('idx_admission_sync_events_event_uuid', 'admission_sync_events_event_uuid_key'),
            ('idx_admission_patient_directory_events_cursor', 'admission_patient_directory_events_pkey')
        ) AS indexes(duplicate_name, keeper_name)
    LOOP
        duplicate_oid := to_regclass(format('public.%I', pair.duplicate_name));
        IF duplicate_oid IS NULL THEN
            CONTINUE;
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM pg_index redundant
            JOIN pg_index keeper
              ON keeper.indexrelid=to_regclass(format('public.%I', pair.keeper_name))
            JOIN pg_class redundant_class ON redundant_class.oid=redundant.indexrelid
            JOIN pg_class keeper_class ON keeper_class.oid=keeper.indexrelid
            WHERE redundant.indexrelid=duplicate_oid
              AND redundant.indrelid=keeper.indrelid
              AND redundant.indkey=keeper.indkey
              AND redundant.indclass=keeper.indclass
              AND redundant.indcollation=keeper.indcollation
              AND redundant.indoption=keeper.indoption
              AND redundant.indnatts=keeper.indnatts
              AND redundant.indnkeyatts=keeper.indnkeyatts
              AND redundant_class.relam=keeper_class.relam
              AND redundant.indpred::TEXT IS NOT DISTINCT FROM keeper.indpred::TEXT
              AND redundant.indexprs::TEXT IS NOT DISTINCT FROM keeper.indexprs::TEXT
              AND redundant.indisvalid AND keeper.indisvalid
              AND redundant.indisready AND keeper.indisready
              AND keeper.indisunique
              AND NOT redundant.indisunique
              AND NOT redundant.indisprimary
              AND NOT redundant.indisexclusion
              AND NOT EXISTS (
                  SELECT 1 FROM pg_constraint WHERE conindid=duplicate_oid
              )
        ) THEN
            RAISE EXCEPTION 'Index % is not a redundant, unconstrained index', pair.duplicate_name;
        END IF;
        EXECUTE format('DROP INDEX public.%I', pair.duplicate_name);
    END LOOP;
END
$maintenance$;

ALTER TABLE public.admission_patient_directory SET (
    autovacuum_vacuum_scale_factor=0.05,
    autovacuum_analyze_scale_factor=0.02
);
ALTER TABLE public.recibos SET (
    autovacuum_vacuum_scale_factor=0.05,
    autovacuum_analyze_scale_factor=0.02
);
ALTER TABLE public.recibo_document_versions SET (
    autovacuum_vacuum_scale_factor=0.05,
    autovacuum_analyze_scale_factor=0.02
);
