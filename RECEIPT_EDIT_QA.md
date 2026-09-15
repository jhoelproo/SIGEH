# Corrección de edición de recibos — 14/09/2026

## Implementación

- `CALCULOS_QT.py`: el administrador puede corregir nombre, fecha de servicio, diagnóstico y sala durante una edición editable. Seguro y cobertura quedan bloqueados. Se conserva la condición de consulta para documentos cerrados, que requieren reapertura según el flujo existente.
- `billing_field_policy.py`: reglas comunes para habilitar campos; los estados de solo lectura siguen protegidos.
- `receipt_edit_integrity.py`: fechas admitidas ISO, día/mes/año y día-mes-año, normalizadas a ISO; bloqueo de cambios de seguro/cobertura; separación entre correcciones del encabezado y comprobación de identidad de Admisión.
- `receipt_documents.py`: el autor visible proviene del creador persistido, no del editor enviado en el contexto documental.
- La auditoría conserva al editor y registra nombre/fecha anteriores y nuevos. El `username`, `created_at` y origen originales no se sustituyen.
- El indicador visual siempre identifica una edición, con autorización corta o larga. Las reglas de revisión de autorizaciones de recibos realmente creados mediante bypass se mantienen.
- Al cargar una ARS histórica ausente del catálogo visible se conserva esa ARS; ya no se selecciona silenciosamente la primera.
- Pruebas nuevas y ampliadas en `tests/test_receipt_edit_integrity.py`, `tests/test_receipt_edit_postgres.py`, `tests/test_billing_field_policy_qt.py`, `tests/test_billing_edit_failure_ui.py` y `tests/test_receipt_edit_guards_postgres.py`.

El trabajo está en `codex/receipt-edit-integrity`, separado de la preparación de extranjeros. La versión del producto y sus metadatos se actualizaron a 1.1.12. No se modificaron datos productivos.

## Pruebas

Unitarias: permisos, fechas válidas/incorrectas, origen y autor de documentos, autorización vacía y longitudes 1, 2, 3, 4, 5 y 7.

Integración PostgreSQL: ediciones consecutivas con conservación de creador y fecha de generación; corrección de fecha de servicio; auditoría del editor; bloqueo de cambio de seguro sin modificación parcial. Prueba adicional de recibo vinculado: el administrador corrige nombre/fecha del recibo sin cambiar la atención de Admisión.

GUI Qt: campos habilitados/bloqueados; lectura de fechas históricas; conservación de ARS histórica; indicador de edición consistente; borrador preservado ante errores de conexión, anulación o diferencias con Admisión.

Regresión ampliada: todos los archivos de pruebas de Facturación/Recibos seleccionados mediante `rg --files tests`, incluyendo exclusión de anuladas, claims ajenos, duplicados, concurrencia, UUID opcionales, snapshots y reaperturas.

## Resultados

- Suite completa final 1.1.12: **1.538 PASS, 0 FAIL, 1 SKIPPED; 60 subtests PASS**, 326,31 segundos. Evidencia: `output/release-1112-regression-final.xml`. Se omitió únicamente la prueba optativa de capacidad contra PostgreSQL real (`RUN_REAL_CAPACITY_INTEGRATION`), ajena al cambio; no se activó sobre producción. Dos advertencias de deprecación existentes: PyPDF2 y ttkbootstrap.tooltip.
- Pasada ampliada final: **474 PASS, 0 FAIL, 0 SKIPPED**. Evidencia: `output/receipt-regression-final.xml`.
- Posteriormente, al añadir dos casos de indicador GUI: módulo Qt completo **13 PASS**.
- Posteriormente, al añadir la corrección de encabezado vinculado: módulo PostgreSQL de guardas completo **9 PASS**.
- Esas dos pasadas incluyen pruebas repetidas; no se suman como si fueran todas casos distintos.
- Preparación 1.1.12: 26 pruebas de actualización/lanzador/empaquetado PASS; 32 pruebas de extensiones de validación y versión PASS, con 3 subtests PASS. Se actualizaron las expectativas del número de versión y el recibo simulado para incluir los campos originales que registra la auditoría.
- Advertencia existente: deprecación de PyPDF2. No se ocultó.
- La primera pasada amplia detectó tres aserciones sobre identidad del objeto enviado al validador. Se actualizaron para comprobar la copia de validación y que el borrador original conserva sus datos. La pasada final aprobó.
- PostgreSQL se ejecutó en un clúster temporal propio, limitado a loopback. Fue detenido y eliminado al terminar; nunca se usó la base del hospital para QA.

## Cobertura

Coverage.py con `--branch` y datos de las suites ejecutadas. Los módulos `receipt_edit_integrity.py` y `billing_field_policy.py` alcanzan 100% de líneas y ramas.

