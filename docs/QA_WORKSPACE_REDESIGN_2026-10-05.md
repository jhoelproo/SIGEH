# Rediseño y consistencia de recibos y listados — QA del 5 de octubre de 2026

ESTADO FINAL: APROBADO PARA ENTREGA

Actualización posterior al comentario del PDF: la posición y el color del NSS se ajustaron de nuevo. La validación de ese ajuste y el build actualizado están en `QA_NSS_POSITION_COLOR_2026-10-05.md`.

Esta aprobación corresponde al cambio local y al ejecutable compilado. Las comprobaciones se realizaron con datos sintéticos, Qt en modo offscreen y PostgreSQL local. No constituye una comprobación en las computadoras del hospital ni de una impresora física.

## IMPLEMENTACIÓN

| Archivo | Funcionalidad |
| --- | --- |
| `CALCULOS_QT.py` | Integra el rediseño, NSS visible, controles de historial, búsqueda y ordenamiento; correcciones compartidas entre recibos y expedientes pendientes; recuperación de sesión sin el aviso repetitivo. |
| `workspace_design.py` | Estilos para temas claro y oscuro, títulos, métricas y paneles de detalle con adaptación al espacio disponible. |
| `workspace_selection.py` | Casillas que reflejan la selección nativa de filas y selección de las filas visibles. |
| `billing_workspace_design.py` | Panel de datos del paciente con NSS y resumen, disposición del catálogo y recibo, totales y adaptación de controles al ancho de ventana. |
| `catalog_workspace_design.py` | Añadir por fila, favoritos, orden por nombre/precio y paginación local de 8, 13 o 20 ítems. Nombres largos con texto completo en tooltip. |
| `monthly_workspace_design.py` | Métricas de pacientes, filtros Todos/Por revisar/Listos/Con errores, detalles, corrección y menú de acciones por fila. |
| `receipt_history_design.py` | Filtros compactos y avanzados, cuatro métricas, panel de detalle, ordenamiento, selección y accesos a las acciones existentes. |
| `receipt_list_consistency.py` | Validación, resolución de copias heredadas, bloqueo ordenado y sincronización de identificación, autorización y especialidad con auditoría. |
| `receipt_authorization_dialog.py` | Diálogo de autorización que guarda en segundo plano, valida entrada y actualiza el historial después del éxito. |
| `pdf_engine/renderer.py`, `pdf_engine/template.html`, `pdf_engine/styles.css` | NSS en los datos del paciente del PDF, con conservación de ceros iniciales y ajuste de texto. |

Pruebas añadidas: `test_workspace_design.py`, `test_receipt_nss_design.py`, `test_receipt_list_consistency.py`, `test_receipt_list_consistency_postgres.py`, `test_receipt_authorization_dialog.py`, `test_login_silent_recovery.py` y `test_receipt_metadata_policy.py`, dentro de `tests/`.

### Requerimientos comprobados

- Facturación, Listados ARS e Historial incorporan los controles funcionales de las referencias, adaptados a los widgets nativos Qt.
- NSS visible en Facturación y en el PDF, dentro de los datos del paciente. El PDF indica `NO REGISTRADO` cuando falta.
- Correcciones desde un expediente pendiente se guardan en el recibo y sus otros expedientes pendientes. Las correcciones desde el recibo actualizan esos expedientes.
- La última corrección guardada prevalece. Los registros heredados se reconcilian mediante identificadores explícitos de recibo y fechas de edición.
- Guardar únicamente autorización no inventa un NSS ni vuelve listo un paciente al que le faltan datos para el envío.
- Autorización disponible en botón, panel de detalle y menú contextual del historial, según los permisos existentes. Permite de 4 a 40 dígitos ASCII y conserva ceros iniciales.
- El diálogo no permite doble guardado ni cierre durante la operación; informa errores y conflictos de otra estación sin sobrescribirlos silenciosamente.
- Se conserva el acceso a papelera de los auditores autorizados. Retirar del expediente conserva el recibo y el historial.
- El aviso informativo de sesión recuperada se eliminó; autenticación, recuperación y errores siguen funcionando.

### Comportamiento preservado

La corrección rápida no cambia precios, ítems, importe, fecha de servicio, origen ni estado de facturación. Los botones del rediseño invocan las operaciones y permisos existentes. Se conserva paginación del historial en el servidor y cálculo del recibo. Los expedientes enviados, cerrados o cancelados mantienen su copia emitida; las correcciones afectan a los expedientes editables.

