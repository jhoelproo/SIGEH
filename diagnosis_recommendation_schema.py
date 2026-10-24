"""Additive private schema; application connections own its data, never REST."""

SCHEMA_LOCK_KEY = "sigeh-diagnosis-recommendation-schema-v1"

READY_SQL = """
SELECT to_regclass('sigeh_recommendations.settings') IS NOT NULL
 AND to_regclass('sigeh_recommendations.jobs') IS NOT NULL
 AND to_regclass('sigeh_recommendations.observations') IS NOT NULL
 AND to_regclass('sigeh_recommendations.evidence') IS NOT NULL
 AND to_regclass('sigeh_recommendations.changes') IS NOT NULL
 AND EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid=to_regclass('sigeh_recommendations.settings')
            AND attname='generation' AND NOT attisdropped)
 AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.recibos')
            AND tgname='recommendation_receipt_change')
 AND EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=to_regclass('public.recibo_items')
            AND tgname='recommendation_item_change')
"""


def schema_ready(con):
    row = con.execute(READY_SQL).fetchone()
    return bool(row and row[0])


def ensure_schema(con):
    if schema_ready(con):
        return False
    con.execute(
        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (SCHEMA_LOCK_KEY,)
    )
    if schema_ready(con):
        return False
    con.execute(SCHEMA_SQL)
    return True


SCHEMA_SQL = r"""
CREATE SCHEMA IF NOT EXISTS sigeh_recommendations;
REVOKE ALL ON SCHEMA sigeh_recommendations FROM PUBLIC;
CREATE TABLE IF NOT EXISTS sigeh_recommendations.settings(
 singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
 config JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(config)='object'),
 version BIGINT NOT NULL DEFAULT 0,seeded BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS sigeh_recommendations.jobs(
 receipt_id BIGINT PRIMARY KEY,touched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE sigeh_recommendations.settings ADD COLUMN IF NOT EXISTS generation UUID NOT NULL DEFAULT gen_random_uuid();
CREATE INDEX IF NOT EXISTS recommendation_jobs_recent_idx ON sigeh_recommendations.jobs(touched_at DESC,receipt_id DESC);
CREATE TABLE IF NOT EXISTS sigeh_recommendations.observations(
 receipt_id BIGINT PRIMARY KEY,diagnoses JSONB NOT NULL,
 specialty TEXT NOT NULL,status TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS recommendation_diagnoses_idx
 ON sigeh_recommendations.observations USING GIN(diagnoses);
CREATE INDEX IF NOT EXISTS recommendation_specialty_idx
 ON sigeh_recommendations.observations(specialty);
CREATE TABLE IF NOT EXISTS sigeh_recommendations.evidence(
 receipt_id BIGINT NOT NULL REFERENCES sigeh_recommendations.observations(receipt_id) ON DELETE CASCADE,
 diagnosis TEXT NOT NULL,specialty TEXT NOT NULL,category TEXT NOT NULL,
 item TEXT NOT NULL,confirmed BOOLEAN NOT NULL,
 PRIMARY KEY(receipt_id,diagnosis,category,item)
);
CREATE INDEX IF NOT EXISTS recommendation_group_idx
 ON sigeh_recommendations.evidence(diagnosis,specialty);
CREATE TABLE IF NOT EXISTS sigeh_recommendations.changes(
 change_key TEXT PRIMARY KEY,revision BIGSERIAL UNIQUE,payload JSONB
);
ALTER TABLE sigeh_recommendations.settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE sigeh_recommendations.jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sigeh_recommendations.observations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sigeh_recommendations.evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE sigeh_recommendations.changes ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON ALL TABLES IN SCHEMA sigeh_recommendations FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA sigeh_recommendations FROM PUBLIC;
INSERT INTO sigeh_recommendations.settings(singleton) VALUES(TRUE) ON CONFLICT DO NOTHING;
INSERT INTO sigeh_recommendations.changes(change_key,payload)
 SELECT 'config',config || jsonb_build_object('version',version)
 FROM sigeh_recommendations.settings ON CONFLICT DO NOTHING;
CREATE OR REPLACE FUNCTION sigeh_recommendations.enqueue_receipt()
 RETURNS TRIGGER LANGUAGE plpgsql SECURITY INVOKER AS $$
DECLARE value JSONB; receipt BIGINT;
BEGIN
 value := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 receipt := (value->>TG_ARGV[0])::bigint;
 INSERT INTO sigeh_recommendations.jobs(receipt_id) VALUES(receipt)
 ON CONFLICT(receipt_id) DO UPDATE SET touched_at=NOW();
 IF TG_OP='UPDATE' AND TG_ARGV[0]='recibo_id' THEN
  INSERT INTO sigeh_recommendations.jobs(receipt_id) VALUES((to_jsonb(OLD)->>'recibo_id')::bigint)
  ON CONFLICT(receipt_id) DO UPDATE SET touched_at=NOW();
 END IF;
 RETURN NULL;
END; $$;
REVOKE ALL ON FUNCTION sigeh_recommendations.enqueue_receipt() FROM PUBLIC;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='recibos'::regclass AND tgname='recommendation_receipt_change') THEN
  CREATE TRIGGER recommendation_receipt_change
  AFTER INSERT OR DELETE OR UPDATE OF dx,specialty_snapshot,estado_facturacion,is_deleted ON recibos
  FOR EACH ROW EXECUTE FUNCTION sigeh_recommendations.enqueue_receipt('id');
 END IF;
 IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='recibo_items'::regclass AND tgname='recommendation_item_change') THEN
  CREATE TRIGGER recommendation_item_change
  AFTER INSERT OR DELETE OR UPDATE OF categoria,nombre,recibo_id ON recibo_items
  FOR EACH ROW EXECUTE FUNCTION sigeh_recommendations.enqueue_receipt('recibo_id');
 END IF;
 IF NOT (SELECT seeded FROM sigeh_recommendations.settings) THEN
  INSERT INTO sigeh_recommendations.jobs(receipt_id) SELECT id FROM recibos ON CONFLICT DO NOTHING;
  UPDATE sigeh_recommendations.settings SET seeded=TRUE;
 END IF;
END; $$;
"""
