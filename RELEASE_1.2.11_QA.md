# SIGEH 1.2.11 — validación

**ESTADO FINAL: APROBADO PARA ENTREGA** del código y del paquete Windows.

Fuente validada: `8aede8171fbb0c2f3787cb6ec28dcf9a69bb1e8b`. Base de comparación: `61f6241`. La instalación física en las estaciones del hospital y el consumo mensual futuro son **NO VERIFICADO**; no constituyen resultados de laboratorio.

## IMPLEMENTACIÓN

- `query_snapshot_cache.py`: una respuesta en RAM, comprobada mediante una huella calculada por PostgreSQL en cada refresco. Los fallos de lectura no entregan una respuesta antigua.
- `CALCULOS_QT.py`: integración opcional en el lector de la cola de Facturación y limpieza al invalidar la caché. Se conservan la consulta de elegibilidad, los parámetros vinculados, los filtros, las reclamaciones, el orden y las verificaciones centrales.
- `admission_hybrid.py` y `patient_directory.py`: dejan de crear tres índices redundantes; mantienen las claves primarias y la unicidad.
- `supabase/migrations/20261006052126_reduce_duplicate_event_indexes.sql`: DDL con verificación de equivalencia y restricciones antes de retirar índices, límites de espera y ajuste de autovacuum/analyze. No contiene eliminación de registros.
- `sigeh_product.py`, `version_config.json` y siete archivos de pruebas: versión 1.2.11, regresiones y compatibilidad del actualizador.

Las pruebas nuevas son `test_query_snapshot_cache.py`, `test_supabase_free_plan_schema.py`, `test_supabase_free_plan_postgres.py` y `test_billing_snapshot_postgres.py`. Se actualizaron tres archivos existentes de pruebas.

## PRUEBAS

Unitarias: caché vacía, cambios, copias independientes, orden, parámetros, invalidación, errores, huellas inválidas y tiempos de ambas consultas. Los primeros ensayos RED detectaron descargas repetidas y creación de índices duplicados; después de las correcciones pasaron.

Integración: PostgreSQL 17 local desechable, consulta real de elegibilidad, correcciones desde otra estación sin cambiar la revisión, cancelaciones, borrado lógico, ARS deshabilitada, recibos existentes y reclamación por otro usuario. La migración conserva datos y unicidad, es idempotente y revierte ante índices incompatibles, parciales, vinculados a una restricción o sin el índice que debe conservarse.

Boundary cases: cero filas, una fila, transición a vacío, parámetros distintos, orden invertido, autorización y NSS con ceros iniciales, fallo de la comprobación y fallo de la descarga.

Regresión: se ejecutaron los módulos relacionados y la colección completa en 13 procesos agrupados, con las fuentes congeladas y configuración local aislada.

## RESULTADOS

| Ejecución | Passed | Failed / errors | Skipped | Subtests passed |
|---|---:|---:|---:|---:|
| Específica y módulos relacionados | 108 | 0 | 0 | 15 |
| Colección completa | 2.395 | 0 | 1 | 74 |
| Ensayo opcional de capacidad, activado aparte en PostgreSQL local desechable | 1 | 0 | 0 | 0 |

El caso omitido por defecto en la colección completa fue `test_real_capacity_dry_run_preserves_operational_counts`; se ejecutó posteriormente con `RUN_REAL_CAPACITY_INTEGRATION=1` y pasó. Hay **2.396 casos únicos validados** y 74 subpruebas; la ejecución específica se solapa con la colección completa y no se suma al total.

## COBERTURA

Coverage.py 7.15.4, con ramas reales: el código nuevo/modificado medido obtuvo **100% de líneas y 100% de ramas**. La caché crítica cubre 39/39 sentencias y 6/6 ramas; la integración modificada en Facturación cubre 12/12 sentencias y 2/2 ramas. Se ejecutaron las tres asignaciones de esquema/versión modificadas. Esto no representa la cobertura global del código heredado.

La cobertura de sentencias SQL por Coverage.py es N/A; sus reglas y rollback se comprobaron ejecutando la migración en PostgreSQL.

## CALIDAD

