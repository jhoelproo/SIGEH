"""Repair only classifications contradicted by their own saved event payload."""


def repair_urgency_projection(connection):
    result = connection.execute(
        """UPDATE admission_attention_projection
           SET service_type='URGENCIA',readiness='INCOMPLETA',
               readiness_reasons='["La atención es de Urgencia, no facturable como Emergencia."]'::jsonb
           WHERE UPPER(TRIM(service_type))='EMERGENCIA'
             AND UPPER(TRIM(latest_payload_json->>'service_type')) IN ('URGENCIA','URGENCIAS')
             AND NOT COALESCE(is_deleted,FALSE)
             AND UPPER(COALESCE(source_status,'ACTIVA')) IN ('ACTIVA','PENDIENTE')"""
    )
    return result.rowcount
