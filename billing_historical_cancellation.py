"""Admin-only cancellation of unbilled inherited admissions, centrally audited."""

from contextlib import nullcontext
from uuid import UUID

from admission_hybrid import (
    AdmissionCloudRepository,
    AdmissionWriteBlocked,
    OperationalSession,
    canonical_role,
)


def inherited_attention_is_active_sql(source_column, attention_column):
    """Internal SQL fragments only: preserve unknown legacy rows, suppress tombstones."""
    return f"""NOT EXISTS (
        SELECT 1 FROM admission_attention_projection cancelled
        WHERE cancelled.source_instance_id={source_column}
          AND cancelled.attention_id={attention_column}
          AND (COALESCE(cancelled.is_deleted,FALSE)
               OR UPPER(TRIM(COALESCE(cancelled.source_status,'ACTIVA')))
                    NOT IN ('ACTIVA','PENDIENTE')
               OR UPPER(TRIM(COALESCE(cancelled.service_type,'EMERGENCIA')))<>'EMERGENCIA')
    )"""


def _validate_historical_identity(row, session):
    if not row:
        raise ValueError("La atención ya no está disponible.")
    if str(row["operational_source_id"]) != session.operational_source_id:
        raise ValueError("La atención pertenece a otro origen operativo.")
    if row["is_deleted"] or str(row["source_status"]).upper() == "ANULADA":
        raise ValueError("La atención ya está anulada.")
    if int(row["turn_id"] or 0) == int(session.turn_id or 0):
        raise ValueError("Esta acción solo corresponde a turnos anteriores.")


def validate_pending_inheritance(row, session):
    _validate_historical_identity(row, session)
    if not row["pending_inheritance"]:
        raise ValueError("La atención no es una heredada pendiente.")
    if row["has_receipt"]:
        raise ValueError(
            "La atención tiene facturación vinculada; no se puede anular aquí."
        )
    if row["has_claim"]:
        raise ValueError("La atención está reservada por Facturación; intente después.")


def _locked_attention(connection, global_id):
    return connection.execute(
        """SELECT p.*,
          EXISTS(SELECT 1 FROM admission_shift_inheritances i
            WHERE i.source_instance_id=p.source_instance_id AND i.attention_id=p.attention_id
              AND i.estado='PENDIENTE' AND i.receipt_id IS NULL) AS pending_inheritance,
          (EXISTS(SELECT 1 FROM recibos r
            WHERE r.admission_global_attention_id=p.global_attention_id
              OR (r.admission_atencion_id=p.attention_id
                AND COALESCE(r.admission_source_instance_id,'LEGACY')=p.source_instance_id))
            OR EXISTS(SELECT 1 FROM admission_shift_inheritances i
              WHERE i.source_instance_id=p.source_instance_id AND i.attention_id=p.attention_id
                AND (i.receipt_id IS NOT NULL OR i.estado='COMPLETADA'))) AS has_receipt,
          EXISTS(SELECT 1 FROM admission_billing_claims c
            WHERE c.source_instance_id=p.source_instance_id AND c.attention_id=p.attention_id
              AND (c.receipt_id IS NOT NULL OR
                   (c.processed_at IS NULL AND c.expires_at>NOW()))) AS has_claim
        FROM admission_attention_projection p
        WHERE p.global_attention_id=%s FOR UPDATE OF p NOWAIT""",
        (global_id,),
    ).fetchone()


def cancel_pending_inheritance(runtime, global_attention_id, reason):
    """Guard and canonical DELETE share one transaction; never enqueue offline."""
    if canonical_role(runtime.current_user) != "administrador":
        raise PermissionError("Solo Administrador puede anular heredadas pendientes.")
    reason = str(reason or "").strip()
    if len(reason) < 8:
        raise ValueError("Escriba un motivo de al menos 8 caracteres.")
    global_id = str(UUID(str(global_attention_id)))
    if runtime.offline or runtime.operational_session is None:
        raise AdmissionWriteBlocked("Se requiere conexión central y una sesión activa.")
    with runtime.host.connection_factory() as connection:
        connection.execute("SET LOCAL lock_timeout='2s'")
        connection.execute("SET LOCAL statement_timeout='10s'")
        # NOWAIT keeps this exceptional admin operation from queuing behind billing.
        connection.execute(
            "LOCK TABLE admission_billing_claims,recibos "
            "IN SHARE ROW EXCLUSIVE MODE NOWAIT"
        )
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"admission-sync:attention:{global_id}",),
        )
        raw_session = connection.execute(
            "SELECT s.* FROM admission_operational_sessions s "
            "JOIN sigeh_product_state p ON p.singleton=1 "
            "AND p.production_epoch_id=s.production_epoch_id "
            "WHERE s.operational_session_id=%s AND s.status='ACTIVE' FOR UPDATE OF s NOWAIT",
            (runtime.operational_session.operational_session_id,),
        ).fetchone()
        if not raw_session:
            raise AdmissionWriteBlocked(
                "La sesión operativa cambió; actualice el listado."
            )
        session = OperationalSession.from_mapping(dict(raw_session))
        row = _locked_attention(connection, global_id)
        validate_pending_inheritance(dict(row) if row else None, session)
        repository = AdmissionCloudRepository(lambda: nullcontext(connection))
        result = repository.cancel_attention(
            global_id,
            current_user=runtime.current_user,
            reason=reason,
            operational_session=session,
            device_id=runtime.device_id,
        )
        if not result or not result.get("is_deleted"):
            raise AdmissionWriteBlocked(
                "No se confirmó la anulación; no se guardó el cambio."
            )
    return result
