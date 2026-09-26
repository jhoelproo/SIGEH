# Incidente de turno del 26 de septiembre de 2026

Versión: SIGEH 1.2.4. Base: `dcd0551` (1.2.3).

## Evidencia y causa

Se consultaron únicamente intervalos, auditoría operacional y errores técnicos centrales. No se descargó el historial clínico ni se modificaron datos de producción.

- El turno 3983 comenzó el 25/09 a las 07:45 y terminó el 26/09 a las 08:30, hora del hospital.
- A las 08:30:33, la auditoría registra `ADMIN_TURN_OVERRIDE`, con el mismo representante antes y después, `allocate_central_turn_id=true` y horario `8AM_8PM`. La operación creó el turno 3984 y cerró el 3983.
- A las 08:41:03 se confirmó `PRIMARY_USER_HANDOFF`, de 3984 a 3985. El informe automático tomó el tramo corto porque era el turno saliente registrado.
- La captura muestra 2 admisiones y 1 atención elegible para facturación. Son universos diferentes (una admisión sin seguro); no corresponde forzar ambos indicadores a tener el mismo número.
- Entre ambas operaciones, la misma computadora alternó reiteradamente dos sesiones de usuario mediante `LOGIN_SESSION_REBOUND`.
- PostgreSQL registró `40P01` a las 08:28:50 y 08:38:06. El heartbeat bloqueaba primero el dispositivo y luego la sesión; el reenganche hacía lo contrario.
- El inicio repetía DDL y reiniciaba punteros locales incluso con el marcador productivo ya confirmado. La prueba reproduce que una reapertura podía cerrar el turno local y marcar eventos pendientes como superados.

La captura `DB-PROD-002` no incluye el detalle de su excepción. Los bloqueos centrales están confirmados, pero no se afirma que ese diálogo concreto corresponda necesariamente al mismo deadlock; falta el log de esa computadora.

## Implementación

- `admission_hybrid.py`: la corrección de horario enviada por el diálogo conserva turno, generación, representante e inicio. Actualiza horario y revisión en una transacción, con auditoría idempotente y comprobación de un único intervalo abierto. El heartbeat utiliza el mismo orden de bloqueo que las operaciones de turno.
- `admission_v15_adapter.py`: una ventana que ya obtuvo su vinculación no vuelve a apropiarse de otra sesión al refrescar. Un nuevo inicio de sesión explícito conserva su recuperación normal.
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: explicación de corrección de horario, vista del inicio conservado y espejo local basado en el inicio central original.
- `sigeh_product.py`: inicio ya preparado mediante consulta de lectura; reset local una vez por época, con marcador persistente, conservación de eventos pendientes y archivos temporales independientes.
- Metadatos de versión y pruebas de actualización ajustados a 1.2.4.

Se mantienen el relevo entre representantes, sus reportes y efectos posteriores, las reglas de facturación y autorización, el historial, los recibos, las impresiones, la sincronización incremental y los permisos. La API técnica heredada que recibe explícitamente otro identificador de turno mantiene su compatibilidad; la corrección de horario de la aplicación no utiliza esa ruta.

## Pruebas de regresión

Antes de corregir, cuatro pruebas fallaron por nueva identidad de turno, inversión de bloqueos, DDL repetido y pérdida del estado local en una reapertura. Otra prueba reprodujo la apropiación reiterada de sesión entre ventanas.

La integración PostgreSQL utiliza un servidor desechable en `127.0.0.1`. Simula 111 admisiones, corrección de horario, 2 admisiones adicionales y relevo: conserva un solo turno de 113 atenciones, 30 recibos, RD$33.00 y 83 pendientes. Repetir el relevo conserva una sola captura de cierre. También verifica el inicio mientras otra conexión mantiene tablas operacionales ocupadas y la concurrencia del heartbeat.

No se realizó QA destructivo contra producción. La impresora física del hospital no forma parte del entorno de pruebas.

## Caso histórico pendiente

Los turnos 3983/3984 y sus documentos originales no se borraron ni fusionaron. El usuario no pudo confirmar qué acción se realizó a las 08:30. Se solicitó confirmar si las 2 atenciones adicionales son válidas o duplicadas antes de reconstruir un informe histórico corregido. La corrección preventiva no depende de alterar ese historial.

