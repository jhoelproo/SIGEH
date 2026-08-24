ALTER TABLE recibos ADD COLUMN IF NOT EXISTS document_storage_mode TEXT;

UPDATE recibos
SET document_storage_mode='LEGACY_PDF'
WHERE document_storage_mode IS NULL
   OR BTRIM(document_storage_mode)='';

ALTER TABLE recibos
  ALTER COLUMN document_storage_mode SET DEFAULT 'SNAPSHOT';
ALTER TABLE recibos
  ALTER COLUMN document_storage_mode SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname='recibos_document_storage_mode_check'
      AND conrelid='recibos'::regclass
  ) THEN
    ALTER TABLE recibos
      ADD CONSTRAINT recibos_document_storage_mode_check
      CHECK(document_storage_mode IN ('LEGACY_PDF','SNAPSHOT','HYBRID'));
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS recibo_document_versions(
  id BIGSERIAL PRIMARY KEY,
  recibo_id INTEGER NOT NULL,
  version INTEGER NOT NULL CHECK(version > 0),
  estado_documento TEXT NOT NULL,
  estado_facturacion TEXT NOT NULL,
  snapshot_jsonb JSONB NOT NULL,
  snapshot_hash TEXT NOT NULL CHECK(snapshot_hash ~ '^[0-9a-f]{64}$'),
  template_version TEXT NOT NULL,
  schema_version INTEGER NOT NULL CHECK(schema_version > 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by TEXT NOT NULL,
  is_current BOOLEAN NOT NULL DEFAULT TRUE,
  CONSTRAINT recibo_document_versions_receipt_fk
    FOREIGN KEY(recibo_id) REFERENCES recibos(id) ON DELETE RESTRICT,
  CONSTRAINT recibo_document_versions_receipt_version_uq
    UNIQUE(recibo_id,version)
);

CREATE INDEX IF NOT EXISTS idx_recibo_document_versions_receipt
  ON recibo_document_versions(recibo_id);
CREATE INDEX IF NOT EXISTS idx_recibo_document_versions_receipt_version
  ON recibo_document_versions(recibo_id,version DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_recibo_document_versions_current
  ON recibo_document_versions(recibo_id)
  WHERE is_current=TRUE;
CREATE INDEX IF NOT EXISTS idx_recibo_document_versions_receipt_current
  ON recibo_document_versions(recibo_id,is_current);

ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_deleted_at TIMESTAMPTZ;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_checksum TEXT;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_size BIGINT;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_backup_reference TEXT;
ALTER TABLE recibos
  ADD COLUMN IF NOT EXISTS legacy_pdf_deletion_batch TEXT;

ALTER TABLE pdf_storage ADD COLUMN IF NOT EXISTS document_type TEXT;
ALTER TABLE pdf_storage ADD COLUMN IF NOT EXISTS owner_receipt_id INTEGER;
UPDATE pdf_storage p
SET document_type='RECEIPT_LEGACY',owner_receipt_id=r.id
FROM recibos r
WHERE r.pdf_filename=p.filename
  AND COALESCE(p.document_type,'')='';
UPDATE pdf_storage
SET document_type='UNKNOWN'
WHERE COALESCE(document_type,'')='';
ALTER TABLE pdf_storage ALTER COLUMN document_type SET DEFAULT 'UNKNOWN';
ALTER TABLE pdf_storage ALTER COLUMN document_type SET NOT NULL;
CREATE INDEX IF NOT EXISTS idx_pdf_storage_document_type
  ON pdf_storage(document_type);
CREATE INDEX IF NOT EXISTS idx_pdf_storage_owner_receipt
  ON pdf_storage(owner_receipt_id)
  WHERE owner_receipt_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS recibo_document_migration(
  recibo_id INTEGER PRIMARY KEY,
  migration_status TEXT NOT NULL DEFAULT 'PENDING',
  classification TEXT,
  source_pdf_filename TEXT,
  source_pdf_size BIGINT,
  source_pdf_hash TEXT,
  backup_location TEXT,
  backup_hash TEXT,
  backup_verified BOOLEAN NOT NULL DEFAULT FALSE,
  snapshot_version INTEGER,
  snapshot_hash TEXT,
  render_verified BOOLEAN NOT NULL DEFAULT FALSE,
  validation_jsonb JSONB,
  deletion_batch_id TEXT,
  pdf_deleted_at TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  verified_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CONSTRAINT recibo_document_migration_receipt_fk
    FOREIGN KEY(recibo_id) REFERENCES recibos(id) ON DELETE RESTRICT,
  CONSTRAINT recibo_document_migration_status_check CHECK(
    migration_status IN(
      'PENDING','ANALYZING','RECONSTRUCTIBLE','MIGRATED_HYBRID',
      'RENDER_VERIFIED','BACKED_UP','DELETION_CANDIDATE',
      'PDF_DELETED','VERIFIED','NEEDS_REVIEW','FAILED','RESTORED'
    )
  ),
  CONSTRAINT recibo_document_migration_classification_check CHECK(
    classification IS NULL OR classification IN(
      'COMPLETE','WITHOUT_ITEMS','TOTAL_MISMATCH','MISSING_HEADER_DATA',
      'MISSING_PDF','INVALID_STATE','SNAPSHOT_EXISTS','ALREADY_MIGRATED'
    )
  )
);
CREATE INDEX IF NOT EXISTS idx_recibo_document_migration_status
  ON recibo_document_migration(migration_status,recibo_id);
CREATE INDEX IF NOT EXISTS idx_recibo_document_migration_classification
  ON recibo_document_migration(classification,recibo_id);
ALTER TABLE recibo_document_migration ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION enforce_receipt_document_version_immutable()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'Las versiones documentales de recibos son inmutables';
  END IF;
  IF NEW.recibo_id IS DISTINCT FROM OLD.recibo_id
     OR NEW.version IS DISTINCT FROM OLD.version
     OR NEW.estado_documento IS DISTINCT FROM OLD.estado_documento
     OR NEW.estado_facturacion IS DISTINCT FROM OLD.estado_facturacion
     OR NEW.snapshot_jsonb IS DISTINCT FROM OLD.snapshot_jsonb
     OR NEW.snapshot_hash IS DISTINCT FROM OLD.snapshot_hash
     OR NEW.template_version IS DISTINCT FROM OLD.template_version
     OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.created_by IS DISTINCT FROM OLD.created_by THEN
    RAISE EXCEPTION 'El contenido de una versión documental no puede modificarse';
  END IF;
  RETURN NEW;
END;
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_trigger
    WHERE tgname='trg_receipt_document_version_immutable'
      AND tgrelid='recibo_document_versions'::regclass
      AND NOT tgisinternal
  ) THEN
    CREATE TRIGGER trg_receipt_document_version_immutable
    BEFORE UPDATE OR DELETE ON recibo_document_versions
    FOR EACH ROW EXECUTE FUNCTION enforce_receipt_document_version_immutable();
  END IF;
END $$;

ALTER TABLE recibo_document_versions ENABLE ROW LEVEL SECURITY;
