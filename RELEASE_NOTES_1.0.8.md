# SIGEH v1.0.8

## Hotfix crítico del conteo del turno

- El resumen ya no interpreta una identidad operacional ausente, un error de PostgreSQL o una réplica SQLite incompleta como un turno con cero pacientes.
- Se incorporó un contrato explícito que diferencia datasets válidos, vacíos válidos, identidad no disponible, base central no disponible, réplica local atrasada y errores de consulta.
- El último resumen y dataset válidos permanecen unidos a `operational_source_id`, `turn_id`, `generation` y `operational_revision`.
- Los resultados de workers antiguos o de otra identidad operacional se descartan antes de alcanzar la GUI.
- Una caída de N pacientes a cero dentro del mismo turno requiere una segunda confirmación central, limitada a un único recheck.

## Operación online y offline

- Online, el conteo consulta directamente `admission_attention_projection` para el turno central exacto y suma atenciones locales pendientes sin duplicarlas por `global_attention_id`.
- Offline, el último dataset confirmado se combina con altas y tombstones locales del mismo turno; la pérdida y recuperación de red no producen un cero intermedio.
- Una réplica SQLite temporalmente vacía no puede sobreponerse a un conteo central válido.
- La pantalla inicial muestra un marcador de carga hasta obtener evidencia válida, en vez de mostrar un cero no confirmado.

## Diagnóstico

- Se agregaron eventos técnicos `TURN_SUMMARY_REFRESH_START`, `TURN_SUMMARY_DATASET_RESULT`, `TURN_SUMMARY_APPLY`, `TURN_SUMMARY_REJECTED` y `TURN_SUMMARY_TOTAL_CHANGED`.
- Los logs comparan conteos central, local, pendiente y visible sin registrar información clínica de pacientes.

## Continuidad de producción

Esta actualización no ejecuta SEED, MERGE, reimportación de baseline, cambio o cierre de turno, recreación de PostgreSQL, borrado de SQLite ni transferencia remota de PRIMARY. No incluye rediseños generales de Reportes o Facturación.
