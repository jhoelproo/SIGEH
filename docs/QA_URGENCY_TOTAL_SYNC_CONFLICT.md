# QA — urgencias ausentes del resumen del turno

Fecha: 09/09/2026. Alcance: únicamente el conjunto de atenciones que alimenta el resumen lateral de Admisión.

## Implementación

- `admission_v15_adapter.py`: una atención local del turno con sincronización `CONFLICT` puede completar el resumen cuando todavía no existe en la proyección central.
- La unión usa el UUID global. Si central ya contiene la misma atención, central conserva autoridad y la fila local no la sustituye ni la duplica.
- URGENCIA continúa separada de GENERAL y conserva las reglas de elegibilidad de Facturación. No se modificaron atenciones, turnos, recibos, heredadas ni datos productivos.
- `tests/test_urgency_total_sync_conflict.py`: reproduce el defecto visible y cubre la exclusión de duplicados.

## Diagnóstico

La consulta productiva de solo lectura encontró 45 emergencias centrales en el turno activo y ninguna urgencia central; la atención seleccionada en la captura no estaba en PostgreSQL. La interfaz mostraba 46 porque incorporaba una fila local pendiente, pero excluía las filas locales en conflicto del resumen aunque Historial sí las mostraba.

## Pruebas y resultados

- Regresión específica de Historial, resumen, Excel y cierre de turno: 97 passed, 0 failed.
- Suite completa: 1436 passed, 25 skipped, 0 failed, 60 subtests passed. Veinticuatro omisiones eran integraciones que requerían PostgreSQL local.
- Repetición de esas 24 integraciones contra bases PostgreSQL desechables: 24 passed, 0 failed.
- Una omisión restante: prueba explícita de capacidad productiva, N/A para este cambio.
- Coverage.py sobre líneas ejecutables modificadas: 12/12, 100 %. Las dos salidas de la condición de conflicto y la deduplicación por UUID fueron ejecutadas. Coverage.py no atribuyó ramas instrumentables a esas expresiones.

## Calidad

- Ruff: PASS, 0 errores nuevos.
- `py_compile`: PASS.
- `git diff --check`: PASS.
- Complejidad: las funciones heredadas modificadas mantienen sus valores anteriores (`_local_list_rows` 20; `load_turn_dataset_result` 14). Funciones nuevas: máximo 5.
- jscpd: 1,108 % global en los dos archivos examinados; ningún clon intersecta las líneas modificadas.
- Seguridad: lectura SQL parametrizada, sin cambios de permisos ni escrituras productivas.

## Build y QA

- PyInstaller: PASS.
- El módulo `admission_v15_adapter` dentro del ejecutable coincide con el código fuente compilado: PASS.
- Smoke aislado del ejecutable: carga V15, PDF y reportes con salida 0 y artefactos presentes: PASS.
- Impresión física: N/A, fuera de este cambio.

Evidencia local: `C:/SIGEH_QA_URGENCY_TOTAL/`.

ESTADO FINAL: APROBADO PARA ENTREGA.
