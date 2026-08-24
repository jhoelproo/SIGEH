-- Integración local y trazable entre Admisión y Facturación.
-- La base SQLite de Admisión permanece completamente en modo solo lectura.

ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_atencion_id BIGINT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_paciente_id BIGINT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_nss_snapshot TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_cedula_snapshot TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_ars_snapshot TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_linked_at TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_linked_by TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_source_updated_at TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_source_instance_id TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_snapshot_hash TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_coverage_status TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS admission_readiness TEXT;

DROP INDEX IF EXISTS uq_recibos_admission_attention_active;
CREATE UNIQUE INDEX IF NOT EXISTS uq_recibos_admission_source_attention_active
ON recibos(COALESCE(admission_source_instance_id,'LEGACY'), admission_atencion_id)
WHERE admission_atencion_id IS NOT NULL AND is_deleted=0;

CREATE TABLE IF NOT EXISTS admission_attention_projection(
  source_instance_id TEXT NOT NULL,
  attention_id BIGINT NOT NULL,
  patient_id BIGINT NOT NULL,
  service_date TEXT NOT NULL,
  patient_name TEXT NOT NULL,
  coverage_status TEXT NOT NULL,
  canonical_ars TEXT,
  nss_snapshot TEXT,
  cedula_snapshot TEXT,
  readiness TEXT NOT NULL,
  readiness_reasons TEXT,
  source_updated_at TEXT,
  snapshot_hash TEXT NOT NULL,
  contract_version INTEGER NOT NULL,
  synced_at TEXT NOT NULL,
  PRIMARY KEY(source_instance_id, attention_id)
);

CREATE INDEX IF NOT EXISTS idx_admission_projection_queue
ON admission_attention_projection(readiness, service_date);