El análisis del diff, incluyendo la sentencia contenedora cuando una expresión ocupa varias líneas, registra **45 sentencias ejecutadas, 0 pendientes; 14 ramas ejecutadas, 0 pendientes**: 100% para el código cambiado medido. No representa cobertura de todo `CALCULOS_QT.py`.

Evidencia: `output/receipt-edit-coverage.json` y `output/receipt-diff-coverage.json`.

## Calidad

- Ruff: PASS en módulos pequeños y pruebas modificadas.
- Ruff sobre los archivos heredados grandes: 0 hallazgos nuevos frente a HEAD; se normalizaron los números de línea contenidos en mensajes para evitar falsos positivos por desplazamiento. Evidencia: `output/receipt-lint-delta.json`.
- Formatter: PASS en los módulos pequeños y pruebas modificadas; no se reformateó masivamente el sistema heredado.
- Las dos pruebas heredadas adicionales se formatearon verificando que su AST permanece idéntico: PASS. Ruff lint y format de ambas: PASS.
- Mypy: PASS en los dos módulos de reglas. No se atribuye un chequeo de tipos completo a la aplicación heredada.
- Radon: máxima complejidad 7 en las funciones de reglas nuevas/modificadas.
- jscpd: 0 clones / 0% duplicación en los dos módulos de reglas.
- Compilación Python: PASS para `CALCULOS_QT.py`, `receipt_documents.py` y ambos módulos de reglas.
- Seguridad: SQL parametrizado, protección de seguro también en persistencia, controles de anulación/claim/duplicidad conservados y pruebas exclusivamente sintéticas.

## Build

Comando final: `python -m PyInstaller --noconfirm --distpath output/receipt-final-build --workpath build/receipt-final build_app.spec`.

Se configuró `SIGEH_SUMATRA_PDF` con el recurso existente del checkout de release; no se descargó ni sustituyó. El primer intento sin ese recurso falló explícitamente y no se consideró aprobado.

Build final: **PASS**. Resultado local: `output/receipt-final-build/SIGEH`.

La recompilación con versión 1.1.12 también terminó en PASS. Actualizador: `python -m PyInstaller --noconfirm --distpath output/receipt-updater --workpath build/receipt-updater build_updater.spec`, PASS.

Empaquetado mediante `release_packaging.prepare_release`: `output/release-1.1.12/SIGEH-1.1.12-windows-x64.zip`. CRC y hashes individuales de 1.822 archivos verificados tras extraer. Metadatos externo e interno: 1.1.12. Validación de ausencia de bases operativas, credenciales y archivos runtime: PASS.

SHA-256: `d994eeaff28c55a6a60a33bbd5e2eea429cb3c8a548f71099d2c618a555e58e9`.

## QA

El ejecutable `CALCULOS_QT.exe --self-test-pdf` generó un PDF de prueba sin conexión a la base. Se comprobó firma PDF, apertura con QtPdf y una página renderizable: PASS. Captura revisada en `output/receipt-build-smoke.png`; el identificador sintético largo SELF-TEST se recorta en la plantilla existente, sin cambios de maquetación en esta corrección.

Se revisaron funcionalidad, regresiones, código y evidencia de QA. No se ejecutó una impresión física ni una modificación sobre producción.

Smoke adicional del ZIP 1.1.12 extraído: `SIGEH.exe --self-test`, `CALCULOS_QT.exe --self-test-pdf` y `CALCULOS_QT.exe --self-test-reports`, todos con exit 0. La configuración usada fue sintética, limitada a loopback y sin conexión al hospital.

## Quality gates

| Gate | Estado |
|---|---|
| Edición, autoría, seguro y coherencia visual | PASS |
| Unitarias / integración / límites / regresión | PASS |
| Cobertura del código modificado medido | PASS |
| Formatter / lint sin errores nuevos / análisis estático | PASS en el alcance indicado |
| Tipos de módulos de reglas | PASS |
| Complejidad / duplicación de módulos de reglas | PASS |
| Build final / smoke PDF del ejecutable | PASS |
| Seguridad de las modificaciones | PASS |
| Impresión física | N/A: esta tarea no modifica el envío a impresora |
| Causa del caso histórico de fecha incorrecta | N/A: excluida de la entrega por instrucción del usuario |
| Corrección de ese registro productivo | N/A: se lanzará sin resolver ese caso |

## Problemas pendientes

El usuario indicó que la fecha de servicio correcta era **13/09/2026**, pero no confirmó el número del recibo. Por instrucción expresa, la 1.1.12 se publica sin corregir ese registro. La normalización implementada evita fechas inválidas y sustituciones silenciosas al cargar; no modifica automáticamente fechas históricas.

El usuario autorizó publicar la siguiente versión y excluyó explícitamente el caso individual de fecha. El paquete 1.1.12 pasó las comprobaciones técnicas y está aprobado para publicación.

ESTADO FINAL: APROBADO PARA ENTREGA
