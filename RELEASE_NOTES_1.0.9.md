# SIGEH v1.0.9

## Convergencia crítica de Admisión

- Los eventos parciales de una atención ya no pueden sustituir un `turn_id`
  válido por `0` ni separar el historial de su turno operacional.
- La generación de hoja incluye explícitamente la identidad del turno en el
  evento sincronizado.
- Online, Historial devuelve la proyección central vigente y agrega solamente
  pendientes locales, deduplicados por `global_attention_id`.
- El resumen cuenta en el total las atenciones de Emergencia, Urgencia y
  Consulta y conserva sus categorías separadas.
- El indicador de estado distingue una estación sincronizada de otra que aún
  tiene elementos pendientes por subir.

## Reducción crítica de egreso

- La sincronización incremental usa un único pull por ciclo, consulta primero
  una cabecera liviana y evita descargar payloads cuando el cursor está al día.
- Los cursores locales son monotónicos y un lote histórico bloqueado se
  recupera mediante la proyección central sin repetirlo indefinidamente.
- Se redujo la frecuencia de sincronización normal y se incorporó backoff ante
  errores de conexión.
- Los logs incluyen métricas agregadas de filas, bytes estimados, cursores y
  resultados sin información clínica.

## Continuidad de producción

La actualización no ejecuta SEED, MERGE, reimportación de baseline, cambio o
cierre de turno, recreación de PostgreSQL, borrado de SQLite ni modificación de
pacientes existentes. No contiene migraciones de esquema.
