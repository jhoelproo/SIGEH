# Validación de la optimización de sincronización

Actualización del 24 de septiembre de 2026: el usuario confirmó haber realizado las comprobaciones en el hospital y autorizó integrar esta optimización en 1.2.3. La validación posterior con PostgreSQL local real y los nuevos cierres se documenta en `CONSISTENCY_AND_CLOSE_QA.md`. Las cifras de ahorro siguientes siguen siendo sintéticas; no se recibió una medición hospitalaria comparable para afirmar un consumo mensual concreto. El estado de candidato que figura más abajo corresponde a la evaluación original del 21 de septiembre.

Fecha: 21 de septiembre de 2026. Base: `f1f5d83`, SIGEH 1.2.2.
Rama: `codex/sync-transfer-budget`. Candidato local; no publicado.

## Implementación

- `admission_hybrid.py`: columnas explícitas, checkpoint resistente a compactación, carga paginada del turno activo y confirmación posterior a la materialización.
- `patient_directory.py`: cabeceras ligeras y actualización de pacientes ya almacenados, sin descargar todo el directorio al iniciar.
- `admission_v15_adapter.py`: paginación histórica, temporizador adaptativo sin ciclos duplicados y aviso de presupuesto.
- `transfer_budget.py`, `transfer_usage.py`: contadores locales, períodos, umbrales y exportación/combinación por estación.
- `CALCULOS_QT.py`: instrumentación del adaptador PostgreSQL y exportación de métricas desde el ejecutable.
- Pruebas: dos archivos nuevos y cinco archivos existentes actualizados. Inventario y decisiones en `SYNC_QUERY_INVENTORY.md` y `SYNC_OPTIMIZATION.md`.

Se conservan permisos, validación de identidad, historial central, operaciones críticas, consultas explícitas y mantenimiento de réplicas completas. Sin conexión solo se dispone de los datos previamente almacenados en la estación. La caché operativa no equivale a una copia completa del hospital.

## Pruebas y regresión

La suite con cobertura terminó con **1.618 passed, 0 failed, 29 skipped y 60 subtests passed**, en 497,39 segundos. Evidencia: `output/sync-optimization-final.log` y su XML. Después del último ajuste de notificación de carga inicial se ejecutaron **57 pruebas específicas: todas aprobadas** (`output/sync-final-targeted.log`).

La última suite completa sobre el estado final terminó con **1.622 passed, 0 failed, 29 skipped y 60 subtests passed**, en 499,81 segundos: `python -m pytest tests -q --junitxml=output/sync-optimization-delivery.xml`. Registro: `output/sync-optimization-delivery.log`. Quedan dos advertencias de deprecación preexistentes (PyPDF2 y ttkbootstrap).

Se probaron cursores 0/25.000, reinicio, rollback real en SQLite, falta de identidades, desconexión, paginación 0/1/3/501, compactación, temporizador ocupado, inicio repetido, umbrales del presupuesto, cambio de mes y fallos del medidor. PostgreSQL se probó mediante conexiones simuladas; no se ejecutó QA destructivo ni consultas contra producción.

Las 29 omisiones corresponden a PostgreSQL real: 28 no encontraron el servidor local de pruebas en `127.0.0.1:55432` y una requiere activar explícitamente la integración de capacidad. No se cuentan como aprobadas.

La primera pasada completa detectó dos expectativas antiguas en los dobles de prueba (parámetro OFFSET y construcción sin inicializador). Se actualizaron y las regresiones correspondientes pasaron. No se relajaron umbrales ni se ocultaron fallos.

## Cobertura y calidad

Medición real con `coverage.py --branch`, no estimada. Los dos módulos nuevos alcanzan **100% de líneas y 100% de ramas**. El detalle del código agregado/modificado está en `output/sync-changed-coverage.json`; la cobertura completa está en `output/sync-optimization-final-coverage.json`.

Sobre las líneas ejecutables agregadas/modificadas de los seis módulos: **292/295 líneas (98,98%) y 77/80 ramas (96,25%)**. Las tres rutas pendientes son el retorno de compatibilidad sin paginador, una respuesta vacía de cabeceras tras un avance concurrente y la presentación del aviso de presupuesto en la barra. La regla de umbrales sí está cubierta al 100%; falta QA visual de ese aviso en una estación real.