CREATE TABLE IF NOT EXISTS admission_ars_crosswalk(
  source_value_key TEXT PRIMARY KEY,
  source_value TEXT NOT NULL,
  canonical_ars TEXT NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS billing_batches(
  id SERIAL PRIMARY KEY,
  period_year INTEGER NOT NULL,
  period_month INTEGER NOT NULL CHECK(period_month BETWEEN 1 AND 12),
  ars TEXT NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  status TEXT NOT NULL DEFAULT 'BORRADOR'
    CHECK(status IN ('BORRADOR','CERRADO','CANCELADO')),
  cutoff_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL,
  updated_at TEXT,
  updated_by TEXT,
  notes TEXT,
  ars_display_name TEXT,
  invoice_number TEXT,
  ncf TEXT,
  invoice_date TEXT,
  ncf_expiration_date TEXT,
  provider_code TEXT,
  provider_name TEXT,
  provider_rnc TEXT,
  ars_rnc TEXT,
  ars_address TEXT,
  director_name TEXT,
  director_title TEXT,
  service_description TEXT NOT NULL DEFAULT 'EMERGENCIA',
  specialty_default TEXT NOT NULL DEFAULT 'EMERGENCIOLOGÍA',
  discount REAL NOT NULL DEFAULT 0,
  itbis REAL NOT NULL DEFAULT 0,
  last_export_path TEXT,
  last_exported_at TEXT,
  UNIQUE(period_year, period_month, ars, version)
);

CREATE TABLE IF NOT EXISTS billing_batch_receipts(
  batch_id INTEGER NOT NULL REFERENCES billing_batches(id) ON DELETE CASCADE,
  recibo_id INTEGER NOT NULL REFERENCES recibos(id) ON DELETE RESTRICT,
  included INTEGER NOT NULL DEFAULT 1 CHECK(included IN (0,1)),
  patient_snapshot TEXT,
  nss_snapshot TEXT,
  cedula_snapshot TEXT,
  document_type_snapshot TEXT,
  document_number_snapshot TEXT,
  authorization_snapshot TEXT,
  service_date_snapshot TEXT,
  specialty_snapshot TEXT,
  ars_snapshot TEXT,
  comprobante_snapshot TEXT,
  billing_date_snapshot TEXT NOT NULL,
  total_snapshot REAL NOT NULL,
  added_at TEXT NOT NULL,
  added_by TEXT NOT NULL,
  removed_at TEXT,
  removed_by TEXT,
  removal_reason TEXT,
  last_edited_at TEXT,
  last_edited_by TEXT,
  PRIMARY KEY(batch_id, recibo_id)
);

ALTER TABLE billing_batch_receipts
ADD COLUMN IF NOT EXISTS comprobante_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS cedula_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS document_type_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS document_number_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS authorization_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS service_date_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS specialty_snapshot TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS last_edited_at TEXT;
ALTER TABLE billing_batch_receipts ADD COLUMN IF NOT EXISTS last_edited_by TEXT;

UPDATE billing_batch_receipts AS br
SET cedula_snapshot=COALESCE(
        NULLIF(TRIM(COALESCE(br.cedula_snapshot,'')),''),
        NULLIF(TRIM(COALESCE(r.admission_cedula_snapshot,'')),'')
    ),
    document_type_snapshot=COALESCE(
        NULLIF(TRIM(COALESCE(br.document_type_snapshot,'')),''),
        CASE
            WHEN NULLIF(TRIM(COALESCE(br.nss_snapshot,'')),'') IS NOT NULL
                 OR NULLIF(TRIM(COALESCE(r.admission_nss_snapshot,'')),'') IS NOT NULL
            THEN 'NSS'
            ELSE 'CÉDULA'
        END
    ),
    document_number_snapshot=COALESCE(
        NULLIF(TRIM(COALESCE(br.document_number_snapshot,'')),''),
        NULLIF(TRIM(COALESCE(br.nss_snapshot,'')),''),
        NULLIF(TRIM(COALESCE(r.admission_nss_snapshot,'')),''),
        NULLIF(TRIM(COALESCE(r.admission_cedula_snapshot,'')),'')
    ),
    authorization_snapshot=COALESCE(
        NULLIF(TRIM(COALESCE(br.authorization_snapshot,'')),''),
        NULLIF(TRIM(COALESCE(r.numero_autorizacion,'')),''),
        r.numero::TEXT
    ),
    service_date_snapshot=COALESCE(
        NULLIF(TRIM(COALESCE(br.service_date_snapshot,'')),''),
        NULLIF(TRIM(COALESCE(r.fecha,'')),'')
    ),
    specialty_snapshot=COALESCE(
        NULLIF(TRIM(COALESCE(br.specialty_snapshot,'')),''),
        'EMERGENCIOLOGÍA'
    )
FROM recibos AS r
WHERE r.id=br.recibo_id
  AND br.last_edited_at IS NULL;

ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS ars_display_name TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS invoice_number TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS ncf TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS invoice_date TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS ncf_expiration_date TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS provider_code TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS provider_name TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS provider_rnc TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS ars_rnc TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS ars_address TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS director_name TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS director_title TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS service_description TEXT NOT NULL DEFAULT 'EMERGENCIA';
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS specialty_default TEXT NOT NULL DEFAULT 'EMERGENCIOLOGÍA';
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS discount REAL NOT NULL DEFAULT 0;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS itbis REAL NOT NULL DEFAULT 0;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS last_export_path TEXT;
ALTER TABLE billing_batches ADD COLUMN IF NOT EXISTS last_exported_at TEXT;

CREATE INDEX IF NOT EXISTS idx_billing_batch_receipts_active
ON billing_batch_receipts(batch_id, included);

CREATE TABLE IF NOT EXISTS billing_batch_events(
  id SERIAL PRIMARY KEY,
  batch_id INTEGER NOT NULL REFERENCES billing_batches(id) ON DELETE CASCADE,
  recibo_id INTEGER REFERENCES recibos(id) ON DELETE SET NULL,
  event_type TEXT NOT NULL,
  performed_at TEXT NOT NULL,
  performed_by TEXT NOT NULL,
  details TEXT
);

CREATE TABLE IF NOT EXISTS billing_ars_profiles(
  ars TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  ars_rnc TEXT,
  ars_address TEXT,
  provider_code TEXT,
  provider_name TEXT,
  provider_rnc TEXT,
  director_name TEXT,
  director_title TEXT,
  service_description TEXT NOT NULL DEFAULT 'EMERGENCIA',
  specialty_default TEXT NOT NULL DEFAULT 'EMERGENCIOLOGÍA',
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL
);
