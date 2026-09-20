# Validación de SIGEH 1.2.0

## Implementación

| Archivos | Cambio |
| --- | --- |
| `patient_directory.py` | Validar conflictos solo para identificadores modificados; conservar revisión, auditoría y protección contra duplicados reales. |
| `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py` | Foco diferido del buscador; botón y selección del historial de turnos; integración con filtros y exportaciones existentes. |
| `admission_turn_history.py`, `admission_turn_history_dialog.py`, `admission_v15_adapter.py` | Consulta paginada de turnos y pacientes por usuario original; control de carga, errores y cambios de filtros. |
| `billing_closure_categories.py`, `billing_closure_recovery.py`, `CALCULOS_QT.py` | Categorías actual/heredada/histórica, autorización mínima de cuatro dígitos, selección del recibo autorizado y migración compatible con cierres antiguos. Apertura del visor en el hilo gráfico sin bucle modal anidado. |
| `report_engine/report_template.html` | Históricas pendientes y facturadas separadas; reglas originales para snapshots antiguos; paginación sin hoja casi vacía. |
| `sigeh_product.py`, `version_config.json` | Versión 1.2.0. |
| Pruebas nuevas de turnos, categorías, autorización, foco y documentos; regresiones existentes actualizadas | Cobertura de los cambios y compatibilidad. |

Se preservan las restricciones de permisos, la autoría original, los identificadores de atención, la auditoría, los recibos y cierres existentes. No se implementa la función pospuesta de extranjeros ni se regeneran cierres antiguos en producción. El historial nuevo consulta metadatos y filas de pacientes; PDF y Excel se crean localmente, sin cargar documentos a la nube.

## Pruebas

- RED/GREEN: reproducidos el conflicto falso por documentos sin modificar, la pérdida del foco y la aceptación de autorizaciones inferiores a cuatro dígitos. Las pruebas fallaron antes de corregirlos y pasan después.
- Unitarias: clasificación actual/heredada/histórica, conteos que concilian con el total, autorización nula/vacía/3/4/más dígitos, selección del recibo autorizado, conflictos de documentos modificados y eliminación de identificadores.
- Integración: PostgreSQL 17 local aislado, paginación sin repeticiones, usuario original frente a editor, migración repetida y conservación de la fecha de activación; recuperación y generación de cierres y documentos.
- GUI: foco y escritura, navegación del historial, selección y generación, filtros, estado vacío/error, bloqueo de consultas duplicadas y callbacks después de cerrar. Visor PDF real y navegación del reporte estadístico.
- Exportaciones: PDF y Excel coherentes con las mismas filas, generado localmente; revisión visual de ambas páginas del cierre con datos ficticios.
- Regresión temporal: autorizaciones anteriores/durante/posteriores al turno, relevo con precisión de microsegundos, arrastre entre varios días y conservación de snapshots anteriores.

## Resultados

Suite completa final: **PASS — 1,599 passed, 0 failed, 1 skipped, 60 subtests passed**, 337.34 segundos. Dos advertencias de deprecación de dependencias existentes.

Comando: `python -m coverage run --branch -m pytest tests -q -rs --junitxml=output/release-120-regression.xml`, mediante el arnés local que inicia y detiene PostgreSQL y configura Qt offscreen.

La prueba omitida requiere habilitar expresamente una comprobación de capacidad de PostgreSQL de producción; no corresponde al QA funcional local. Una primera ejecución encontró una expectativa antigua de tres opciones de turno; se actualizó para la cuarta opción y se repitió la suite completa. Comprobación adicional tras formatear pruebas: **19 passed**.

Evidencia: `output/regression-120-final.log`, `output/release-120-regression.xml`.

## Cobertura

Medida con coverage.py, sobre líneas ejecutables modificadas y módulos nuevos completos:

| Archivo | Líneas | Ramas |
| --- | --- | --- |
| Interfaz de Admisión V15 | 30/30 | 8/8 |
| CALCULOS_QT.py | 33/33 | 2/2 |
| admission_turn_history.py | 27/27 | 6/6 |
| admission_turn_history_dialog.py | 117/117 | 26/26 |
| admission_v15_adapter.py | 14/14 | 2/2 |
| billing_closure_categories.py | 25/25 | 4/4 |
| billing_closure_recovery.py | 1/1 | N/A: sin ramas modificadas |
| patient_directory.py | 13/13 | 6/6 |
| Total del cambio | **260/260 — 100 %** | **54/54 — 100 %** |