- Ruff: PASS en módulos nuevos y pruebas nuevas. Diferencial en módulos existentes: **0 errores nuevos**; permanecen 327 avisos anteriores en `CALCULOS_QT.py`.
- Formato: PASS en módulos y pruebas nuevos; `git diff --check`: PASS. No se reformateó masivamente código heredado.
- Mypy: PASS en ambos módulos nuevos con `--check-untyped-defs --follow-imports=silent --ignore-missing-imports`. Tipado integral de los módulos heredados: NO VERIFICADO.
- Compilación sintáctica: PASS en los seis módulos afectados.
- Bandit: PASS, 0 hallazgos en los módulos nuevos. No equivale a una auditoría de seguridad integral del sistema.
- Duplicación: PASS en módulos nuevos con Pylint duplicate-code, mínimo seis líneas. Duplicación global del repositorio: NO VERIFICADO.
- Complejidad: lógica nueva máxima 10. La función extraída `_pull_full_incremental` conserva complejidad 13 del código original. Funciones heredadas afectadas mantienen excepciones existentes (hasta 52 en historial); no se hizo una refactorización ajena al alcance. Evidencia: `output/sync-complexity-final.json`.

## Build y smoke test

Comando: `python -m PyInstaller --noconfirm --distpath output/sync-evaluation-build --workpath build/sync-evaluation build_app.spec`.

**Build: PASS**, registro `output/sync-evaluation-final-build.log`.

Ejecutable comprobado: `output/sync-evaluation-build/SIGEH/CALCULOS_QT.exe`, con su lanzador `SIGEH.exe`.

**Smoke: PASS** en arranque, creación de PDF, visor con PDF existente, exportaciones PDF/Excel y exportación JSON de consumo. Resultados en `output/sync-final-smoke/results.json`. La primera invocación del visor apuntó a un archivo inexistente y devolvió 1; la comprobación corregida sobre el PDF generado devolvió 0. No se probó una impresora física.

## Comparación

El escenario sintético de cuatro estaciones y veinte páginas por estación recibió 51.289.600 bytes con la versión base y 4.886.240 con el candidato, preservando las 4.000 filas visibles: **90,5% menos payload en ese escenario**. No representa una jornada hospitalaria ni garantiza el límite mensual de 5 GB.

## Cuatro pasadas y Quality Gates

| Gate | Estado | Alcance o limitación |
|---|---|---|
| Funcionalidad local | PASS | Implementación y pruebas del candidato |
| Unitarias / límites | PASS | Escenarios normales, vacíos, errores y transiciones |
| Integración local | PASS | SQLite real, adaptadores simulados y ejecutable |
| Regresión | PASS | Suite completa y pruebas específicas posteriores |
| Cobertura nueva/modificada | PASS | 98,98% líneas y 96,25% ramas; módulos nuevos 100% |
| Formatter / lint | PASS | Alcance modificado; sin errores nuevos |
| Tipos | PASS | Módulos nuevos; heredados no verificados integralmente |
| Análisis estático / seguridad | PASS | Comprobaciones locales descritas arriba |
| Complejidad nueva | PASS | Máximo 10; excepciones heredadas documentadas |
| Duplicación nueva | PASS | Detección local descrita arriba |
| Build / smoke | PASS | Paquete Windows de evaluación |
| Jornada real con varias computadoras | NO VERIFICADO | Requiere estaciones del hospital y comparación con Supabase |
| PostgreSQL real de pruebas / planes SQL | NO VERIFICADO | Sin entorno de staging disponible para esta validación |
| QA de producción | NO VERIFICADO | No desplegado; no se utilizó producción para QA |

La pasada funcional y la de regresiones se apoyan en las pruebas anteriores. La de clean code revisó responsabilidades, fallos del medidor, privacidad y complejidad diferencial. La de QA mantiene abierta la validación de campo, sin declarar un ahorro mensual no medido.

## Pendientes antes de despliegue

Confirmar número de estaciones e inicio del ciclo. Completar una jornada comparable con las computadoras reales, contrastar contadores con egress de Supabase y verificar impresión, latencia y operación offline. Medir también la fusión del historial cuando existen pendientes locales, que conserva el comportamiento anterior y puede descargar más filas.

**ESTADO FINAL: NO APROBADO PARA ENTREGA** — candidato validado localmente; falta la comparación real exigida antes de producción.
