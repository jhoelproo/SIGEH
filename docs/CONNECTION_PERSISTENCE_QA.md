# Persistencia de conexión entre actualizaciones

## IMPLEMENTACIÓN

`database_config.py`: resolver DPAPI y `.env` tanto junto al ejecutable como en `_internal`. Se conserva la prioridad de entorno administrado, bundle portable, DPAPI y `.env`. `updater.py`: preservar `.env` en ambas ubicaciones durante el reemplazo de la distribución. Pruebas en `test_database_config_portable.py` y `test_sigeh_update.py`.

## PRUEBAS Y RESULTADOS

RED: tres regresiones reproducidas, tres fallos reales antes del cambio (`output/connection-persistence-red.xml`). GREEN: 46 pruebas PASS de configuración, actualización, lanzador y paquete, cero fallos/omisiones (`connection-persistence-tests.xml`). Regresión adicional: 56 PASS de arranque, migraciones y actualización, cero fallos/omisiones (`connection-persistence-regression.xml`). El primer intento de esta selección nombró un archivo inexistente y no ejecutó pruebas; se corrigió la selección antes del resultado informado.

Prueba de integración de actualización completa sobre filesystem temporal: se reemplaza la aplicación, se conservan datos y configuración, y la nueva instalación resuelve la misma URL sintética. DPAPI real del usuario Windows en directorios temporales. Ningún secreto real ni operación sobre el hospital.

## COBERTURA

coverage.py: función modificada `_resolve_database_url_with_source`, 17/17 líneas y 14/14 ramas, **100 % / 100 %** (`connection-persistence-patch-coverage.json`). Las dos constantes de rutas añadidas al actualizador se ejercitan con las pruebas de preservación y actualización completa.

## CALIDAD

Ruff lint: PASS. Ruff formatter: PASS sobre los cuatro archivos afectados. Mypy: PASS, cero errores en los dos módulos. Radon: función modificada 10; sin funciones nuevas. Funciones heredadas del actualizador exceden 10 antes del cambio; se agregan solo dos rutas a la constante, sin modificar sus responsabilidades ni complejidad. Pylint duplicate-code, seis líneas: sin bloques detectados. SQL: N/A, no se cambian consultas ni datos. No hay supresiones ni cambios de umbral.

Bandit ejecutado: contiene hallazgos heredados sobre criptografía portable y subprocess sin shell; comparación diferencial ejecutada: cinco hallazgos antes y después, cero nuevos (`connection-persistence-security-diff.json`). No se afirma que todos los módulos estén libres de hallazgos.

## BUILD Y QA

Build limpio de aplicación y actualizador: PASS, exit 0. Comparación del código empaquetado: aplicación, lanzador, database_config y actualizador coinciden con las fuentes (`connection-persistence-package-verification.json`). Smoke real de lanzador con configuración sintética, generación PDF, apertura del visor y reportes: cuatro PASS, exit 0 (`connection-persistence-smoke.json`). QA de regresión del conjunto anterior conservada en `MONTHLY_MANUAL_RECEIPTS_QA.md`; no se presenta como una nueva suite completa posterior a este cambio.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad de preservación de conexión | PASS |
| Unitarias/integración/regresión relacionada | PASS |
| Cobertura y casos alternativos | PASS |
| Formato/lint/tipos | PASS |
| Complejidad nueva/modificada y duplicación | PASS |
| Seguridad final diferencial | PASS — cero hallazgos nuevos |
| Build y smoke del paquete nuevo | PASS |
| Diagnóstico del error exacto del hospital | NO VERIFICADO |
| QA final | NO VERIFICADO |
| Migración SQL | N/A — no hay cambio |

## PROBLEMAS PENDIENTES

La captura muestra la excepción genérica del lanzador, no la causa técnica. Se recibió el log del hospital: termina antes del error mostrado tras actualizar. Falta el log de la instalación resultante y el del actualizador para contrastar ese incidente concreto. No se puede afirmar que los defectos corregidos expliquen por sí solos ese mensaje. No se ha publicado una versión.

**ESTADO FINAL: NO APROBADO PARA ENTREGA — validaciones y diagnóstico pendientes.**

## Reparador localizado y alcance confirmado

Se localizó el asset público SIGEH-1.1.6-reparar-conexion.exe de v1.1.6 en jhoelproo/SIGEH. Inspección estática sin ejecutar ni descifrar credenciales: instala database_url.bundle bajo _internal mediante archivo temporal y os.replace, y verifica el lanzador. Las pruebas nuevas comprueban que un bundle real sintético se conserva byte por byte y se resuelve tras dos actualizaciones sucesivas, en raíz y _internal. Esta vía ya estaba preservada por el actualizador previo: no se afirma que la falta de preservación de .env explique necesariamente el incidente del reparador. Se corrigen dos defectos demostrados y permanece pendiente el diagnóstico concreto de la captura.

## Análisis del log recibido y persistencia de recuperación — 4 de octubre