- Ruff: PASS en el módulo y pruebas nuevos; cero errores nuevos en los archivos heredados modificados.
- Formatter: PASS en el código/pruebas nuevos. Dos pruebas existentes ya requerían reformateo en la base y en la versión actual; no se hizo una reformulación masiva fuera del cambio.
- Mypy: PASS en el módulo nuevo, con cuerpos sin anotación comprobados; cero errores nuevos frente a la base en los módulos heredados.
- Bandit/análisis estático: PASS para el cambio; cero hallazgos nuevos, sin supresiones añadidas. Los avisos heredados del repositorio no se presentan como corregidos.
- Radon: complejidad máxima nueva **4**. La función heredada de elegibilidad conserva complejidad **14** y la carga de validación **10**, sin incremento. Se conserva su estructura para evitar cambiar reglas de facturación ajenas a este ajuste.
- jscpd 5.4.0: cinco archivos de producción completos, máximo de archivo 10 MB, mínimo seis líneas/50 tokens. Duplicación **2,342%**, frente a 2,346% en la base; cero bloques nuevos y cero líneas duplicadas en la caché. Pylint también encontró cero bloques adicionales.

## BUILD

PASS:

```powershell
python -X utf8 -m PyInstaller --noconfirm --log-level WARN --distpath output/supabase-free-app --workpath build/supabase-free-app build_app.spec
python -X utf8 -m PyInstaller --noconfirm --log-level WARN --distpath output/supabase-free-updater --workpath build/supabase-free-updater build_updater.spec
python release_packaging.py --dist output/supabase-free-app/SIGEH --updater output/supabase-free-updater/SIGEH_Updater.exe --output output/release-1.2.11 --version 1.2.11
```

Se comparó el bytecode incluido con la fuente vigente en nueve módulos/ejecutables y, adicionalmente, la versión de producto incluida en el actualizador. Coinciden.

## QA

PASS: arranque del lanzador, PDF de recibo sintético, visor de reportes, reportes, integridad V15 y ayuda del actualizador. El ZIP contiene **1.822 archivos**, todos verificados por tamaño y SHA-256, sin credenciales, bases locales ni logs de ejecución.

Actualización real del código empaquetado 1.2.10 → 1.2.11: PASS. Conserva byte a byte la configuración protegida y el historial sintético. Segundo ciclo con el actualizador ya reemplazado: PASS. Se sustituyó únicamente el callback final de abrir el lanzador para no abrir el acceso ni contactar producción. El segundo ciclo reaplica el mismo paquete; no equivale a probar una futura compilación todavía inexistente.

SHA-256 del ZIP: `f060c0c8fc5a453cabcc2c8f70be7126791003c6afd71c870c755a9091ae8da1`.

Las cuatro pasadas finales verificaron requerimientos, regresiones, clean code y QA. Se mantuvieron los parámetros SQL vinculados, las verificaciones de permisos, la propagación de errores y las protecciones del actualizador. No se realizó QA destructivo contra producción.

## QUALITY GATES

| Gate del cambio | Estado |
|---|---|
| Funcionalidad, unit tests, integración, regresión y boundary tests | PASS |
| Cobertura del código nuevo/modificado y crítico | PASS |
| Formatter y lint del cambio | PASS |
| Tipos, análisis estático y seguridad del cambio | PASS |
| Complejidad nueva / ausencia de incremento heredado | PASS |
| Duplicación | PASS |
| Build y smoke tests empaquetados | PASS |
| Integridad y conservación de datos en la actualización | PASS |
| Cuatro pasadas de QA final | PASS |
| Types/coverage de DDL PostgreSQL | N/A — se valida mediante ejecución e integración |

## PROBLEMAS PENDIENTES

El ahorro mensual real y la instalación en las tres estaciones requieren observar su uso después de actualizar. La cuota acumulada y las restricciones del proveedor no se restablecen por publicar una versión. Los avisos heredados de calidad/seguridad del repositorio quedan fuera de esta corrección; no se debilitó ninguna configuración para ocultarlos.

Evidencia local: `output/supabase-free-regression-results.json`, `supabase-free-capacity-real-local.xml`, `supabase-free-coverage-summary.json`, `supabase-free-quality.json`, `supabase-free-duplication.json`, `supabase-free-billing-comparison.json`, `release-1211-package-validation.json`, `release-1211-package-verification.json`, `release-1211-updater-product.json` y los trece XML de la regresión final. Los resultados de la cuenta y la auditoría de producción se conservan únicamente en el informe local privado.