No se enlazan documentos por nombre del paciente. La sustitución confirmada del original 4006 por el alterno 992133 ya estaba registrada en el expediente 34: se verificó mediante lectura; no fue necesario modificar producción.

## PRUEBAS

### Unitarias y casos de borde

101 pruebas aprobadas en la última ejecución de los seis módulos rápidos del cambio. Incluyen autorización vacía, espacios, caracteres no ASCII, 3/4/40/41 dígitos, ceros iniciales; documento vacío y límites de 24 caracteres; fechas de edición inválidas, distintas zonas horarias y correcciones vacías explícitas; permisos, datos vacíos, primera/última página, orden y favoritos, casillas, estados y diálogos.

```powershell
python -X utf8 -m pytest tests/test_workspace_design.py tests/test_receipt_nss_design.py tests/test_receipt_list_consistency.py tests/test_receipt_authorization_dialog.py tests/test_login_silent_recovery.py tests/test_receipt_metadata_policy.py -q --tb=short --junitxml=output/workspace-unit-final.xml
```

### Integración PostgreSQL

`tests/test_receipt_list_consistency_postgres.py` prueba persistencia en PostgreSQL local aislado: ambos sentidos de corrección, datos heredados presentes solamente en el listado, copia emitida inmutable, revisiones del documento, datos financieros preservados, operaciones sin cambios, recibo inexistente, membresía inválida, permisos, rollback por error de documento y conflicto entre dos estaciones.

Evidencia específica: `output/workspace-specific-final.xml` contiene 57 comprobaciones aprobadas. La suite relacionada posterior contiene 183 aprobadas, incluyendo integración y regresión. No se hizo QA destructivo contra producción.

### RED → GREEN y regresiones

- En la versión anterior, cinco pruebas reprodujeron la desincronización y el aviso repetitivo: `output/receipt-list-baseline-red.log` y su JUnit. Luego pasaron con la corrección.
- Dos pruebas adicionales fallaron antes de recuperar el NSS heredado presente solamente en el listado: `output/workspace-legacy-identity-red.log`. Después pasaron tanto la edición rápida como la edición completa.
- La regresión detectó que el rediseño ocultaba el botón de papelera al auditor autorizado. Se conservó el botón y se ejecutaron nuevamente las seis pruebas de ese acceso: `output/workspace-trash-regression.xml`.
- Se comprobaron los controles heredados sin el controlador visual, conservación de filtros cargados, combinación de estado con filtro Listos y menú de autorización para administrador, auditor y rol sin permisos. Los 28 tests finales del módulo UI pasaron.

Orden seguido: pruebas específicas, módulos del cambio, suite relacionada, suite completa y regresión de los controles afectados por los últimos ajustes.

## RESULTADOS

| Ejecución | Aprobadas | Fallidas/errores | Omitidas |
| --- | ---: | ---: | ---: |
| Unitarias finales | 101 | 0 | 0 |
| Específica de integración y UI | 57 | 0 | 0 |
| Suite relacionada | 183 | 0 | 0 |
| GUI/autorización/NSS tras ajuste visual | 46 | 0 | 0 |
| Módulo UI final, con menús y filtros | 28 | 0 | 0 |
| Regresión de papelera | 6 | 0 | 0 |
| Suite completa en 12 grupos | 2.244 | 0 | 1 |

Las filas se solapan: no se suman entre sí. El recuento completo corresponde a 2.245 casos JUnit, incluidos subcasos de pruebas parametrizadas/unittest. Evidencia: `output/workspace-final-results.json`, `output/workspace-design-group-1.xml` a `workspace-design-group-12.xml` y `output/workspace-design-regression-groups.json`.

La única omisión es `test_real_capacity_dry_run_preserves_operational_counts`, una prueba optativa de capacidad que requiere habilitar explícitamente una base real. No corresponde al comportamiento modificado; no se habilitó contra producción.

El gestor heredado de importación necesita mantener vivo `QApplication` durante todo su módulo de pruebas para evitar destruir/recrear Qt entre casos. Se ejecutó en un proceso aislado con esa referencia viva: ocho pruebas aprobadas, sin cambiar el código de importación ni sus aserciones.