## Pruebas, resultados y cobertura

- Regresión completa final: **1.748 PASS, 0 FAIL, 1 SKIP y 60 subpruebas PASS**, en 814,64 segundos. Con las ocho pruebas Qt aisladas: **1.756 PASS**. La prueba omitida exige habilitar explícitamente una prueba de capacidad contra PostgreSQL real; no es necesaria ni segura como QA automático de producción.
- Primera pasada completa: 1.737 PASS, 2 FAIL, 1 SKIP y 60 subpruebas PASS. Los fallos eran fixtures: la integración usaba un cierre anterior a la habilitación del reporte y el arranque simulado no devolvía una época válida. Se fijó la fecha de habilitación de la base desechable y se aislaron los servicios productivos en la prueba de arranque, sin debilitar reglas del producto.
- Confirmación de ambos fallos y pruebas del incidente: 24 PASS.
- Suite focal final: 158 PASS, 0 FAIL. Casos adicionales del marcador local: 19 PASS, 0 FAIL.
- Importación Qt ejecutada en proceso independiente: 8 PASS, 0 FAIL. Se mantiene este aislamiento por el ciclo de vida de sus hilos Qt en la suite monolítica.
- PostgreSQL desechable: conservación de la cohorte y recibos, cierre idempotente, concurrencia, lectura durante bloqueos y rollback completo cuando falta el intervalo.
- Bordes: horario vacío/inválido, las tres opciones válidas, identidad/inicio inexistentes, 0/2 intervalos abiertos, reintento, época vacía, marcador ausente/corrupto, disco con error, reapertura y tres inicios simultáneos.

Cobertura real con `coverage.py --branch`, restringida al código productivo modificado mediante el diff contra `dcd0551`: **78/78 líneas (100 %) y 36/36 ramas (100 %)**. Por archivo: producto 42/42 y 16/16; servicio operacional 22/22 y 12/12; adaptador 4/4 y 2/2; interfaz 10/10 y 6/6. No representa cobertura del proyecto completo.

La función completa nueva `_correct_turn_schedule` tiene 18/18 líneas ejecutables y 10/10 ramas cubiertas. Las cinco funciones críticas examinadas reúnen 70/71 líneas y 27/28 ramas; queda sin ejecutar la salida heredada de `_reset_local_database` cuando se le pasa directamente una ruta inexistente, que el llamador ya excluye.

Comando de regresión completa: `python -m pytest tests --ignore=tests/test_admission_import_task_manager.py --junitxml=output/turn-incident-full-final.xml -q --tb=short`, con `QT_QPA_PLATFORM=offscreen`. El módulo excluido se ejecutó aparte con `python -m pytest tests/test_admission_import_task_manager.py`.

Evidencia local: `output/turn-incident-full-final.xml`, `turn-incident-final.xml`, `turn-incident-boundaries.xml`, `turn-incident-isolated-qt.xml`, `turn-incident-coverage.json`, `turn-incident-diff-coverage.json` y `turn-incident-critical-coverage.json`.

## Calidad y seguridad

- Ruff diferencial contra la base: 0 errores nuevos en los cuatro módulos productivos.
- Ruff en los nueve archivos de pruebas modificados/nuevos: PASS, sin avisos.
- Ruff formatter: PASS en `sigeh_product.py`, los dos archivos nuevos de pruebas y las funciones nuevas extraídas. Los módulos heredados extensos conservan su estilo sin reformateo masivo.
- Mypy: PASS en `sigeh_product.py` con `--check-untyped-defs --follow-imports=silent --ignore-missing-imports`. Los módulos GUI heredados no tienen una puerta global de tipos limpia; no se afirma haber tipado toda la aplicación.
- Compilación sintáctica: PASS en los cuatro módulos modificados.
- Radon: complejidad máxima de funciones nuevas **8**; las otras nuevas tienen 1, 2 y 3.
- Pylint `duplicate-code`, mínimo 6 líneas: PASS, sin hallazgos. No se interpreta este resultado como un porcentaje de duplicación de todo el repositorio.
- Bandit: **0 hallazgos en líneas modificadas**. Permanecen 240 avisos heredados fuera del cambio; no se desactivaron reglas. El borrado SQL heredado de cachés usa nombres constantes de una lista cerrada, sin entradas del usuario.
- Revisión manual: permisos de administrador/principal antes de la corrección; SQL parametrizado; auditoría y actualización dentro de la misma transacción; no se incluyen credenciales ni bases operacionales en el ZIP.