No se atribuye este porcentaje a toda la aplicación heredada. La lógica crítica nueva de clasificación está cubierta al 100 %. Evidencia: `output/coverage-120.json`, `output/diff-coverage-120.json`.

## Calidad

- **PASS:** Ruff formatter para módulos nuevos y pruebas nuevas; se preserva el formato de los grandes módulos heredados sin reformatearlos masivamente.
- **PASS:** Ruff lint de módulos nuevos/pruebas; comparación con el commit base de los cuatro módulos heredados: cero hallazgos nuevos (`output/quality-120.json`).
- **PASS:** Mypy en los tres módulos nuevos, `--follow-imports=skip`. No se afirma tipado íntegro de dependencias ni de la aplicación heredada.
- **PASS:** Compileall y `git diff --check`.
- **PASS:** Radon: complejidad máxima de funciones nuevas **9**; validación nueva de documentos **6**. Se conserva la deuda previa de los orquestadores heredados, sin una refactorización fuera de alcance.
- **PASS:** jscpd 5.3.0: **0 clones, 0 % duplicación**, 419 líneas de módulos y funciones nuevos analizadas (`output/duplication-120/jscpd-report.json`).

## Build

**PASS:** `python -m PyInstaller --noconfirm --distpath output/release-120-build --workpath build/release-120 build_app.spec` y equivalente para `build_updater.spec` con destinos separados. Se usó el recurso SumatraPDF existente del proyecto para la impresión empaquetada.

**PASS:** empaquetado con `release_packaging.prepare_release`, CRC del ZIP, tamaño/SHA-256 de **1,822 archivos**, versión interna/externa 1.2.0 y coincidencia de la plantilla empaquetada con el código final.

ZIP: `SIGEH-1.2.0-windows-x64.zip`, 309,823,170 bytes.

SHA-256: `b171dcd220feb2ba7109fd4728b24a8d8f51fa297d5b1a0f734488117a136175`.

## QA

- **PASS:** ZIP extraído, `SIGEH.exe --self-test` recuperando configuración sintética de una instalación previa; `CALCULOS_QT.exe --self-test-pdf` y `--self-test-reports`.
- **PASS:** apertura real del PDF en QPdfView, ventana visible y utilizable sin guardar copia; documentos legibles revisados con Poppler. Evidencia local `output/closure-120-page-1.png`, `output/closure-120-page-2.png`, `output/viewer-120-visual.png` y `output/history-120-visual.png`.
- **PASS:** seguridad revisada: SQL parametrizado, entrada de búsqueda con apariencia de inyección tratada como texto, permisos existentes de reportes, ningún secreto en el paquete público y QA de escritura exclusivamente local.
- **NO VERIFICADO:** salida física en la impresora del hospital; el dispositivo no está disponible. El despacho de apertura/impresión está cubierto por pruebas de software.

## Cuatro pasadas y quality gates

Funcionalidad revisada requisito por requisito; regresiones de flujos relacionados ejecutadas; clean code revisado; QA y evidencias verificados.

| Gate de entrega de software | Estado |
| --- | --- |
| Funcionalidad | PASS |
| Unitarias, integración, regresión y límites | PASS |
| Cobertura | PASS |
| Formatter y lint del cambio | PASS |
| Análisis estático y tipos aplicables | PASS |
| Complejidad y duplicación nuevas | PASS |
| Build y smoke test del paquete | PASS |
| Seguridad del cambio | PASS |
| QA final de software | PASS |
| Prueba de capacidad de producción | N/A — no se realiza carga contra producción para esta corrección |
| Impresora física | NO VERIFICADO — dispositivo hospitalario no accesible; fuera de la certificación local del paquete |

## Problemas pendientes y alcance

No se fusionan fichas duplicadas reales: cambiar un identificador a uno ajeno sigue rechazándose. El historial de turnos requiere conexión central y depende de los turnos/autorías persistidos; no inventa turnos que no estén registrados. Los reportes ya capturados no se reescriben con reglas nuevas. Las nuevas categorías se aplican a los siguientes cierres capturados.

La publicación permite descargar/actualizar la aplicación; no acredita que todas las estaciones del hospital ya hayan sido actualizadas. Es necesario actualizar todas las estaciones para evitar que una versión anterior capture cierres con las reglas anteriores.

**ESTADO FINAL: APROBADO PARA ENTREGA** del software validado. La salida física en papel conserva la limitación indicada.
