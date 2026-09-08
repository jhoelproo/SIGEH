# SIGEH 1.1.7 — validación de entrega, 08/09/2026

## Implementación

- `admission_statistical_reports.py`: acepta horas AM/PM, con/sin segundos, en fechas ISO o día/mes/año. Regresión sintética con 137 atenciones. El cierre detecta filas que el reporte no pudo interpretar y evita generar un total incompleto.
- `CALCULOS_QT.py`: vínculo de recibos por UUID antes del identificador local, rechazo de colisiones de identidad y preservación de coincidencias heredadas inequívocas. Conserva el corte temporal de autorización. Añade `global_attention_id UUID` al detalle del cierre mediante migración aditiva e idempotente; el UUID se conserva al arrastrar pendientes.
- `excel_delivery_path.py`, `excel_printing.py`: copia validada a ruta corta antes de usar Office; instancia privada de Excel, ajuste de una página de ancho y múltiples páginas de alto, encabezado repetido y cierre de recursos.
- `excel_artifact.py`: recuperación explícita desde un workbook reconstruido y validado. Conserva los bytes del archivo dañado en una copia identificada y no sustituye el último respaldo válido por corrupción. La API predeterminada sigue rechazando reemplazar archivos corruptos.
- `turn_excel_delivery.py`: repara artefactos faltantes de envíos inciertos sin reenviar a la impresora ni repetir el relevo.
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: rutas de archivo reducidas, revisión de contenido en el nombre, guardado validado del listado cerrado, recuperación sin diálogos modales en el inicio, encabezados de impresión y verificación de integridad antes de reutilizar Excel.
- `ADMISION_PYSIDE6_V15/qt_compat.py`: restauración real de la ventana minimizada, conservando la búsqueda y la misma instancia del Historial.
- `sigeh_product.py`, `version_config.json`: versión 1.1.7. `tests/test_sigeh_update.py`: expectativas del canal actualizadas.
- Incluye los cambios previamente preparados de `admission_listing.py`, `billing_historical_cancellation.py`, `admission_v15_adapter.py`: anulación administrativa de heredadas sin recibos/reservas; coherencia de urgencias/consultas y actualización del Excel. Evidencia específica en `QA_20260908_HEREDADAS_Y_LISTADO.md`.

Pruebas nuevas: `test_report_recovery_117.py`, `test_closure_receipt_identity_117.py`, `test_closure_uuid_persistence_117.py`. Actualizadas: `test_excel_printing.py`, `test_turn_excel_post_commit.py`, `test_current_turn_dataset_20260822.py`, además de las pruebas de anulación del cambio anterior.

Se preservan los filtros legítimos, los recibos, el historial clínico y los cierres ya emitidos. No se relaja la exclusión de anuladas/claims ni se repite una impresión de resultado incierto. La actualización no recalcula automáticamente cierres históricos ya guardados.

## Pruebas y resultados

| Comprobación | Resultado |
|---|---|
| RED previo para horas AM/PM y UUID de recibos | PASS: seis regresiones reproducidas antes de corregir |
| RED previo para restauración del Historial | PASS: la ventana permanecía minimizada |
| Suite relacionada inicial | PASS: 120 pruebas |
| Validación integral de UUID y arrastre entre cierres | PASS: SQL real, migración, captura, recibo de otra estación, pendiente → autorizada |
| Suite completa final, coverage con ramas | **PASS: 1435 passed, 0 failed, 1 skipped; 60 subtests passed** |
| Microsoft Excel real, exportación del área de impresión | **PASS: 137 pacientes, 7 páginas, ninguno omitido** |
| Revisión visual de primera y última página renderizadas | PASS: tablas legibles, encabezados repetidos, última fila 137 presente |
| Ejecutable: `--check-v15-package` | PASS, exit 0; abre Admisión/Historial/configuración |
| Ejecutable: `--self-test-pdf` y `--self-test-reports` | PASS, exit 0; PDF y XLSX generados |
| Verificación de código en EXE/PYZ contra fuente | PASS: doce módulos coinciden |
| Integridad del ZIP y hashes por archivo | PASS: 1820 archivos verificados |

Omisión: prueba de capacidad que exige `RUN_REAL_CAPACITY_INTEGRATION=1`; no se modificó capacidad/mantenimiento físico. Avisos previos de deprecación: PyPDF2 y ttkbootstrap.tooltip. Las pruebas PostgreSQL se ejecutaron en instancias desechables; el servidor adicional se apagó al finalizar.

Límites comprobados: medianoche/mediodía, AM/PM con segundos, UUID distinto con mismo ID local, coincidencia UUID con otro ID/estación, cero/múltiples coincidencias heredadas, autorización antes/después del cierre, archivo ausente/corrupto, evidencia conservada, fallo de cola, contexto inválido, impresión incierta y recuperación idempotente, minimizar/restaurar con búsqueda preservada. También se ejecutaron las pruebas existentes de integración, rollback y concurrencia.

