# SIGEH v1.0.7

## Estabilización crítica del turno operacional

- El `turn_id` central solo puede cambiar mediante un relevo explícito a un representante diferente o una corrección administrativa extraordinaria autorizada.
- El relevo normal actualiza representante, turno, generación e intervalo operacional dentro de una única transacción PostgreSQL.
- Los intervalos `8AM_8AM` persisten 24 horas; `8AM_8PM` y `8PM_8AM` persisten 12 horas.
- Seleccionar al representante actual para un relevo es un no-op absoluto: no reserva turno, no modifica generación y no toca el espejo local.
- La materialización `obtener_o_crear_turno()` de SQLite quedó aislada como espejo local y ya no puede crear ni sustituir un turno central.
- Heartbeat, sincronización, Historial, Excel, PDF y refresh de GUI permanecen como consumidores de la identidad operacional.
- La corrección administrativa del representante conserva `turn_id`, generación, intervalos y conteo.

## Protección del conteo e identidad

- El resumen del turno queda versionado por `operational_source_id`, `turn_id`, `generation` y `operational_revision`.
- Un fallo temporal, una identidad incompleta o un worker atrasado ya no reemplazan un conteo válido por cero.
- Una consulta válida y vacía sigue representándose correctamente como cero pacientes.
- La pérdida temporal de PostgreSQL conserva el último snapshot central confirmado y lo marca como temporalmente no verificado.
- Se agregaron eventos `OPERATIONAL_IDENTITY_CHANGED` y `TURN_SUMMARY_REFRESH` con trigger y datos técnicos no clínicos.

## Reportes estadísticos

- Se reforzó el snapshot inmutable compartido por vista previa, PDF y Excel.
- La exportación conserva el listado operacional oficial y su resumen estadístico derivado del mismo dataset.
- Los errores de lectura o exportación quedan categorizados sin presentar resultados parciales como válidos.

## Continuidad de producción

Esta actualización no ejecuta SEED, MERGE, reimportación de baseline, cierre automático de turno, recreación de PostgreSQL ni borrado de SQLite. El historial, el turno central y las sincronizaciones pendientes continúan sobre el mismo estado productivo.
