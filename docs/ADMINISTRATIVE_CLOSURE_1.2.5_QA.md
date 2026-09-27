# Cierres administrativos — SIGEH 1.2.5

## Evidencia

Consulta central de solo lectura: el turno 3985 terminó el 26/09/2026 a las 19:59:23 (UTC-4) mediante `ADMIN_TURN_OVERRIDE`, pasando al 3986. No tiene fila en `billing_close_snapshots` ni cierre de Facturación. El turno 3983 presenta la misma omisión; el 3984 sí tiene captura y PDF. Se consultaron únicamente identidades de turno, marcas de tiempo y estados operativos.

## Corrección

Tres rutas admitían exclusivamente `PRIMARY_USER_HANDOFF`: captura transaccional, recuperación de cierres y alcance de atenciones heredadas. Ahora admiten cambios administrativos confirmados que realmente pasan a otro turno. Las correcciones con el mismo identificador y los eventos sin turno nuevo quedan excluidos. Se preservan los controles de periodo, identidad, transacción, captura inmutable y entrega sin duplicados.

La captura ausente de los turnos ya omitidos no se reconstruye con datos actuales ni se modifica la auditoría histórica. La recuperación del documento de esos turnos queda pendiente de una fuente verificable de datos al cierre.

## Validación

Se reprodujo primero el fallo contra PostgreSQL local: el relevo normal pasó y el administrativo falló al no recuperar cierres. Después de corregir las tres rutas, 85 pruebas ampliadas pasaron, incluyendo corrección sin cambio de turno, turno nuevo ausente, rollback, reintentos, inmutabilidad y cantidades heredadas.

Regresión final: `python -m pytest tests --ignore=tests/test_admission_import_task_manager.py -q --tb=short --junitxml=output/closure-125-full.xml`: 1.756 passed, 0 failed, 1 skipped y 60 subtests passed (678,69 s). El módulo Qt aislado produjo 8 passed. La omisión exige activar expresamente una prueba de capacidad sobre una base real; no se ejecutó contra producción.

Coverage.py: los tres módulos de cierre suman 54 sentencias y 6 ramas, todas cubiertas (100 % de líneas y ramas). La cobertura Python no instrumenta ramas PL/pgSQL; estas se verificaron con casos reales de PostgreSQL desechable. Evidencia: `output/administrative-closure-coverage.json`, `output/administrative-closure-final.xml`, `output/closure-125-full.xml` y `output/closure-125-isolated-qt.xml`.

Ruff check y format: PASS. Mypy de los tres módulos: PASS. Radon: complejidad máxima 6; funciones con SQL modificado, 1. Pylint duplicate-code: sin hallazgos a partir de 6 líneas. Bandit señala dos avisos B608 sobre consultas construidas con alias internos; la revisión confirma parámetros de datos enlazados y alias constantes. No se añadieron entradas de usuario a identificadores SQL ni se desactivaron reglas.

## Corrección central aplicada

Migración `capture_billing_close_for_administrative_rollovers`: aplicada mediante Supabase. Solo reemplaza `sigeh_capture_billing_close`, sin modificar recibos, auditorías ni capturas previas. Consulta posterior: aceptación administrativa=true, exclusión de turno sin cambio=true, trigger habilitado=O. Los cierres previamente ausentes siguen ausentes.

Supabase advisors: 64 avisos informativos por RLS sin políticas (tablas internas), 3 advertencias de search_path y 1 de extensión en public ajenas a la función modificada. Esta conserva search_path fijo. Referencias: [search_path](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable), [extensión](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public), [RLS](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).

## Build y entrega

Build de aplicación PASS: `python -m PyInstaller --noconfirm --log-level WARN --distpath output/closure-125-app --workpath build/closure-125-app build_app.spec`. Build del actualizador 1.2.5 PASS (`output/direct-download-125-updater-build.log`).

Empaquetado PASS: `python release_packaging.py --dist output/closure-125-app/SIGEH --updater output/direct-download-125-updater/SIGEH_Updater.exe --output output/release-1.2.5 --version 1.2.5`.

SHA-256 final: `ef2afdedadeef8f0138eb105bfce4b98d2592690ec4d95776f224bc9fa6d27c8`.

La compilación anterior de 1.2.5 fue sustituida por este paquete. Smoke de la extracción final PASS: 1.822 hashes verificados, recuperación del lanzador desde instalación sintética previa, generación PDF, visor integrado, exportaciones de reportes, transferencia y componentes de Admisión; seis salidas 0. Evidencia: `output/direct-download-125-package-smoke.json`. El paquete no usa una conexión de producción para estas pruebas ni incluye credenciales.

## Cuatro pasadas y puertas

Funcionalidad: captura, recuperación y heredadas cubren ambos tipos de transición. Regresión: 1.764 pruebas y 60 subpruebas aprobadas, 0 fallos, 1 omisión explícita. Clean code: cambio limitado a filtros existentes, sin refactorización extensa, complejidad dentro del límite. QA: integración PostgreSQL, GUI, cobertura, análisis, build y smoke del ZIP ejecutados.

| Puerta | Resultado |
|---|---|
| Funcionalidad preventiva, unitarias, integración, regresión y bordes | PASS |
| Cobertura de los módulos de cierre | PASS — 100 % líneas y ramas Python; SQL verificado por casos |
| Formatter, lint, tipos y análisis estático | PASS — sin errores nuevos |
| Complejidad | PASS — máximo 6 en módulos de cierre; 7 en helpers nuevos de configuración |
| Duplicación | PASS — sin hallazgos nuevos en Pylint, umbral 6 líneas |
| Build y smoke del paquete | PASS |
| Seguridad del cambio | PASS — SQL parametrizado; alias internos constantes; sin credenciales en ZIP |
| QA final de la corrección preventiva | PASS |
| Impresión física en el hospital y próximo relevo real | NO VERIFICADO — requieren operación hospitalaria |
| Reconstrucción de cierres 3983/3985 sin captura | NO VERIFICADO — no se inventan ni sustituyen cifras históricas |

ESTADO FINAL: APROBADO PARA ENTREGA de la corrección preventiva. La recuperación de los cierres ya omitidos sigue pendiente y no se declara resuelta.
