# SIGEH v1.1.1 — hotfix de relevo operacional

## Corrección central

- Corrige la colisión de `transition_id` que podía revertir el relevo formal
  al registrar varios eventos de auditoría bajo la restricción única
  `uq_admission_operational_transition`.
- Cada relevo conserva un único registro canónico de transición. Los eventos
  auxiliares continúan auditándose sin competir por esa identidad única.
- Un reintento con el mismo `transition_id` solo se acepta cuando actor,
  estación, fuente operacional, turno previo, generación, revisión,
  representante actual, representante destino y demás contexto coinciden.
- Un ID reutilizado para otra operación produce un rechazo seguro y no crea
  un turno, generación, intervalo ni UUID alternativo.
- Si PostgreSQL confirmó el relevo pero se perdió la respuesta, el cliente
  relee la transición, verifica el `OperationalState` central y adopta el
  resultado ya confirmado.
- Los envíos concurrentes de la misma operación quedan serializados por el
  lock operacional central y convergen en una sola transición efectiva.

## Interfaz y continuidad

- El diálogo mantiene un solo cambio de turno en curso y reutiliza el contrato
  original durante la recuperación post-commit.
- La interfaz ya no inventa un UUID si una respuesta confirmada carece de
  `transition_id`; ese estado inconsistente se rechaza explícitamente.
- La actualización no ejecuta SEED, MERGE, reset de historial, cierre de turno,
  cambio de PRIMARY ni recreación de SQLite/PostgreSQL.
- No incluye migraciones destructivas ni altera pacientes, atenciones,
  facturación, UUID globales, turno vigente o representante vigente.

## Validación disponible

- Pruebas automatizadas para relevo normal, replay idempotente, colisión
  semántica, pérdida de respuesta post-commit, concurrencia, nueva operación
  con nuevo ID, `UniqueViolation` y doble envío visual.
- La publicación oficial queda condicionada a los gates físicos del mismo ZIP
  en PRIMARY y SECONDARY; estas notas no afirman pruebas físicas no ejecutadas.
