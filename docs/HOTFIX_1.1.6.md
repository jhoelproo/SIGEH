# Hotfix 1.1.6 — especialidad, identidad del Historial e impresión

## Implementación

- `admission_specialty.py`: una resolución compartida de especialidad desde campos explícitos o el payload durable. «GENERADA» no se interpreta como una especialidad. Sin datos recuperables no se inventa una clasificación.
- `admission_hybrid.py`: las tres rutas de escritura de proyección aceptan `detail_sheet`, emitido por V15, además de `specialty`. Hidratación y reconciliación conservan la especialidad, evitando sustituirla por el marcador «GENERADA».
- `admission_v15_adapter.py`: el Historial local «Todos» conserva el UUID consultado por ID en la misma réplica SQLite. Consultas centrales y totales usan la especialidad recuperada; se elimina el fallback SQL que convertía la ausencia en General.
- `admission_statistical_reports.py`: tarjetas, filtros, PDF y Excel reciben la misma especialidad normalizada, incluyendo registros anteriores con especialidad en `latest_payload_json`.
- `excel_printing.py` y V15: la impresión automática de Windows utiliza una instancia privada de Excel, abre en solo lectura e imprime la primera hoja del listado con las copias solicitadas. Cierra libro/instancia y libera COM incluso si falla. Se reemplaza el verbo de shell «print», dependiente de la asociación `.xlsx`. No se toca una sesión Excel abierta por el usuario.
- `requirements.txt` y `build_app.spec`: empaquetado de los módulos nuevos y pywin32 para Windows. Versión de hotfix 1.1.6.
- `turn_excel_delivery.py`: solicitud durable por `transition_id` antes de reiniciar el listado. La impresión se ejecuta en el trabajo de fondo y no depende del callback GUI que `shutdown` descarta. Al volver a abrir, las solicitudes PENDING reconstruyen el turno saliente exacto. Los envíos SUBMITTING no se repiten automáticamente: se muestran para revisar la cola e impedir copias duplicadas ante un resultado incierto. No hay transacciones SQLite abiertas durante generación/impresión y la transición central nunca se repite.
- Solicitar impresión obliga a generar el Excel aunque esté desactivada la preferencia independiente de guardar copia. El interruptor global de impresión sigue respetándose.

## Evidencia y alcance

Las capturas demuestran «SIN ESPECIALIDAD» en los resultados, Pediatría visible en Historial y rechazo por identidad ausente. No demuestran por sí solas el contenido central ni la causa física del problema de impresión.

Defectos reproducidos con datos sintéticos: consulta local sin UUID y proyección que no leía la especialidad de `detail_sheet`. Regresiones incluyen UUID de atención sincronizada, herencia de especialidad en hidratación, proyección bulk, conteo de tres especialidades y XLSX reabierto con los tres valores correctos.

No se ejecutaron consultas ni modificaciones productivas. Para registros que carezcan tanto de especialidad explícita como de información durable, se requiere diagnóstico: no se asigna especialidad a partir del nombre del paciente.

El usuario confirmó que se omite al cambiar el turno y luego se reinicia. Se reprodujo la pérdida del callback GUI al cerrar; la nueva prueba descarta deliberadamente ese callback y comprueba el envío en segundo plano. También se probó reapertura y recuperación del contexto del turno original, y supresión de duplicados tras envío incierto.

Impresión física: NO VERIFICADO. Este equipo no tiene `Excel.Application` registrado. Se probaron envío/cantidad/primera hoja y limpieza ante fallos de apertura, impresión, cierre e inicio mediante simulaciones. La ruta requiere Excel instalado e impresora configurada. La reproducción del defecto de ciclo de vida no demuestra por sí sola todos los factores del incidente hospitalario.

## Pruebas / calidad

