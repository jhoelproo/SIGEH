# SIGEH v1.1.0 — candidato de producción

## Estabilidad operacional

- Conserva las protecciones de v1.0.8/v1.0.9: un error de identidad, red o
  réplica local no se interpreta como cero pacientes; el resumen mantiene el
  último snapshot válido y un cambio N → 0 exige confirmación central.
- El historial online continúa leyendo PostgreSQL y combinando únicamente los
  pendientes locales de la estación, deduplicados por `global_attention_id`.
- El relevo normal registra `TURN_HANDOFF_REQUESTED` y
  `TURN_HANDOFF_COMMITTED` dentro de la misma transacción central.

## Transferencia remota de PRIMARY

- Un Administrador autenticado puede seleccionar otra estación SECONDARY
  adjunta, activa y con heartbeat reciente.
- La PRIMARY actual debe continuar saludable; si está desconectada se bloquea
  la operación.
- La transferencia usa advisory lock, row lock y revisión operacional esperada.
- PRIMARY anterior y destino intercambian sus roles sin cerrar sus logins y sin
  modificar `turn_id`, generación del turno, representante, historial o conteo.
- La confirmación muestra estación, rol, usuario, última actividad y salud. El
  estado de sincronización se presenta como no reportado cuando la estación no
  ha publicado esa métrica; no se afirma una sincronización inexistente.

## Facturación bypass

- Un recibo autorizado creado con “Continuar sin verificar” queda listo para
  auditoría cuando contiene autorización, aun sin atención de Admisión.
- Una autorización ASCII numérica con el mínimo configurado queda `CLEAR`.
- Letras, símbolos, dígitos no ASCII o una longitud inferior al mínimo dejan el
  documento completo con `PENDING_REVIEW` y una razón explícita.
- Sin autorización el recibo permanece `PRELIMINAR`.
- El snapshot documental v2 conserva origen, actor, rol, dispositivo, motivo de
  bypass, autorización y resultado de revisión sin crear una atención falsa.

## Capacidad y Supabase

- La medición real del 30/08/2026 fue 349 MB. Los 41.8 GB observados en el panel
  correspondían a egreso de red, no al tamaño de PostgreSQL.
- No se eliminó ninguna fila. El staging existente está `ANALYZED` e incompleto,
  y los PDF no tienen archivo externo verificado; ambos quedan protegidos.
- La retención de eventos deja de usar un plazo global de 7 días: atención usa
  un mínimo conservador de 180 días y directorio de pacientes 365 días.
- Toda purga técnica exige confirmación explícita y referencia de un respaldo
  recuperable verificado.

## Migración

- `20260830_billing_bypass_authorization_review.sql`: agrega de forma
  idempotente `review_status` y `review_reason`, clasifica recibos bypass
  existentes y marca como listos solamente los que ya tengan autorización.
- No modifica pacientes, atenciones, UUID, turnos, PRIMARY, baseline ni SQLite.

## Estado de la release

El código es candidato. No publicar ni desplegar v1.1.0 hasta completar en el
hospital la prueba con dos estaciones saludables, el respaldo recuperable y
las pruebas críticas de producción descritas en el plan de despliegue.
