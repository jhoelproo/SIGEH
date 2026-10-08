# QA de SIGEH 1.2.12 — 8 de octubre de 2026

**ESTADO FINAL: APROBADO PARA ENTREGA** del paquete local de prueba. Esta aprobación no es una publicación ni una validación operativa de producción.

## IMPLEMENTACIÓN

- **Pantallas pequeñas:** el perfil se recalcula con el tamaño real de la ventana incluso al redimensionar dentro del mismo monitor; navegación y formulario compactos, catálogo con anchura mínima útil, pie de acciones accesible y pestañas Catálogo/Recibo por debajo de 1180 píxeles lógicos. El historial conserva ocho filas completas en la geometría usada para un escritorio de 1280×768. Los filtros avanzados y resúmenes permanecen disponibles; los permisos de los auxiliares se mantienen al cambiar tamaño o sesión.
- **Búsquedas y foco:** borrar mediante la X y escribir después de usar filtros funciona. Limpiar filtros devuelve el foco al buscador. El listado permite búsqueda por recibo. Admisión busca por fecha calendario, «Ayer» y nombre/documentos; las solicitudes manuales consultan el historial central y sobreviven a eventos automáticos, respetando la pausa por restricción. La actualización automática sigue usando la caché.
- **Especialidades:** el selector ofrece EMERGENCIOLOGÍA, GINECOLOGÍA y PEDIATRÍA. GENERAL se guarda como EMERGENCIOLOGÍA; el autocompletado admite prefijos, acentos y errores cercanos. Se conservan valores históricos desconocidos al editar documentos existentes.
- **Correcciones del listado:** nombre, fecha de servicio, autorización, especialidad e identificación se propagan al recibo, Admisión vinculada y listados pendientes mediante transacciones auditadas. RENACER admite NO. PÓLIZA; HUMANO y PRIMERA NO. AFILIADO; SEMMA NO. CARNET. Los identificadores alternativos conservan ceros y no reemplazan el NSS/cédula originales. Los listados enviados permanecen inmutables.
- **Turnos y reingresos:** intervalos clínicos delimitados impiden tratar una atención del 4 de octubre como duplicada en el turno del 6 de octubre. Reimprimir, abrir, editar y registrar reingreso funcionan en los casos autorizados. La identidad y motivo del reingreso sobreviven al cierre y sincronización. «Este turno» muestra primero las atenciones recientes.
- **Estado y edición de recibos:** FACTURADO genera documento FINAL, incluso sin vínculo. Una instantánea preliminar antigua se sustituye por una nueva versión sin eliminar su historia. Se permite corregir el recibo propio pendiente cuando la preparación de Admisión cambió, conservando controles de identidad, propiedad, permisos, anulación y reclamaciones.
- **Vinculación:** se abre el historial completo de Admisión; se busca por nombre, NSS o cédula y se excluyen atenciones vinculadas/facturadas. El vínculo queda auditado y protegido contra carreras y origen equivocado.
- Se retiró la acción «Actualizar base de datos de Admisión» del menú. La inicialización de esquemas sigue disponible internamente. Al ajustar el tamaño se descuenta el marco de Windows antes de redimensionar el área cliente; repetir el ajuste ya no aumenta la altura.

Se preservaron las operaciones del catálogo, cantidades, favoritos, eliminación, totales, paginación, edición y numeración. La continuidad local anterior permanece: cola durable, UUID idempotente, catálogo persistente, sincronización y pausa por restricciones, respaldos verificados y restauración. La facturación offline conserva la restricción de rol y requiere tarifas previamente guardadas.

Archivos por área: `display_layout.py`, `billing_responsive.py`, `billing_workspace_design.py`, `billing_items_design.py` y `receipt_history_responsive.py` para tamaño; `CALCULOS_QT.py`, `billing_specialties.py`, `monthly_receipt_fields.py`, `receipt_patient_correction.py`, `receipt_list_consistency.py` y `admission_demographics.py` para correcciones; `admission_visit_dates.py`, `admission_reentry.py`, `admission_hybrid.py`, `admission_v15_adapter.py` y V15 para Admisión; `receipt_document_state.py`, `receipt_documents.py`, `billing_admission_edit.py`, `receipt_attention_link.py`, su diálogo y `pdf_engine/renderer.py` para documentos/vinculación. El inventario completo está al final.

