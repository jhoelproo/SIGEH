# QA SIGEH 1.1.8 — autoría, urgencias y recuperación del cierre

Fecha: 09/09/2026. Base de comparación: 16ff15ba711124ca808022a1c451182a9a291ce3 (1.1.7).

## Implementación

- `admission_hybrid.py`, `admission_authorship.py`: conservan autor, turno de origen, equipo de origen y fecha de creación al editar. El usuario operativo sigue validándose por separado. Recuperación idempotente de autoría únicamente desde evidencia CREATE durable, nunca por nombre del paciente.
- `admission_contract.py`, `admission_urgency_repair.py`: URGENCIA conserva su clasificación al materializarse en central. Corrige proyecciones activas cuyo propio payload guardado demuestra URGENCIA. Continúa excluida de facturación como EMERGENCIA.
- `admission_bridge.py`: la lectura de atenciones de turno incluye UUID y metadatos operativos.
- `admission_v15_adapter.py`: el Historial puede mostrar atenciones locales con conflicto de sincronización. Mostrar un conflicto no autoriza facturarlo sin identidad central ni elimina restricciones de elegibilidad.
- `billing_closure_recovery.py`, `CALCULOS_QT.py`: recuperan capturas/reportes de relevos centrales confirmados, con identidad central de turno y fuente. Solo ejecuta una estación primaria sincronizada. No provoca relevos por horario ni trata correcciones administrativas como relevo. La cola de reportes evita solicitudes duplicadas mientras trabaja.
- El corte autorizado es fijo: 07/09/2026 00:00 UTC-4. Se incluyen turnos anteriores que terminaron a partir del corte y se conservan pendientes entre días; no existe reinicio diario. Se respetan anulaciones y descartes previos de la lista.
- `sigeh_product.py`, `version_config.json`: versión 1.1.8. Pruebas nuevas y existentes actualizadas bajo `tests/`.

## Pruebas y límites

Pruebas de SQLite y PostgreSQL local desechable: autor original frente a editor, persistencia y reproducción en otra estación; urgencia frente a emergencia; UUID; conflictos visibles; pendientes heredados durante tres días e idempotencia; corte exacto y segundo anterior; turno abierto; transición no confirmada/administrativa; anuladas/tombstones; guardas de estación y sincronización; cola y fallos de recuperación.

No se utilizó producción para QA destructivo. La consulta productiva anterior fue de solo lectura. Las dos hojas del 08/09 no se identificaron en central: las coincidencias antiguas encontradas pertenecían a otras fechas. No se reconstruyeron atenciones desde fotografías ni se asociaron por nombre. Su recuperación requiere evidencia de la instalación hospitalaria que las generó.

## Resultados y cobertura

- Suite completa final: **1453 passed, 0 failed, 1 skipped, 60 subtests passed**, 309,59 s. Dos avisos de deprecación preexistentes.
- Pruebas específicas finales de recuperación/cola/ventana: **16 passed, 0 failed**, 2,65 s. Incluyen cinco pruebas añadidas después de la suite completa; las otras once se repitieron. No hubo cambios posteriores de código productivo.
- La omisión es la prueba explícita de capacidad de una base real: fuera del alcance. Las integraciones de PostgreSQL del cambio sí se ejecutaron y pasaron en bases locales desechables.
- Coverage.py real, sobre líneas ejecutables añadidas/modificadas respecto a la base y módulos nuevos: **87/88 líneas = 98,86 %; 18/18 ramas = 100 %**. Los tres módulos nuevos de lógica crítica alcanzan 100 % de sus líneas ejecutables. Las ramas SQL se verifican mediante casos de integración, no se presentan como ramas instrumentadas por Python.
- La única línea del cambio no ejecutada pertenece a la compatibilidad del actor en el protocolo central anterior; se mantiene cubierta la ruta integrada actual. No se afirma cobertura completa del monolito.

Comando de suite: `python -m coverage run --branch --source=admission_authorship,admission_urgency_repair,billing_closure_recovery,admission_hybrid,admission_contract,admission_bridge,admission_v15_adapter,CALCULOS_QT -m pytest -v --junitxml=C:/SIGEH_QA_118/final-suite.xml`. Las pruebas específicas se ejecutaron con `coverage run --append` y generaron `queue-tests.xml`; `coverage json` y `coverage_changes.py` produjeron la medición del cambio.