Los primeros siete grupos pasaron antes del último ajuste de visibilidad/estilos; los grupos restantes y las pruebas GUI afectadas se ejecutaron después. Los últimos cambios de pruebas no alteraron el código de aplicación empaquetado.

## COBERTURA

Medida con `coverage.py --branch`, no estimada. Las métricas del archivo heredado se calculan sobre las sentencias modificadas semánticamente respecto a HEAD, excluyendo cambios de formato. Los ocho módulos nuevos se miden completos.

| Alcance | Líneas | Ramas |
| --- | ---: | ---: |
| Ocho módulos nuevos | 100 % | 98,72 % |
| Todo el código nuevo/modificado semánticamente | 100 % | 97,42 % |
| Cambios semánticos de `CALCULOS_QT.py` | 100 % | 92,11 % |
| Funciones críticas de corrección y documento en `CALCULOS_QT.py` | 100 % | 100 % |
| Módulo compartido de consistencia | 100 % | 100 % |
| Diálogo de autorización | 100 % | 100 % |

Las funciones críticas medidas son `_receipt_metadata_policy`, `_snapshot_corrected_receipt`, `update_receipt_authorization` y `update_monthly_batch_receipt_export_data`: 49 líneas ejecutables y 16 ramas, todas cubiertas.

Evidencia: `output/workspace-final-coverage.json`, `output/workspace-ui-final-coverage.json`, `output/workspace-coverage-summary.json` y `output/workspace-critical-coverage.json`. El cálculo de alcance está en `output/workspace-coverage-scopes.py`.

## CALIDAD

- Formatter: PASS para módulos/pruebas nuevos y funciones modificadas de tamaño acotado. `ruff format --check`; `git diff --check` sin errores.
- Lint: PASS, Ruff sin errores en archivos nuevos; comparación del código heredado contra HEAD sin errores nuevos.
- Type checker: PASS, mypy sin errores en los ocho módulos nuevos; comparación del código heredado sin errores de tipos nuevos.
- Análisis estático: PASS, Ruff/mypy y Bandit. Bandit no reportó hallazgos en los módulos nuevos.
- Duplicación: PASS, Pylint `duplicate-code` con mínimo de seis líneas de similitud no reportó nuevos bloques duplicados entre los módulos nuevos. No se afirma un porcentaje global de duplicación del código heredado.
- Complejidad nueva: PASS, máximo ciclomático real de 10, medido por Radon; valores por módulo en `output/workspace-complexity.json`.

### Excepciones documentadas de código heredado

No se aplicó formato masivo a seis funciones grandes: `save_receipt_with_items`, los constructores de Listados e Historial, `_build_ui`, `_apply_display_layout` y `load_recibo_for_editing`. Se preservó su estructura para evitar una refactorización fuera del alcance. Los bloques nuevos pequeños están formateados y separados en los módulos nuevos.

El archivo principal ya contiene funciones con complejidad superior a 10. Las adiciones necesarias para sincronizar después del guardado y delegar controles aumentaron algunas: `save_receipt_with_items` 183→185, filtro SQL 27→28, llenado mensual 19→20, estado de acciones 34→35, menú 20→21, fila 20→21, métricas 25→26 y carga de recibo 38→39. `_fill_list` y `_prepare_data` pasan de 10 a 11. La disposición principal se mantiene en 93. Estas son excepciones explícitas del código heredado: extraer toda su orquestación transaccional/UI requeriría una refactorización distinta a este cambio. No se añadieron funciones nuevas de complejidad superior a 10.

Evidencia: `output/workspace-static-summary.json`, logs `workspace-new-lint`, `workspace-new-types`, `workspace-format`, `workspace-duplication`, `workspace-security` y comparación contra `output/workspace-static-baseline/`. Las advertencias de deprecación de PyPDF2 y ttkbootstrap son heredadas.

## BUILD

```powershell
python -X utf8 -m PyInstaller --noconfirm --distpath output/workspace-build --workpath output/workspace-build-cache build_app.spec
```

Resultado: PASS. El build terminó correctamente en 341,584 segundos; evidencia `output/workspace-build.log`.

Se verificó que los nueve módulos principales empaquetados corresponden al código fuente final y que la plantilla y CSS del PDF son idénticos a los archivos actuales. Ejecutable local: `output/workspace-build/SIGEH/SIGEH.exe`.

