"""Capture the canonical closure dataset in the handoff transaction."""

from uuid import UUID

SCHEMA = r"""
CREATE TABLE IF NOT EXISTS billing_reporting_policy(
 singleton SMALLINT PRIMARY KEY CHECK(singleton=1),
 baseline_at TIMESTAMPTZ NOT NULL DEFAULT '2026-09-23T00:00:00-04:00',
 enabled_at TIMESTAMPTZ NOT NULL DEFAULT NOW());
INSERT INTO billing_reporting_policy(singleton) VALUES(1) ON CONFLICT DO NOTHING;
ALTER TABLE billing_reporting_policy ENABLE ROW LEVEL SECURITY;
CREATE TABLE IF NOT EXISTS billing_close_snapshots(
 source_id UUID NOT NULL, turn_id BIGINT NOT NULL, transition_id UUID NOT NULL UNIQUE,
 closed_at TIMESTAMPTZ NOT NULL, schema_version INTEGER NOT NULL DEFAULT 3,
 dataset JSONB NOT NULL, PRIMARY KEY(source_id,turn_id));
ALTER TABLE billing_close_snapshots ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION sigeh_capture_billing_close() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp SET timezone='America/Santo_Domingo'
AS $function$
DECLARE
 source UUID; old_turn BIGINT; new_turn BIGINT; start_at TIMESTAMPTZ;
 end_at TIMESTAMPTZ; baseline TIMESTAMPTZ; previous_turn BIGINT;
 captured JSONB; existing_transition UUID; enabled TIMESTAMPTZ;
BEGIN
 IF NEW.transition_id IS NULL
    OR NEW.event_type IS DISTINCT FROM 'TURN_HANDOFF_TRANSITION'
    OR NEW.details_json->>'status' IS DISTINCT FROM 'COMMITTED'
    OR NEW.details_json->'request'->>'transition_type' IS DISTINCT FROM 'PRIMARY_USER_HANDOFF'
    THEN RETURN NEW; END IF;
 source := (NEW.details_json->'request'->>'operational_source_id')::UUID;
 old_turn := (NEW.details_json->'result'->>'old_turn_id')::BIGINT;
 new_turn := (NEW.details_json->'result'->>'new_turn_id')::BIGINT;
 IF old_turn IS NULL OR old_turn=new_turn THEN RETURN NEW; END IF;
 SELECT transition_id INTO existing_transition FROM billing_close_snapshots
  WHERE source_id=source AND turn_id=old_turn;
 IF FOUND THEN
   IF existing_transition<>NEW.transition_id THEN
     RAISE EXCEPTION 'BILLING_CLOSE_TRANSITION_COLLISION';
   END IF;
   RETURN NEW;
 END IF;
 SELECT MIN(started_at),MAX(ended_at) INTO start_at,end_at
  FROM admission_operational_turn_intervals
  WHERE operational_session_id=NEW.operational_session_id AND turn_id=old_turn;
 IF start_at IS NULL OR end_at IS NULL THEN
   RAISE EXCEPTION 'BILLING_CLOSE_INTERVAL_MISSING';
 END IF;
 SELECT baseline_at,enabled_at INTO STRICT baseline,enabled
  FROM billing_reporting_policy WHERE singleton=1;
 IF end_at<enabled THEN RETURN NEW; END IF;
 SELECT i.turn_id INTO previous_turn FROM admission_operational_turn_intervals i
  JOIN admission_operational_sessions s USING(operational_session_id)
  WHERE s.operational_source_id=source AND i.turn_id<>old_turn
    AND i.ended_at<=start_at
  ORDER BY i.ended_at DESC,i.started_at DESC LIMIT 1;

 WITH admissions AS MATERIALIZED (
   SELECT p.global_attention_id::TEXT AS id,p.turn_id,
          p.created_at_effective_utc AS created_at,p.canonical_ars AS ars,
          (COALESCE(p.is_deleted,FALSE)
           OR UPPER(COALESCE(p.source_status,'')) IN ('ANULADA','CANCELLED','TOMBSTONED')
           OR UPPER(COALESCE(p.service_type,''))<>'EMERGENCIA'
           OR COALESCE(p.coverage_status,'')='SIN_SEGURO_DECLARADO'
           OR UPPER(COALESCE(p.canonical_ars,'')) IN ('SIN SEGURO','SENASA SUBSIDIADO','BANCO CENTRAL','YUNEN')) AS excluded
   FROM admission_attention_projection p
   WHERE p.operational_source_id=source AND p.global_attention_id IS NOT NULL
     AND p.created_at_effective_utc<end_at
     AND (p.turn_id=old_turn OR (p.created_at_effective_utc>=baseline
          AND EXISTS(SELECT 1 FROM admission_operational_turn_intervals i
                     JOIN admission_operational_sessions s USING(operational_session_id)
                     WHERE s.operational_source_id=source AND i.turn_id=p.turn_id
                       AND i.ended_at<=end_at)))
 ), receipts AS MATERIALIZED (
   SELECT r.id,r.ars,r.total,
          COALESCE(NULLIF(r.created_at,''),NULLIF(r.fecha,''))::TIMESTAMPTZ AS created_at,
          CASE WHEN COALESCE(NULLIF(r.autorizacion_at,''),NULLIF(r.created_at,''))::TIMESTAMPTZ<end_at
               THEN r.numero_autorizacion ELSE '' END AS authorization,
          linked.global_attention_id::TEXT AS attention_id,
          COALESCE(linked.is_deleted,FALSE)
           OR UPPER(COALESCE(linked.source_status,'')) IN ('ANULADA','CANCELLED','TOMBSTONED')
           OR (linked.global_attention_id IS NOT NULL
               AND (UPPER(COALESCE(linked.service_type,''))<>'EMERGENCIA'
                    OR COALESCE(linked.coverage_status,'')='SIN_SEGURO_DECLARADO'
                    OR UPPER(COALESCE(linked.canonical_ars,'')) IN
                       ('SIN SEGURO','SENASA SUBSIDIADO','BANCO CENTRAL','YUNEN'))) AS attention_excluded
   FROM recibos r
   LEFT JOIN LATERAL (
     SELECT p.global_attention_id,p.is_deleted,p.source_status,p.service_type,
            p.coverage_status,p.canonical_ars
     FROM admission_attention_projection p
     WHERE (r.admission_global_attention_id IS NOT NULL
            AND p.global_attention_id=r.admission_global_attention_id)
        OR (r.admission_global_attention_id IS NULL AND p.attention_id=r.admission_atencion_id
            AND p.source_instance_id=COALESCE(r.admission_source_instance_id,'LEGACY'))
     LIMIT 1
   ) linked ON TRUE
   WHERE r.is_deleted=0
     AND UPPER(COALESCE(r.estado_documento,'')) NOT IN ('ANULADO','CANCELADO','INVALIDO','CANCELLED')
     AND UPPER(COALESCE(r.estado_facturacion,'')) NOT IN ('ANULADO','CANCELADO','INVALIDO','CANCELLED')
     AND COALESCE(NULLIF(r.created_at,''),NULLIF(r.fecha,''))::TIMESTAMPTZ<end_at
     AND (COALESCE(NULLIF(r.created_at,''),NULLIF(r.fecha,''))::TIMESTAMPTZ>=start_at
          OR linked.global_attention_id::TEXT IN (SELECT id FROM admissions))
 ) SELECT jsonb_build_object(
     'source',source,'turn',old_turn,'previous_turn',previous_turn,
     'transition_id',NEW.transition_id,'started_at',start_at,'closed_at',end_at,
     'baseline',baseline,
     'canonical_receipt_count',(SELECT COUNT(*) FROM receipts
                               WHERE created_at>=start_at AND NOT attention_excluded),
     'admissions',COALESCE((SELECT jsonb_agg(to_jsonb(a)) FROM admissions a),'[]'::jsonb),
     'receipts',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM receipts r),'[]'::jsonb)
   ) INTO captured;
 INSERT INTO billing_close_snapshots(source_id,turn_id,transition_id,closed_at,dataset)
 VALUES(source,old_turn,NEW.transition_id,end_at,captured);
 RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION sigeh_capture_billing_close() FROM PUBLIC;
DROP TRIGGER IF EXISTS sigeh_capture_billing_close ON admission_operational_audit;
CREATE TRIGGER sigeh_capture_billing_close AFTER INSERT OR UPDATE
 ON admission_operational_audit FOR EACH ROW EXECUTE FUNCTION sigeh_capture_billing_close();

CREATE OR REPLACE FUNCTION sigeh_keep_billing_close_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path=public,pg_temp AS $function$
BEGIN RAISE EXCEPTION 'BILLING_CLOSE_SNAPSHOT_IMMUTABLE'; END;
$function$;
REVOKE ALL ON FUNCTION sigeh_keep_billing_close_immutable() FROM PUBLIC;
DROP TRIGGER IF EXISTS sigeh_keep_billing_close_immutable ON billing_close_snapshots;
CREATE TRIGGER sigeh_keep_billing_close_immutable BEFORE UPDATE OR DELETE
 ON billing_close_snapshots FOR EACH ROW EXECUTE FUNCTION sigeh_keep_billing_close_immutable();
"""


def install_close_snapshot_schema(connection):
    connection.executescript(SCHEMA)


def load_close_snapshot(connection, source, turn, *, closed_at=None):
    from billing_close_model import build_close_snapshot, instant

    try:
        source = str(UUID(str(source)))
    except ValueError:
        return None
    row = connection.execute(
        "SELECT dataset FROM billing_close_snapshots WHERE source_id=%s::UUID AND turn_id=%s",
        (str(source), int(turn)),
    ).fetchone()
    if not row and closed_at is not None:
        required = connection.execute(
            """SELECT 1 FROM billing_reporting_policy policy
               WHERE policy.singleton=1 AND %s::TIMESTAMPTZ>=policy.enabled_at
                 AND EXISTS(SELECT 1 FROM admission_operational_sessions
                            WHERE operational_source_id=%s::UUID)""",
            (instant(closed_at), source),
        ).fetchone()
        if required:
            raise RuntimeError(
                "Falta la captura central del relevo; reintente la generación del reporte."
            )
    return build_close_snapshot(**dict(row["dataset"])) if row else None