## PRUEBAS

Se ejecutaron pruebas específicas, regresiones de módulos, suites relacionadas y toda la colección aplicable en procesos aislados. Incluyen:

- Unitarias: normalización, estado documental, intervalos, permisos, importes, foco, búsqueda y controles responsive.
- Integración: SQLite temporal real; bases PostgreSQL locales desechables; bloqueo, INSERT/SELECT/UPDATE/DELETE, duplicados, concurrencia, auditoría y rollback; versiones documentales e identificadores; sincronización y reapertura.
- GUI: ventanas Qt reales en modo offscreen, señales, X, escritura, botones, permisos, cierre, redimensionamiento, filtros y persistencia. La prueba interactiva usa datos ficticios locales.
- Bordes: intervalo de turno semiabierto, cambio de día y formatos de fecha; motivo de reingreso de 7/8 caracteres, estados inválidos, origen ausente/equivocado, UUID inválido, identidad/paciente/día distintos; números largos con ceros; datos vacíos; filtros y tablas sin resultados; PDF binario/estructurado ausente o renderer fallido.
- Continuidad: guardar atención/recibo offline, cierre/reapertura, sincronización sin duplicar incluso tras perder la respuesta después del commit; bloqueo de consultas repetidas por restricción; restauración de respaldos y rechazo de copias corruptas.

Los bugs se reprodujeron antes de corregirlos: recibos/foco (10 fallos RED), fechas/turnos (5), reingreso (2), calendario (4), carrera de búsqueda manual (2), identificadores (7) y permisos del resumen (3). El viewport produjo nueve fallos RED antes de la corrección, incluidos los cambios de tamaño sin nueva señal de layout. El ajuste de marco/cliente produjo tres fallos unitarios RED; también se reprodujo el crecimiento de altura en la ventana completa antes de corregirlo. Las ejecuciones GREEN y regresiones están en los XML de `output/oct08-*`. Las tres últimas pruebas de borde fueron añadidas para completar cobertura, sin cambiar la aplicación.

## RESULTADOS

| Resultado JUnit consolidado | Cantidad |
| --- | ---: |
| Casos registrados, incluyendo subtests | 2901 |
| PASS | 2900 |
| FAIL | 0 |
| ERROR | 0 |
| SKIP | 1 |
| Módulos / procesos | 221 / 49 |

[Resumen JUnit](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-junit-summary.json) y [Manifiesto de módulos y procesos](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-verified-groups.json). Se comprobó que todos los módulos recogidos por pytest aparecen exactamente una vez en la validación final. Tras corregir el viewport se repitió la colección completa con las fuentes actuales; el manifiesto incluye su fingerprint.

La omisión es `test_real_capacity_dry_run_preserves_operational_counts`, que exige `RUN_REAL_CAPACITY_INTEGRATION=1`. Es una comprobación opcional de capacidad real, ajena a estos cambios; no se habilitó QA contra producción.

La primera pasada tuvo cuatro errores de conexión y 26 casos omitidos por PostgreSQL local apagado: se inició el servidor y todos los grupos afectados pasaron al repetirlos. Un grupo Qt completaba sus 67 aserciones pero fallaba al liberar el heap nativo; se reprodujo dos veces y se aisló cada uno de sus cinco módulos. Todos terminaron con exit 0. Se conservan logs/XML iniciales; no se omitieron sus casos ni se ignoró el código de salida. La ejecución inicial del analizador de seguridad en el proceso aislado no encontró Bandit en el user-site; se repitió el comando en el runtime normal y terminó con exit 0. El fallo original se conserva en `output/oct08-initial-viewport-pipeline.json`. Los avisos restantes son deprecaciones heredadas de PyPDF2/ttkbootstrap y módulos no importados dentro de procesos individuales de cobertura.

## COBERTURA

Coverage.py real con ramas; combinación de los 49 procesos. Los módulos nuevos se miden completos. En archivos heredados se miden sentencias semánticamente modificadas respecto de `output/oct08-baseline`; se excluye el mero cambio de formato. Estos porcentajes **no representan toda la aplicación heredada**.

