# Vinculación administrativa de recibos a atenciones heredadas

Fecha de validación: 29 de septiembre de 2026.

## Implementación

Acceso: **Facturación → Recibos → seleccionar recibo → Más acciones → Vincular a atención heredada…**.

- `CALCULOS_QT.py`: acción exclusiva del administrador y actualización de su disponibilidad al cambiar la selección o el usuario.
- `receipt_attention_link_dialog.py`: búsqueda paginada de heredadas con el selector existente, confirmación explícita y guardado en segundo plano. Cancelar no escribe; durante el guardado no se permite cerrar ni enviar otra vez.
- `receipt_attention_link.py`: comprobación del rol almacenado, atención vigente y elegible, recibo sin vínculo, paciente y cobertura compatibles. Los identificadores conocidos no pueden contradecirse. Los recibos sin seguro pueden tener ARS vacía.
- Tres archivos de pruebas: `tests/test_receipt_attention_link.py`, `tests/test_receipt_attention_link_postgres.py` y `tests/test_receipt_attention_link_dialog.py`.

La operación bloquea la atención y el recibo, vuelve a consultar su estado y guarda el vínculo, la resolución de la herencia y la auditoría en una sola transacción. Utiliza el bloqueo compartido con el guardado normal de facturas para serializar operaciones concurrentes. Una reserva vigente de otra estación impide vincular.

Se preservan número, paciente, fecha de servicio, fecha de creación, autor original, ARS, cobertura, autorización, importes, ítems y documento del recibo. Solo cambian los metadatos de vinculación y la revisión. Se registra internamente `ADMISSION_ATTENTION_LINKED_LATER` con el administrador, el recibo y la atención.

El recibo guardado resuelve el pendiente aunque sea preliminar. No se añade una sección de vinculación a los reportes. Al conservar la fecha original, un recibo antiguo no vuelve a sumar importe como facturación del turno actual. No se modifican las instantáneas de cierres ya emitidas. No hay migración de esquema ni cambios de producción en esta tarea.

## Pruebas

- Unitarias: recibo inexistente, vacío, eliminado, anulado o ya vinculado; roles no autorizados; nombres/ARS incompatibles; cédula/NSS contradictorios; normalización de espacios y mayúsculas; cobertura sin seguro.
- Integración PostgreSQL desechable: conservación del autor original distinto del administrador que vincula, persistencia, herencia resuelta, ausencia de reserva, registros inexistentes y revisión de estados cambiados después de seleccionar.
- Concurrencia: dos solicitudes para el mismo recibo y dos recibos que intentan usar la misma atención. Solo una operación confirma y solo queda una auditoría.
- Rollback: fallo forzado al registrar la auditoría; el recibo y la herencia permanecen intactos.
- Reportes: heredada del turno anterior e histórica resueltas por un recibo antiguo; reducción de pendientes sin nuevo importe ni campos adicionales.
- Qt: acción visible solo al administrador, recibo vinculado deshabilitado, selección/cancelación, confirmación asíncrona, errores, cierre bloqueado durante la operación y actualización del historial al confirmar.

Las pruebas usan PostgreSQL temporal en loopback y datos sintéticos. No se realizó QA destructivo contra producción.

## Resultados comprobados

- Pruebas específicas finales: **54 PASS, 0 FAIL, 0 skipped** (`output/receipt-link-final-focused.xml`).
- Suite relacionada de recibos, deduplicación y consistencia: **49 PASS, 0 FAIL, 0 skipped** (`output/receipt-link-related.xml`).
- Caracterización y regresión inicial de herencia/cierres, junto con la función: **72 PASS**.
- Módulo de importación Qt, ejecutado aislado: **8 PASS** (`output/receipt-link-isolated-qt.xml`).
- Inspección de reportes y arranque, repetida con código fijo: **11 PASS** (`output/receipt-link-source-inspection.xml`).

Estos grupos se solapan y no deben sumarse como pruebas distintas. La ejecución general inicial tuvo 1.806 aprobadas, 60 subpruebas aprobadas, una omisión optativa y dos fallos de inspección de fuentes: sus funciones estaban cargadas antes de una extracción que desplazó líneas del archivo. No eran cambios de comportamiento; las 11 comprobaciones de sus módulos pasan con la fuente fija. También se descartó una ejecución combinada que abortó en el módulo de importación Qt; este módulo se verificó en su proceso separado. No se presentan esas ejecuciones como PASS.