Fuente recibida: `C:/Users/ampar/OneDrive/Desktop/lanzador_log.txt`. Resumen sin credenciales: `output/hospital-launcher-log-summary.json`. Contiene 330 eventos, 21 fallos CONFIGURATION_MISSING, seis arranques con configuración recuperada de existing_install y 22 con portable_bundle. Último evento JSON: 2026-10-02T15:24:42.562937+00:00. Última línea posterior: entrega de 1.2.6 al instalador el 2026-10-03 07:34:51. No contiene UNEXPECTED_BOOTSTRAP_ERROR ni el fallo de la captura del 3 de octubre por la tarde. La ausencia de registro no demuestra ausencia del fallo.

### IMPLEMENTACIÓN

- `portable_launcher.py`: al recuperar la conexión de una instalación anterior, guarda una copia cifrada con DPAPI en la instalación actual. El actualizador conserva ese archivo. Un fallo de persistencia registra solo el tipo del error y permite continuar el arranque que ya dispone de conexión.
- `database_config.py`: escritura de configuración protegida mediante temporal, flush, fsync y reemplazo atómico. Un fallo de escritura o reemplazo conserva el archivo anterior y elimina el temporal.
- `tests/test_portable_launcher.py`, `tests/test_database_config_portable.py` y `tests/test_sigeh_update.py`: regresión de segundo arranque después de retirar la configuración anterior, preservación del archivo DPAPI en raíz y _internal durante dos actualizaciones, cifrado real de Windows, fallos de disco/reemplazo/cifrado, valor vacío y ausencia de credenciales en los logs.
- Se preservan prioridades de resolución, conexión del proceso hijo y reglas de atención/facturación. No hay cambios de SQL ni escritura en producción.

### PRUEBAS Y RESULTADOS

RED ejecutado: la prueba de recuperación falló porque no se guardaba database_url.protected (`connection-recovery-red.xml`). GREEN y suite de regresión relacionada final: 111 PASS, cero fallos, cero skipped (`connection-recovery-tests.xml`). La prueba usa una URL sintética y no conecta con una base real. Los fallos de disco se simulan; la lectura/escritura y DPAPI se ejecutan realmente en Windows.

### COBERTURA Y CALIDAD

Cobertura real con coverage.py: escritura protegida 19/19 líneas y 2/2 ramas; resolución 17/17 y 14/14; persistencia del lanzador 7/7 líneas y sin ramas instrumentadas, ambos resultados de excepción y éxito probados. Detalle: `connection-recovery-patch-coverage.json`. Radon: escritura 4, persistencia 2, resolución 10. Ruff lint y formatter PASS sobre seis archivos; Mypy PASS sobre tres módulos; Pylint duplicate-code sin detecciones. Bandit diferencial: siete hallazgos heredados antes y después, cero nuevos (`connection-recovery-security-diff.json`). No se excluye código modificado para alcanzar cobertura: el JSON se limita a los módulos pertinentes porque la suite carga un pyscript sintético sin archivo fuente.

### BUILD Y QA DEL CAMBIO NUEVO

Comando ejecutado: `python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/connection-recovery-app --workpath build/connection-recovery-app build_app.spec`. PASS, exit 0 (`connection-recovery-build.log`). El actualizador no recibió nuevos cambios desde el build anterior verificado. Comparación bytecode de cinco módulos empaquetados con sus fuentes: PASS (`connection-recovery-package-verification.json`). Smoke del ejecutable de lanzador, PDF, visor gráfico y reportes: cuatro PASS, exit 0 (`connection-recovery-smoke.json`). Integración adicional con SIGEH.exe real: recupera configuración sintética de otra carpeta, la cifra y persiste; se retira el archivo de la carpeta anterior y el segundo arranque sigue resolviendo desde DPAPI. Ambos exit 0 (`connection-recovery-binary-integration.json`). Se comprueba que la URL no está en texto plano y se retira la configuración sintética del artefacto al finalizar.

Pasada 1 funcionalidad: PASS en recuperación y conservación local; incidente exacto posterior a 1.2.6 NO VERIFICADO. Pasada 2 regresión relacionada: PASS. Pasada 3 clean code: PASS, sin refactorización masiva. Pasada 4 QA local: pruebas, cobertura, análisis estático, seguridad, build y smoke PASS. La suite completa hospitalaria de 2073 PASS documentada en MONTHLY_MANUAL_RECEIPTS_QA.md es anterior a este cambio y no se presenta como ejecutada nuevamente.

### QUALITY GATES Y PROBLEMAS PENDIENTES

Unitarias, integración filesystem/DPAPI/ejecutable, regresión, límites, cobertura, formato, lint, tipos, análisis estático, complejidad, duplicación, seguridad diferencial, build y smoke: PASS. Integración SQL: N/A — no cambia persistencia de base de datos. Diagnóstico del fallo exacto y QA final del incidente: NO VERIFICADO. Hace falta el lanzador_log.txt de la carpeta instalada después de la actualización y `%LOCALAPPDATA%/SIGEH/updates/actualizador.log` de la computadora del hospital. No se ha publicado una nueva versión.

**ESTADO FINAL: NO APROBADO PARA ENTREGA.**

## Decisión posterior de publicación

El usuario autorizó publicar la siguiente versión conservando pendientes los diagnósticos del hospital. La validación del release 1.2.7, incluida la suite completa y dos ciclos de actualización con los ejecutables instalados, está en RELEASE_1.2.7_QA.md. El estado anterior corresponde al diagnóstico que seguía pendiente, no a un fallo de las pruebas locales de persistencia.