| Ámbito | Lines | Branches |
| --- | ---: | ---: |
| Seis módulos nuevos | 274/274 — 100.0% | 76/76 — 100.0% |
| Código nuevo/modificado | 713/714 — 99.86% | 177/178 — 99.44% |
| Lógica crítica modificada | 503/504 — 99.8% | 137/138 — 99.28% |

Cada archivo cumple ≥90% de líneas/≥85% de ramas; la lógica crítica supera 95%/90%. No se redujeron umbrales. La única línea/rama modificada sin ejecutar es el rechazo de rol sin permiso de facturación de pacientes sin seguro en `CALCULOS_QT.py:11201`; la política conserva la protección existente. Los cinco módulos preservados de continuidad local alcanzan 100%/100% en esta suite; el alcance de los cambios previos de respaldo está documentado en [QA de continuidad](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/docs/QA_CONTINUIDAD_LOCAL_20261006.md).

[Cobertura completa medida](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-coverage.json) · [Cobertura del cambio y faltantes](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-coverage-summary.json).

## CALIDAD

- Formatter: PASS. Ruff aplicado a módulos/pruebas nuevos y rangos modificados; igualdad AST comprobada en archivos heredados. Los tres tests suplementarios pasaron `ruff format --check`.
- Lint: PASS, cero errores nuevos en comparación con baseline; módulos y pruebas nuevos sin infracciones. `git diff --check`: PASS (solo avisos Git sobre conversión LF/CRLF).
- Type checker: PASS para módulos nuevos y cero errores nuevos de mypy en código heredado. No se afirma que el repositorio heredado completo carezca de errores.
- Análisis estático/seguridad: PASS del cambio mediante Ruff, mypy y Bandit; cero hallazgos nuevos. Bandit conserva 323 observaciones heredadas en el ámbito amplio analizado; no se suprimieron para obtener PASS.
- Complejidad: máximo 10 en funciones de módulos nuevos; helpers nuevos ≤10. Funciones heredadas no aumentan: `_make_snapshot` conserva 11 y `_ensure_window_visible` 8; guardado de recibo 189→189, historial V15 24→24, ctor historial de Admisión 12→12. Excepción técnica explícita: se mantuvieron orquestadores heredados para evitar una refactorización masiva; la nueva lógica se extrajo en helpers.
- Duplicación: Pylint `duplicate-code` registró 0 bloques nuevos y 1 heredado. PASS del cambio. Porcentaje de duplicación global ≤3%: NO VERIFICADO, recomendación global sin herramienta porcentual configurada; no se inventa una medida.

[Métricas de calidad y comparación](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-quality.json) · [Formato y AST](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-format-ranges.json) · [Lint de tests](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-test-lint.json) · [Calidad suplementaria](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-boundary-test-quality.json).

## BUILD

PASS, comando ejecutado:

```powershell
python -m PyInstaller --noconfirm --distpath output/oct08-app --workpath output/oct08-build build_app.spec
```

El updater también compiló con PyInstaller. Se compararon bytecode/constantes/nombres del código empaquetado con las fuentes actuales, incluidos V15 y los módulos preservados de continuidad, y hashes de HTML/CSS/metadatos: PASS. El ejecutable se reconstruyó después de la corrección final de display_layout.py.

[Log del build](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-final-build.log) · [Build updater](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-updater-build.log) · [Verificación del código empaquetado](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-package-verification.json).

## QA

Cuatro smoke tests del ejecutable Windows pasaron con perfil temporal aislado y backend inaccesible de prueba: `SIGEH.exe --self-test`, generación de recibo, visor de reportes y generación de reportes. [Resultados smoke](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-smoke.json).