## Cobertura

Herramienta: `coverage.py`, con `--branch`.

- Servicio crítico: **60/60 líneas, 24/24 ramas — 100 %**.
- Diálogo: **87/87 líneas, 16/16 ramas — 100 %**.
- Integración en el historial: **9/9 líneas ejecutables nuevas — 100 %**.
- Sin exclusiones nuevas para alcanzar los umbrales.
- Evidencia: `output/receipt-link-coverage-final.json`. La cobertura se refiere al código de esta funcionalidad, no a toda la aplicación.

## Calidad

- Ruff y formatter en los dos módulos y tres archivos de pruebas nuevos: PASS.
- Ruff diferencial de `CALCULOS_QT.py`: **0 errores nuevos**; conserva 327 diagnósticos heredados.
- Mypy, con `--check-untyped-defs --follow-imports=silent --ignore-missing-imports`, en ambos módulos nuevos: PASS. Comparación del módulo heredado: 969 errores preexistentes, sin errores nuevos; las referencias a números de línea se normalizan al comparar diagnósticos desplazados.
- Compilación sintáctica de los tres archivos productivos: PASS.
- Radon: complejidad máxima nueva **9**; nuevos métodos de integración **1 y 3**. `_update_action_state` conserva su complejidad heredada **34**; se extrajo la nueva condición para no incrementarla ni refactorizar el historial fuera del alcance.
- Pylint `duplicate-code`, mínimo seis líneas: sin bloques duplicados detectados en los módulos nuevos. No se afirma un porcentaje de duplicación global del repositorio.
- Bandit: **0 hallazgos** en los módulos nuevos. Revisión manual: permisos comprobados al guardar, SQL parametrizado, transacción atómica y texto plano en la confirmación.

## Build y QA

Comando de compilación:

```powershell
python -m PyInstaller --noconfirm --log-level WARN --distpath output/receipt-link-app --workpath build/receipt-link-app build_app.spec
```

Build: **PASS**. Se comprobó que los dos módulos nuevos y `CALCULOS_QT` dentro del ejecutable coinciden con el código fuente mediante comparación de bytecode, nombres y constantes. Las tres pruebas de humo `--self-test-pdf`, `--self-test-report-viewer` y `--self-test-reports` devolvieron **0 (PASS)**, sin conectarse a producción. Evidencia: `output/receipt-link-build-final.log`, `output/receipt-link-package-modules.json` y `output/receipt-link-smoke.json`.

QA visual: captura del menú y confirmación con datos sintéticos; selección, textos completos y botones legibles en tema oscuro. Evidencia en `output/receipt-link-gui/`. La carga de Segoe UI se hizo en el proceso de captura, porque el motor Qt offscreen no descubría por sí solo la fuente del sistema.

La versión publicada no se ha modificado. No se cambió el instalador ni se publicó este paquete de pruebas.

## Consolidación final de regresión

Los nueve grupos originales terminaron con código de salida 0: **1.810 pruebas pasadas, 60 subpruebas pasadas, 0 fallidas y 1 omitida**. El módulo Qt que requiere proceso aislado agregó **8 pasadas**: total **1.818 pasadas**. La omitida corresponde al dry-run de capacidad real que necesita habilitación explícita; no se ejecutó contra producción. Evidencia: `output/receipt-link-regression-groups.json`, XML/logs de los nueve grupos y `output/receipt-link-isolated-qt.xml`.

Gates del cambio: funcionalidad, unitarias, integración, regresión, cobertura del código modificado, bordes, formatter, cero diagnósticos nuevos de lint/tipos, análisis estático, complejidad nueva, detección de duplicación, build, smoke, seguridad y QA de laboratorio: **PASS**. Auditoría de capacidad real no relacionada: **N/A**. Publicación e instalación en el hospital no forman parte de esta validación local.

**ESTADO FINAL: APROBADO PARA ENTREGA** del código validado; no equivale a una release publicada.