## Calidad

- Ruff: cero problemas nuevos respecto a la base; el monolito mantiene sus 327 avisos previos. Módulos nuevos limpios.
- Mypy `--follow-imports=silent --check-untyped-defs`: PASS en los tres módulos nuevos.
- Radon: complejidad máxima 7 en funciones nuevas. No se refactorizó masivamente el código heredado.
- jscpd (Python, máximo 5 MB): 124 clones tanto en base como en resultado; líneas duplicadas bajan de 1307 a 1306 y porcentaje de 2,3950 % a 2,3864 %. Los bloques que intersectan cambios pertenecen a estructuras heredadas; ningún módulo nuevo contiene clones detectados.
- SQL parametrizado, identificadores constantes, conservación de recibos/claims y UUID, sin credenciales ni bases operativas en el paquete público.

## Build y smoke

```powershell
python -m PyInstaller --noconfirm --distpath C:/SIGEH_QA_118/dist --workpath C:/SIGEH_QA_118/build build_app.spec
python -m PyInstaller --noconfirm --distpath C:/SIGEH_QA_118/updater --workpath C:/SIGEH_QA_118/updater-build build_updater.spec
python release_packaging.py --dist C:/SIGEH_QA_118/dist/SIGEH --updater C:/SIGEH_QA_118/updater/SIGEH_Updater.exe --output C:/SIGEH_QA_118/package --version 1.1.8
```

Los tres comandos finalizaron correctamente. Se compararon 18 módulos empaquetados con el código fuente compilado: coincidencia completa. ZIP: CRC e inventario correctos, 1820 archivos verificados individualmente por SHA256.

SHA256: `1d2fd165dac65865c1f0f482bd17dbbbcce7574b52c3553ffc53dffb6123b715`.

Smoke del ejecutable en perfil aislado: `--check-v15-package`, `--self-test-pdf`, `--self-test-reports`, los tres con salida 0 y artefactos presentes. Impresión física hospitalaria: NO VERIFICADO, equipo no disponible. Se mantiene la comprobación de entrega/impresión de 1.1.7.

## Incidencias del entorno

Windows registró errores de disco, evento 7 (bloques defectuosos), en disco 1, correspondiente a D:. Las ejecuciones allí se interrumpieron; no se consideran aprobadas. La compilación, PostgreSQL de QA y verificaciones se trasladaron a C: (disco 0), sin reparar el disco ni alterar datos productivos.

Una ejecución posterior tuvo una interrupción nativa de Qt en pruebas de importación. Las ocho pruebas del módulo pasaron aisladamente; se repitió la suite completa sin modificar código durante su ejecución. Se conservan los registros de ejecuciones fallidas.

## QA final y quality gates

Cuatro pasadas completadas: funcionalidad y límites; regresiones e integración; clean code, complejidad y duplicación; QA, métricas, build y smoke.

| Gate | Estado |
|---|---|
| Funcionalidad implementada, unitarias, integración, límites y regresión | PASS |
| Cobertura del cambio y nueva lógica crítica | PASS |
| Formatter en módulos/pruebas nuevos, convenciones heredadas, diff sin errores | PASS |
| Lint sin errores nuevos, py_compile y análisis estático | PASS |
| Tipos aplicables en módulos nuevos | PASS |
| Complejidad nueva y duplicación sin incremento | PASS |
| Build, integridad del paquete y smoke del ejecutable | PASS |
| Revisión de seguridad y QA local | PASS |
| Prueba de capacidad productiva | N/A — fuera del cambio |

Limitaciones externas: impresión física en el hospital y recuperación de las dos hojas antiguas ausentes NO VERIFICADAS por falta del equipo/base original. No forman parte de la evidencia de QA local aprobada ni se declaran reparadas.

Evidencia local: `C:/SIGEH_QA_118/` (logs, XML, cobertura, complejidad, duplicación, smoke, verificación de fuentes y archivos).

ESTADO FINAL: APROBADO PARA ENTREGA — software 1.1.8 y paquete verificados, con los límites externos descritos.