La QA gráfica con Segoe UI verificó las geometrías pequeñas y el redimensionamiento normal en las tres pestañas reales. La matriz adicional prueba un monitor de 1280×720, límites del marco, un monitor de 1920×1080 y restauración de la ventana amplia. Para escritorio 1280×768 se reservó espacio equivalente a marco/barra de tareas. Con las tres pestañas principales reales y el debounce normal, catálogo: cinco ítems completos visibles en 1280×680; historial: ocho filas completas, pie de acciones y paginación accesibles. Por debajo de 1180 píxeles lógicos el catálogo dispone de su pestaña ancha. Filtros avanzados conservan al menos cuatro filas en el estado probado. La primera fixture gráfica ocultaba dos pestañas y resultó demasiado favorable: se descartó como prueba del flujo completo y se verificó la ventana real con las tres pestañas. No se realizó control nativo de una pantalla física mediante CUA; la comprobación utilizó Qt/QTest y capturas renderizadas, no una maqueta.

Capturas inspeccionadas: [Facturación 1280 nativa, tres módulos](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-responsive/interactive-billing.png) · [Historial 1280 nativo](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-responsive/interactive-history.png) · [Catálogo 1024](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-responsive/billing-after-1024.png) · [Filtros avanzados](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-responsive/history-advanced-1200.png) · [Monitor 1280×720, tres módulos](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-responsive/billing-three-modules-monitor-1280.png) · [Métricas y regresión de la ventana completa](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-mainwindow-viewport-report.md).

PDF: renderer real, Playwright y extracción PyPDF2, render con Poppler e inspección visual. FACTURADO no vinculado genera final; recibo local pendiente conserva preliminar. Póliza de 22 dígitos con ceros iniciales se conserva y ambos documentos tienen una página sin solapamiento. [Log PDF](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-pdf-qa.log).

Seguridad del ámbito: SQL parametrizado, origen/UUID validados, permisos preservados, carreras y rollback probados, contenido escapado para HTML. El paquete público excluye credenciales/backend y bases operativas; requiere provisionamiento externo ya existente. No se hizo un pentest externo ni QA destructivo de producción.

Paquete: `1818` archivos comprobados por SHA-256, CRC del ZIP y guardas de distribución PASS. SHA-256:

```text
f825daeecebcc4406c2cb206200f54992226dd357a647cac4920bb1c4c264710
```

[Paquete de prueba 1.2.12](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-release/SIGEH-1.2.12-windows-x64.zip) · [Integridad del ZIP](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct08-archive-qa.json).

Cuatro pasadas finales: funcionalidad PASS requerimiento por requerimiento; regresiones PASS según manifiesto; clean code PASS con excepciones heredadas documentadas; QA PASS con tests/cobertura/calidad/build/smoke/inspección gráfica.

## QUALITY GATES

| Gate obligatorio aplicable | Estado |
| --- | --- |
| Funcionalidad | PASS |
| Unit tests | PASS |
| Regression tests | PASS |
| Integration tests | PASS |
| Coverage | PASS |
| Boundary tests | PASS |
| Formatter | PASS |
| Lint — cero errores nuevos | PASS |
| Static analysis — cero errores nuevos | PASS |
| Type checker — cero errores nuevos | PASS |
| Complexity del cambio | PASS |
| Duplication nueva | PASS |
| Build | PASS |
| Smoke del ejecutable | PASS |
| Security checks del ámbito | PASS |
| QA final local | PASS |

N/A: integración opcional de capacidad real, no necesaria para los cambios y no habilitada. NO VERIFICADO: porcentaje global recomendado de duplicación; disponibilidad del servicio remoto y aceptación operativa en equipos reales del hospital.

## PROBLEMAS PENDIENTES

No hay fallos funcionales abiertos en el ámbito probado. Quedan aceptación del hospital sobre la versión local y despliegue/migraciones de producción; no se ejecutaron. La migración de columnas de identificación es idempotente y se probó localmente. Las mejoras locales reducen solicitudes repetidas, pero no constituyen garantía de consumo remoto o coste cero. Los orquestadores grandes y hallazgos heredados descritos permanecen fuera de esta corrección.

La prueba interactiva usa `sigeh_oct08_preview` en un servidor PostgreSQL propio 127.0.0.1:55434 y un perfil temporal independiente, con datos ficticios y tarifas DEMO. El primer arranque compartía el servidor de QA y sufrió el cierre de una conexión; se relanzó con un cluster distinto para evitar interferencia de las pruebas de configuración. El marcador de arranque confirma la apertura del historial y los screenshots nativos muestran los recibos. Ejecuta la UI desde fuentes verificadas como idénticas al código del paquete; no se presenta esa sesión como ejecución del binario congelado. Los smoke tests del binario se ejecutaron por separado.