- Focales iniciales: 14 PASS. Suite relacionada inicial: 128 PASS. Relevo/impresión/reinicio: 78 PASS; prueba adicional de reapertura incluida en 7 PASS del módulo de entrega. Las ejecuciones se solapan.
- Cobertura real mediante coverage con ramas: 84/84 líneas y 22/22 ramas (100 %), tres módulos nuevos.
- Ruff: PASS en módulos/pruebas nuevos; sin nuevos diagnósticos en los cuatro módulos existentes modificados.
- Mypy: PASS en los módulos nuevos. Radon: máximo 7 en especialidad, 3 en impresión y 6 en entrega durable.
- Integración PostgreSQL temporal: 22 PASS, 98,56 segundos; servidor local detenido al finalizar. Importador y lanzador aislados: 8 y 5 PASS.
- Un intento previo de suite, iniciado antes del cambio de versión, terminó con 1275 PASS y un fallo de comparación 1.1.5 cargada/1.1.6 en disco. Se repite con fuentes estables; no se cuenta ese intento como PASS.
- Suite final estable: 1283 PASS, 0 FAIL, 23 SKIP y 60 subtests PASS, 283,47 segundos. Los skips no se consideran aprobaciones. Importador/lanzador se ejecutaron por separado para aislar su entorno.
- Duplicación de los tres módulos nuevos: jscpd 0 %. Formato Ruff y `git diff --check`: PASS. No se ha medido cobertura diferencial completa de las funciones modificadas del monolito.

## Build y QA del paquete

- Build: `python -m PyInstaller --noconfirm --clean --log-level WARN --distpath D:/SIGEH_RELEASE_116/final-dist --workpath D:/SIGEH_RELEASE_116/final-build build_app.spec`. Artefactos generados y verificaciones de paquete: PASS. Updater construido con `build_updater.spec`. Permanecen advertencias de hidden imports opcionales de mypyc.
- Empaquetado: `python -m release_packaging --dist D:/SIGEH_RELEASE_116/final-dist/SIGEH --updater D:/SIGEH_RELEASE_116/updater-dist/SIGEH_Updater.exe --output D:/SIGEH_RELEASE_116/package --version 1.1.6`: PASS.
- ZIP: CRC, SHA-256, 1808 archivos cotejados con manifiesto, versiones y comparación del bytecode empaquetado con las fuentes finales: PASS. Incluye COM y los tres módulos nuevos.
- SHA-256: `bd0617128b14eff2c1e08c881ca4010918b3c71ea803eb9bbed8d8a8c32d2c5b`.
- Smoke del ejecutable extraído: `--check-v15-package`, `--self-test-pdf`, `--self-test-reports`: PASS, los tres exit code 0. Admisión, Historial y Configuración abrieron sin panel de error; se generaron PDF y XLSX en un perfil aislado.
- Seguridad de alcance: SQL parametrizado, identidad por UUID, ausencia de bases de datos y credenciales empaquetadas, instancia Excel privada y sin QA productivo: PASS en revisión y comprobaciones de paquete. Pentest integral: N/A, no forma parte de este hotfix.
- Evidencia local: `D:/SIGEH_RELEASE_116/` (XML de pruebas, coverage, lint, duplicación, logs, package-verification.json y smoke.json).

## Cuatro pasadas y quality gates

1. Funcionalidad: PASS automatizado para especialidades, UUID de Historial y entrega durable; impresión física NO VERIFICADO.
2. Regresión: PASS en suite final y suites aisladas; integración PostgreSQL temporal PASS.
3. Clean code: PASS de revisión del alcance; complejidad máxima nueva 7 y duplicación nueva 0 %. Métricas integrales del monolito NO VERIFICADO.
4. QA: unitarias, integración, límites, regresión, build, paquete y smoke PASS. Cobertura, formatter, lint, análisis estático y tipos de módulos nuevos PASS; gates integrales de todo el código heredado modificado NO VERIFICADO. QA hospitalario NO VERIFICADO.

Los gates integrales heredados y la prueba física de impresión continúan NO VERIFICADO. No declarar la impresión hospitalaria solucionada únicamente por pruebas simuladas.

ESTADO FINAL: NO APROBADO PARA ENTREGA.
