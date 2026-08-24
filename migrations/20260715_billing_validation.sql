-- Migración de revisión local. No se ejecuta automáticamente desde este archivo.
-- Los recibos existentes se conservan como SIN_CLASIFICAR; los nuevos usarán PENDIENTE.
BEGIN;

ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check;
ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (
    role IN ('auxiliar', 'administrador', 'facturador de auditoria', 'auditoria medica y cuentas')
);

ALTER TABLE recibos ADD COLUMN IF NOT EXISTS deleted_by TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS deleted_reason TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS estado_facturacion TEXT DEFAULT 'SIN_CLASIFICAR';
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS estado_facturacion_at TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS estado_facturacion_por TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS motivo_no_facturado TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS observacion_facturacion TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS referencia_facturacion TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS numero_autorizacion TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS autorizacion_at TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS autorizacion_por TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS estado_documento TEXT NOT NULL DEFAULT 'PRELIMINAR';
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS motivo_no_facturado_codigo TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS auditoria_asignada_a TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS auditoria_asignada_at TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS auditoria_checklist_json TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS auditoria_preflight_json TEXT;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS auditoria_riesgo INTEGER NOT NULL DEFAULT 0;
ALTER TABLE recibos ADD COLUMN IF NOT EXISTS revision_version INTEGER NOT NULL DEFAULT 0;

UPDATE recibos
SET estado_facturacion = 'SIN_CLASIFICAR'
WHERE estado_facturacion IS NULL OR TRIM(estado_facturacion) = '';

ALTER TABLE recibos ALTER COLUMN estado_facturacion SET DEFAULT 'PENDIENTE';
ALTER TABLE recibos ALTER COLUMN estado_facturacion SET NOT NULL;
ALTER TABLE recibos DROP CONSTRAINT IF EXISTS recibos_estado_facturacion_check;
ALTER TABLE recibos ADD CONSTRAINT recibos_estado_facturacion_check CHECK (
    estado_facturacion IN ('PENDIENTE', 'FACTURADO', 'NO_FACTURADO', 'SIN_CLASIFICAR')
);

UPDATE recibos
SET estado_documento = CASE
    WHEN estado_facturacion = 'FACTURADO' THEN 'FINAL'
    WHEN NULLIF(TRIM(COALESCE(numero_autorizacion, '')), '') IS NOT NULL
        THEN 'LISTO_AUDITORIA'
    ELSE 'PRELIMINAR'
END
WHERE estado_documento IS NULL OR TRIM(estado_documento) = '';

UPDATE recibos
SET estado_documento = 'FINAL'
WHERE estado_facturacion = 'FACTURADO' AND estado_documento <> 'FINAL';

UPDATE recibos
SET estado_documento = 'LISTO_AUDITORIA'
WHERE estado_facturacion IN ('PENDIENTE', 'SIN_CLASIFICAR')
  AND estado_documento = 'PRELIMINAR'
  AND (
      COALESCE(tipo_cobertura, 'ASEGURADO') = 'NO_ASEGURADO'
      OR NULLIF(TRIM(COALESCE(numero_autorizacion, '')), '') IS NOT NULL
  );

UPDATE recibos
SET auditoria_asignada_a = NULL,
    auditoria_asignada_at = NULL
WHERE estado_documento = 'PRELIMINAR'
  AND auditoria_asignada_a IS NOT NULL;

ALTER TABLE recibos DROP CONSTRAINT IF EXISTS recibos_estado_documento_check;
ALTER TABLE recibos ADD CONSTRAINT recibos_estado_documento_check CHECK (
    estado_documento IN ('PRELIMINAR', 'LISTO_AUDITORIA', 'FINAL')
);

CREATE TABLE IF NOT EXISTS recibo_facturacion_history (
    id SERIAL PRIMARY KEY,
    recibo_id INTEGER NOT NULL REFERENCES recibos(id) ON DELETE CASCADE,
    estado_anterior TEXT,
    estado_nuevo TEXT NOT NULL,
    realizado_por TEXT NOT NULL,
    realizado_at TEXT NOT NULL,
    motivo TEXT,
    observacion TEXT,
    referencia TEXT,
    total_al_momento DOUBLE PRECISION NOT NULL,
    ars_al_momento TEXT,
    recibo_version INTEGER NOT NULL DEFAULT 0,
    CHECK (estado_anterior IS NULL OR estado_anterior IN (
        'PENDIENTE', 'FACTURADO', 'NO_FACTURADO', 'SIN_CLASIFICAR'
    )),
    CHECK (estado_nuevo IN (
        'PENDIENTE', 'FACTURADO', 'NO_FACTURADO', 'SIN_CLASIFICAR'
    ))
);

ALTER TABLE recibo_facturacion_history ADD COLUMN IF NOT EXISTS evento_tipo TEXT NOT NULL DEFAULT 'CAMBIO_ESTADO';
ALTER TABLE recibo_facturacion_history ADD COLUMN IF NOT EXISTS motivo_codigo TEXT;
ALTER TABLE recibo_facturacion_history ADD COLUMN IF NOT EXISTS checklist_json TEXT;
ALTER TABLE recibo_facturacion_history ADD COLUMN IF NOT EXISTS preflight_json TEXT;
ALTER TABLE recibo_facturacion_history ADD COLUMN IF NOT EXISTS riesgo INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_recibos_billing_status
    ON recibos(estado_facturacion);
CREATE INDEX IF NOT EXISTS idx_recibos_billing_status_date
    ON recibos(estado_facturacion, estado_facturacion_at);
CREATE INDEX IF NOT EXISTS idx_recibos_created_status
    ON recibos(created_at, estado_facturacion);
CREATE INDEX IF NOT EXISTS idx_recibos_ars_status
    ON recibos(ars, estado_facturacion);
CREATE INDEX IF NOT EXISTS idx_recibo_billing_history_receipt
    ON recibo_facturacion_history(recibo_id, realizado_at);
CREATE INDEX IF NOT EXISTS idx_recibos_audit_queue
    ON recibos(estado_facturacion, auditoria_asignada_a, created_at);

COMMIT;