## INVENTARIO DEL CAMBIO ACTUAL

Base: snapshot previo a estas correcciones, preservando el trabajo de continuidad anterior. HTML/CSS, metadatos de versión y migraciones se describen arriba y en el informe de continuidad.

- [admission_demographics.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_demographics.py)
- [admission_hybrid.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_hybrid.py)
- [admission_v15_adapter.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_v15_adapter.py)
- [billing_admission_edit.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_admission_edit.py)
- [billing_items_design.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_items_design.py)
- [billing_specialties.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_specialties.py)
- [billing_workspace_design.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_workspace_design.py)
- [CALCULOS_QT.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/CALCULOS_QT.py)
- [display_layout.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/display_layout.py)
- [receipt_attention_link.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_attention_link.py)
- [receipt_attention_link_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_attention_link_dialog.py)
- [receipt_documents.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_documents.py)
- [receipt_history_design.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_history_design.py)
- [receipt_history_focus.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_history_focus.py)
- [receipt_list_consistency.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_list_consistency.py)
- [receipt_patient_correction.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_patient_correction.py)
- [sigeh_product.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/sigeh_product.py)
- [ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py)
- [pdf_engine/renderer.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/pdf_engine/renderer.py)
- [tests/test_admission_v15_unified_history.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_v15_unified_history.py)
- [tests/test_billing_items_presentation.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_billing_items_presentation.py)
- [tests/test_billing_specialties.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_billing_specialties.py)
- [tests/test_emergency_history_newest_first_v104.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_emergency_history_newest_first_v104.py)
- [tests/test_inherited_receipt_save.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_inherited_receipt_save.py)
- [tests/test_monthly_patient_name_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_patient_name_postgres.py)
- [tests/test_monthly_receipt_corrections.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_receipt_corrections.py)
- [tests/test_receipt_attention_link_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_attention_link_dialog.py)
- [tests/test_receipt_attention_link_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_attention_link_postgres.py)
- [tests/test_receipt_edit_guards_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_edit_guards_postgres.py)
- [tests/test_recent_demographics_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_recent_demographics_postgres.py)
- [tests/test_sigeh_update.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_sigeh_update.py)
- [tests/test_workspace_design.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_workspace_design.py)
- [admission_reentry.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_reentry.py)
- [admission_visit_dates.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_visit_dates.py)
- [billing_responsive.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_responsive.py)
- [monthly_receipt_fields.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/monthly_receipt_fields.py)
- [receipt_document_state.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_document_state.py)
- [receipt_history_responsive.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_history_responsive.py)
- [tests/test_admission_history_manual_search_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_history_manual_search_oct08.py)
- [tests/test_admission_oct08_corrections.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_oct08_corrections.py)
- [tests/test_admission_reentry_boundaries_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_reentry_boundaries_oct08.py)
- [tests/test_admission_reentry_sync_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_reentry_sync_oct08.py)
- [tests/test_admission_visit_dates.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_visit_dates.py)
- [tests/test_display_viewport_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_display_viewport_oct08.py)
- [tests/test_mainwindow_viewport_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_mainwindow_viewport_oct08.py)
- [tests/test_monthly_metadata_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_metadata_dialog.py)
- [tests/test_monthly_metadata_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_metadata_postgres.py)
- [tests/test_monthly_receipt_fields.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_receipt_fields.py)
- [tests/test_oct08_responsive.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct08_responsive.py)
- [tests/test_owned_identifier_refresh_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_owned_identifier_refresh_oct08.py)
- [tests/test_receipt_document_state_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_document_state_oct08.py)
- [tests/test_receipt_flow_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_flow_oct08.py)
- [tests/test_receipt_flow_oct08_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_flow_oct08_postgres.py)
- [tests/test_receipt_history_summary_permissions.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_history_summary_permissions.py)
- [tests/test_receipt_legacy_fallback_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_legacy_fallback_oct08.py)
- [tests/test_receipt_link_boundaries_oct08.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_link_boundaries_oct08.py)
- [tests/test_receipt_metadata_correction.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_metadata_correction.py)
