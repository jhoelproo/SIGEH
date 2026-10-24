BEGIN;

ALTER TABLE ars ADD COLUMN IF NOT EXISTS honorarium_specialties
    JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(honorarium_specialties)='object');
ALTER TABLE universal_items ADD COLUMN IF NOT EXISTS materials_configured
    BOOLEAN NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS medication_material_links(
    medication_id INTEGER NOT NULL REFERENCES universal_items(id) ON DELETE CASCADE,
    material_id INTEGER NOT NULL REFERENCES universal_items(id) ON DELETE RESTRICT,
    units INTEGER NOT NULL CHECK(units BETWEEN 1 AND 1000),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT NOT NULL,
    PRIMARY KEY(medication_id,material_id),
    CHECK(medication_id<>material_id)
);
CREATE INDEX IF NOT EXISTS medication_material_links_material_idx
    ON medication_material_links(material_id);
ALTER TABLE medication_material_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE medication_material_links FROM PUBLIC;
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN
        REVOKE ALL ON TABLE medication_material_links FROM anon;
    END IF;
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN
        REVOKE ALL ON TABLE medication_material_links FROM authenticated;
    END IF;
END;
$$;

COMMIT;
