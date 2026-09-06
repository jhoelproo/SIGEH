# SIGEH 1.1.5

## Cambios

- Historial de Emergencias permite enviar una atención a Facturación por su UUID central. Reutiliza reservas y recibos existentes; evita ofrecer el borrador seleccionado como un nuevo pendiente.
- Continúa y edita recibos de emergencias heredadas sin bloquearse contra su propio vínculo. Se mantienen los bloqueos de anulación, URGENCIA, reserva ajena y otro recibo.
- El diálogo de turno distingue relevo y corrección administrativa mediante una elección explícita. El horario nominal no selecciona por sí solo una corrección administrativa.
- Guardado Excel con temporal único validado, coordinación entre escritores y conservación del último archivo válido. Un archivo corrupto ya no se elimina como recuperación automática.
- Edición de paciente por identidad maestra y revalidación del mismo paciente conservando cargos y autorización cuando los datos clínicos compatibles lo permiten.

## Validación

Suite funcional final: 1263 aprobadas, 0 fallidas, 23 omitidas y 60 subtests aprobados. De las omitidas, 22 pruebas de integración pasaron en PostgreSQL temporal; queda una prueba optativa de capacidad. Importador y lanzador aislados: 8 y 5 aprobadas. Las cifras de suites solapadas no se suman.

Tras actualizar la versión, pruebas de actualización y empaquetado: 21 aprobadas. El primer intento señaló una expectativa de versión futura ahora igual a la actual; se actualizó la expectativa y se reejecutó el conjunto.

Módulo de handoff: cobertura real 100 % de líneas y ramas, complejidad máxima 9, duplicación 0 %, Ruff y Mypy PASS. Otros cambios y métricas detallados en `HISTORY_BILLING_HANDOFF_20260906.md`, `TURN_EXCEL_PREPARATION_20260906.md` y los informes de recibos/pacientes asociados. No se certifican cobertura o tipado integral de los monolitos heredados.

## Alcance de publicación

El usuario autorizó compilar y publicar la siguiente versión, y retiró el caso 399 como bloqueo. No se afirma que la diferencia entre Historial y selector sea imposible: el acceso conserva las reglas centrales de elegibilidad y requiere una atención sincronizada.

La publicación no ejecuta cambios manuales sobre datos hospitalarios ni demuestra QA física entre estaciones. Persisten las limitaciones generales documentadas. La autorización de publicación no convierte controles no ejecutados en PASS.

ESTADO FINAL de homologación integral: NO APROBADO PARA ENTREGA.