## QA

### Smoke del ejecutable

Cuatro ejecuciones aprobadas, todas con código de salida 0 y directorios de datos aislados:

1. `SIGEH.exe --self-test`.
2. `CALCULOS_QT.exe --self-test-pdf`.
3. `CALCULOS_QT.exe --self-test-report-viewer`.
4. `CALCULOS_QT.exe --self-test-reports`.

Evidencia: `output/workspace-build-verification.json` y logs de `output/workspace-build-smoke/`.

### QA visual y eventos

Se abrieron y capturaron las pantallas reales Qt con datos sintéticos, temas claro/oscuro y anchos 1366/1680. Se revisaron legibilidad, navegación, botones, selección, filtros, paneles, menús, validaciones, estados enabled/disabled, actualización, cierre y reapertura. En espacios estrechos se conservan controles mediante scroll y tooltips.

Capturas: `output/workspace-visual-qa/billing-1366.png`, `billing-1680.png`, `billing-light-1366.png`, `monthly-1366.png`, `monthly-1680.png`, `history-1366.png`, `history-1680.png` y `correction-dialog.png`.

Se generaron tres PDFs reales: NSS con ceros iniciales, identificador/nombre largos y NSS ausente. Se extrajo texto para verificar identificación y total RD$ 7.737,20, y se revisaron las imágenes de todas las páginas. Los tres ejemplos tienen dos páginas; la muestra normal ya tenía dos páginas con la plantilla anterior. No se atribuye ese salto de página a la incorporación del NSS.

### Seguridad

Consultas parametrizadas; ordenamiento SQL mediante lista permitida; autorización comprobada en backend y UI; texto del paciente plano y HTML escapado; ausencia de enlaces por nombre; bloqueos de recibo→expedientes en orden consistente; rollback de recibo/listado/auditoría/documento ante fallos. No se añadieron secretos ni se suprimieron validaciones.

## QUALITY GATES

| Gate | Estado | Alcance/evidencia |
| --- | --- | --- |
| Funcionalidad | PASS | Requerimientos y pruebas anteriores. |
| Unit tests | PASS | 101 pruebas finales. |
| Regression tests | PASS | Suite completa y controles afectados. |
| Integration tests | PASS | PostgreSQL local, documento, módulos y archivos. |
| Coverage | PASS | 100 % líneas / 97,42 % ramas; crítico 100 %/100 %. |
| Boundary tests | PASS | Límites, vacíos, páginas y transiciones. |
| Formatter | PASS | Archivos nuevos/bloques acotados; excepciones heredadas documentadas. |
| Lint | PASS | Cero errores nuevos. |
| Static analysis | PASS | Ruff/mypy/Bandit. |
| Type checker | PASS | Módulos nuevos y comparación heredada. |
| Complexity | PASS | Nuevo máximo 10; excepciones heredadas documentadas. |
| Duplication | PASS | Sin hallazgos nuevos en Pylint duplicate-code. |
| Build | PASS | PyInstaller final. |
| Smoke | PASS | Cuatro ejecuciones del build final. |
| Security checks | PASS | Pruebas de permisos/inyección/rollback/conflictos y Bandit. |
| QA final | PASS | Revisión funcional, regresión, clean code y QA. |
| Prueba optativa de capacidad real | N/A | Requiere habilitación de una base real; ajena al cambio. |
| Impresión física y revisión en hospital | NO VERIFICADO | No se dispone de esa computadora/impresora en esta ejecución. |

## CUATRO PASADAS FINALES

1. Funcionalidad: revisión de cada requerimiento, datos y controles. PASS.
2. Regresiones: suite completa, acceso del auditor y conservación de operaciones existentes. PASS.
3. Clean code: módulos por responsabilidad, nombres, duplicación, errores, código muerto y complejidad; excepciones heredadas documentadas. PASS.
4. QA: pruebas, métricas, análisis, build, smoke y artefactos visuales. PASS para la entrega local.

## PROBLEMAS PENDIENTES

No quedan fallos detectados del cambio local. Persisten deuda técnica de complejidad/formato y advertencias heredadas señaladas arriba. La validación en el hospital y la impresión física no se ejecutaron. La validación visual automatizada usa Qt offscreen; no es una prueba manual de todos los monitores y escalados de Windows.
