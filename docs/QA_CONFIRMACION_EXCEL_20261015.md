# QA — Confirmar listados con Excel editado

Fecha: 15/10/2026. Alcance: cambio y compilación locales. Sin publicación ni cambios de datos en producción.

ESTADO FINAL: APROBADO PARA ENTREGA

## IMPLEMENTACIÓN

- [CALCULOS_QT.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/CALCULOS_QT.py): separa la firma de los datos del expediente de la firma del archivo exportado. Permite confirmar un Excel modificado manualmente si el expediente registrado sigue correspondiendo a la exportación.
- El evento `CONFIRMADO_ENVIADO` conserva la ruta y añade SHA-256 del archivo original, SHA-256 al confirmar y el indicador `excel_modified`. Utiliza la auditoría y la transacción existentes; no requiere migraciones.
- La confirmación explica que marcará como enviados los pacientes e importes registrados en el expediente. Las correcciones externas del Excel no se importan automáticamente.
- Se conservan permisos, validación de datos obligatorios, comprobación de duplicados, bloqueo tras el envío, identificadores, conceptos, importes y fotografías históricas. Cambiar el contenido del expediente dentro del sistema sigue requiriendo generar de nuevo el Excel.
- Pruebas nuevas: [test_monthly_batch_confirmation.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_batch_confirmation.py) y [test_monthly_batch_confirmation_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_batch_confirmation_postgres.py).
- Los cambios previos del árbol de trabajo se preservaron. La comparación de calidad de esta tarea utiliza una copia del estado al comenzar, no el último commit publicado.

## PRUEBAS

- RED: la versión anterior rechaza el archivo después de modificar datos, formato o fórmulas. Evidencia: `output/oct15-excel-red.xml` y su log.
- Unitarias/interfaz: 21 casos. Incluyen Excel sin editar, datos/formato/fórmulas editados, firma inexistente o malformada, caracteres no ASCII, archivo ausente/inaccesible, denegación individual del permiso, cancelación y aceptación.
- Integración PostgreSQL: 5 casos sobre un servidor temporal exclusivo. Comprueban persistencia tras confirmar, auditoría de ambos archivos, importes congelados, bloqueo de una segunda confirmación, cambios internos del expediente, rollback por fallo de auditoría, reintento y solicitudes desde dos conexiones independientes.
- Regresión relacionada: 179 pruebas y 14 subpruebas PASS. Incluye Admisión → Facturación → listado, correcciones, selección, retirada y protección de listas enviadas.
- Condiciones de borde: datos vacíos, firma incompleta/inválida, archivo ausente, cero confirmaciones al cancelar y una sola confirmación ante dos solicitudes. No se utilizó una base de producción para QA.

## RESULTADOS

| Etapa | Passed | Failed/errors | Skipped |
|---|---:|---:|---:|
| Pruebas específicas | 26 | 0 | 0 |
| Regresión relacionada | 179 + 14 subpruebas | 0 | 0 |
| Regresión general | 2950 + 74 subpruebas | 0 | 1 |

Las etapas específicas y relacionadas se solapan: sus conteos no deben sumarse como casos únicos.

## COBERTURA

Medición real con `coverage.py --branch`, limitada a las sentencias ejecutables nuevas o modificadas frente al estado inicial de esta tarea:

- Líneas: 12/12, **100 %**.
- Ramas: 4/4, **100 %**.
- Lógica crítica modificada: **100 % de líneas y ramas**.
- Evidencia específica: `output/oct15-excel-specific-coverage-summary.json`.
- Consolidación con regresión general: PASS; 100.0 % de líneas y 100.0 % de ramas. Evidencia: `output/oct15-excel-coverage-summary.json`.

## CALIDAD

- Formatter: PASS en rangos modificados y ambos archivos de pruebas; se verificó que el formato no cambia el AST.
- Lint: PASS, cero errores nuevos frente al estado inicial; Ruff de las pruebas PASS.
- Type checker: PASS, cero errores nuevos de mypy frente al estado inicial.
- Static analysis/seguridad: PASS diferencial; Bandit no añade hallazgos. Permanecen 77 avisos heredados del archivo revisado; este cambio no constituye una auditoría de seguridad completa.
- Complejidad nueva máxima: **7**. La confirmación transaccional heredada baja de **24 a 19**; el controlador gráfico permanece en **11**. Excepción de alcance: se mantienen sus controles existentes sin una refactorización masiva ajena a la corrección.
- Duplicación: PASS, cero bloques nuevos detectados por Pylint `duplicate-code` con umbral de seis líneas, comparando los 16 archivos de aplicación involucrados en el árbol de trabajo.
- Evidencia: `output/oct15-excel-quality.json` y logs asociados.

## BUILD

- Comando: `python -X utf8 -m PyInstaller --noconfirm --distpath output/oct15-excel-app --workpath output/oct15-excel-build build_app.spec`.
- Resultado: PASS.
- Artefacto local: `output/oct15-excel-app/SIGEH/SIGEH.exe`.
- Evidencia: `output/oct15-excel-build.log`.
- La numeración publicada no se modificó. Este ejecutable es una compilación local de prueba.

## QA

- Diálogo real de Qt: PASS, botones Sí/No ejercitados con registros sintéticos, cancelación sin enviar y aceptación con actualización de vistas. Inspección visual: texto completo y botones visibles, 400 × 392 px, dentro de 1280 × 720.
- Capturas y evidencia: `output/oct15-excel-gui/` y `output/oct15-excel-gui.json`.
- Smoke tests del ejecutable: 4/4 PASS (arranque, PDF, visor e informes). Verificación del código empaquetado y de los recursos: PASS. Evidencias: `output/oct15-excel-smoke.json` y `output/oct15-excel-package-verification.json`.
- Cuatro pasadas: funcionalidad PASS; regresión PASS; clean code PASS; QA final PASS.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad, unit tests, integración, boundary tests | PASS |
| Regresión relacionada | PASS |
| Regresión general | PASS |
| Coverage de cambios y lógica crítica | PASS |
| Formatter, lint, tipos y análisis estático diferencial | PASS |
| Complejidad y duplicación nueva | PASS |
| Build | PASS |
| GUI | PASS |
| Smoke tests del ejecutable | PASS |
| Seguridad del cambio | PASS |
| Migración de base de datos | N/A — utiliza campos y auditoría existentes |
| QA final | PASS |

## PROBLEMAS PENDIENTES

- No quedan fallos de la corrección ni de las validaciones aplicables ejecutadas.
- Los cambios manuales del Excel no actualizan los datos internos; la pantalla de confirmación lo indica expresamente.
- No se ha publicado esta modificación en producción.

Prueba opcional omitida (ajena a la corrección de Excel):

- `tests.test_database_capacity_optimization_20260824.test_real_capacity_dry_run_preserves_operational_counts`: explicit real PostgreSQL capacity integration test

Regresión completa: 2951 casos únicos, 230 módulos y 49 grupos. Huella de código: `5766dc8ba5451e2b3b1477c9d102468f1220f83d203b0c10d39327368dbf956b`. Evidencia: `output/oct15-excel-test-summary.json` y `output/oct15-excel-verified-groups.json`.