## Cobertura y calidad

Medición coverage.py real sobre líneas ejecutables añadidas/modificadas respecto a HEAD y módulos nuevos:

- **Líneas: 290/292 = 99,32 %. Ramas: 88/94 = 93,62 %.**
- Índices/vinculación de recibos, coincidencia heredada, parser de fechas y servicio nuevo de anulación: **100 % de líneas y ramas**.
- La función heredada completa `capture_shift_closure_snapshot` tiene 88,89 % de líneas y 77,27 % de ramas; las nuevas rutas de persistencia UUID fueron ejecutadas con PostgreSQL real. No se presenta la cobertura del cambio como cobertura total del sistema.
- Ruff: PASS en módulos nuevos/pequeños y pruebas nuevas; 0 problemas nuevos en los archivos heredados comparados con la base. Persisten avisos previos de los módulos monolíticos.
- Formato: `ruff format --check` PASS en los módulos pequeños/pruebas nuevas; convenciones conservadas en monolíticos. `git diff --check` y `py_compile`: PASS.
- Tipos: `mypy --follow-imports=silent --check-untyped-defs` PASS para módulos nuevos de entrega/artefactos/cola (con stubs openpyxl) y módulos de anulación/clasificación del cambio anterior. No se afirma tipado completo del monolito.
- Radon: funciones nuevas/separadas de vínculo ≤10; `_link_shift_receipts` bajó de 23 a 6. Se conserva la función heredada de captura sin una refactorización masiva: complejidad 63 frente a 61, por normalización opcional de UUID. Excepción documentada de código heredado, no función nueva.
- jscpd, límite de archivo 5 MB para incluir el monolito: 11 fuentes, 1,214 % de duplicación global, ningún bloque detectado que intersecte el cambio.
- Revisión de seguridad: SQL parametrizado, identificadores internos constantes, migración aditiva, permisos centrales para anulación, protección de recibos/claims, validación previa de artefactos, conservación de evidencia y paquete público sin credenciales/bases operacionales.

## Build y paquete

```powershell
python -m PyInstaller --noconfirm --distpath D:/SIGEH_RELEASE_117/dist --workpath D:/SIGEH_RELEASE_117/build build_app.spec
python -m PyInstaller --noconfirm --distpath D:/SIGEH_RELEASE_117/updater --workpath D:/SIGEH_RELEASE_117/updater-build build_updater.spec
python release_packaging.py --dist D:/SIGEH_RELEASE_117/dist/SIGEH --updater D:/SIGEH_RELEASE_117/updater/SIGEH_Updater.exe --output D:/SIGEH_RELEASE_117/package --version 1.1.7
```

Los tres comandos finalizaron correctamente. SHA256 del ZIP:

`6a572cf50d59d279cdf9e1dc9bc5703b15d4363a58db2304c1d65fbb033eda72`

## QA final y quality gates

Cuatro pasadas: requisitos funcionales; regresiones/persistencia; responsabilidades, duplicación y complejidad; QA, métricas, build y smoke.

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias, límites, integración aplicable, regresión | PASS |
| Cobertura del cambio y de la nueva lógica crítica | PASS |
| Formato en alcance, lint sin problemas nuevos, análisis estático | PASS |
| Tipos aplicables | PASS |
| Complejidad nueva y duplicación | PASS; excepción heredada descrita |
| Build, paquete, smoke, seguridad aplicable, QA local | PASS |
| Capacidad física de BD | N/A — fuera del cambio |
| Salida física de impresora del hospital | NO VERIFICADO — se verificó la salida paginada mediante Excel real |

## Evidencia y límites productivos

Evidencia local en `D:/SIGEH_RELEASE_117/`: `full-suite.xml`, `full-suite.log`, `coverage.json`, `changed-coverage.json`, `lint-comparison.json`, `duplication/`, `build.log`, `build-source-verification.json`, `smoke-results.json`, `package-verification.json`, `office-layout-check.json`, PDFs y renders de QA.

Una consulta productiva de solo lectura confirmó el cierre señalado con 59 pendientes y cero vínculos guardados con recibos. No permitió identificar inequívocamente sus autorizaciones. No se alteraron esos registros ni se usaron nombres para asociar recibos durante la corrección. No se afirma que ese cierre histórico haya sido reparado. Las correcciones verificadas previenen las rutas defectuosas detectadas para las siguientes operaciones.

**ESTADO FINAL: APROBADO PARA ENTREGA** — paquete 1.1.7 verificado; la impresión física y los cierres históricos específicos conservan los límites indicados.