## Build y QA del paquete

Build de aplicación: PASS.

```powershell
python -m PyInstaller --noconfirm --distpath output/turn-incident-app --workpath build/turn-incident-app build_app.spec
```

Build de actualizador: PASS.

```powershell
python -m PyInstaller --noconfirm --distpath output/turn-incident-updater --workpath build/turn-incident-updater build_updater.spec
```

Empaquetado público: PASS con `release_packaging.py --dist output/turn-incident-app/SIGEH --updater output/turn-incident-updater/SIGEH_Updater.exe --output output/release-1.2.4 --version 1.2.4`.

SHA-256 del ZIP: `83362087763fdf43ee20c5b508bb1065426a7d654213c132fc7d727d5bd23906`.

Se extrajo el ZIP en una carpeta nueva y se verificaron **1.822 hashes de archivos**. Las siete comprobaciones PASS fueron: rechazo controlado de conexión no configurada (salida esperada 5), lanzador con configuración sintética, generación PDF, apertura con visor Qt integrado, exportaciones de reportes, exportación de transferencia y carga del paquete Admisión. Las otras seis devolvieron 0. Los tests no conectaron ese paquete con producción.

QA Qt: apertura del diálogo para administrador y auxiliar, textos, operación seleccionada, conservación del inicio, confirmación y separación del relevo; capturas inspeccionadas. No se verificó impresión física en el hospital ni el diálogo exacto de error sin su log.

Evidencia local: `output/turn-incident-build-final.log`, `turn-incident-updater-build.log`, `turn-incident-package-qa.json`, `turn-incident-format.log`, `turn-incident-static.json`, `turn-incident-duplication.log`, `turn-incident-security.json` y `output/turn-incident-gui/`.

## Cuatro pasadas y puertas finales

1. Funcionalidad: preservación del turno al corregir horario, cohorte completa al relevar y reapertura conservando pendientes.
2. Regresión: suites focales, PostgreSQL, suite completa final y Qt aislado PASS.
3. Clean code: cambio acotado, helper dedicado, parámetros SQL y cero avisos nuevos de lint/duplicación.
4. QA: cobertura, tipos aplicables, análisis, builds y paquete PASS.

| Puerta | Estado | Alcance |
|---|---|---|
| Funcionalidad | PASS | Corrección preventiva y preservación del relevo |
| Unitarias, regresión e integración | PASS | 1.756 aprobadas y 60 subpruebas |
| Cobertura y bordes | PASS | 100 % del diff; casos críticos descritos arriba |
| Formatter y lint | PASS | Formato nuevo y cero errores nuevos |
| Tipos aplicables | PASS | Mypy del módulo de producto; GUI dinámica fuera de una puerta global de tipos |
| Análisis estático y complejidad | PASS | Compilación sintáctica, Ruff y complejidad nueva máxima 8 |
| Duplicación | PASS | Sin hallazgos nuevos con Pylint |
| Build | PASS | Aplicación y actualizador |
| Smoke y QA Qt | PASS | Paquete extraído y diálogos |
| Seguridad | PASS | Cero hallazgos en el diff y paquete sin datos/secretos |
| QA final | PASS | Cuatro pasadas completadas |
| Capacidad contra producción | N/A | Prueba externa optativa; no se carga producción para QA |
| Impresión física en el hospital | NO VERIFICADO | Sin acceso a esa impresora; su implementación no cambió |
| Causa exacta del diálogo DB-PROD-002 | NO VERIFICADO | Falta el log de la estación afectada |

**ESTADO FINAL: APROBADO PARA ENTREGA** de la corrección preventiva 1.2.4. La rectificación del cierre histórico sigue pendiente de validar las dos atenciones adicionales; no se presenta como realizada.
