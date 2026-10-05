# SIGEH 1.2.8 — validación del release

## IMPLEMENTACIÓN

Se incrementan `sigeh_product.py`, `version_config.json` y las expectativas de versión de `tests/test_sigeh_update.py` a 1.2.8. El ejemplo de siguiente versión pasa a 1.2.9. La expectativa 1.2.8 falló antes de incrementar la versión: `output/release-128-version-red.xml`.

El release reúne los cambios de `private_insurance_exporter.py`, `CALCULOS_QT.py` y el módulo nuevo `monthly_batch_removal.py`: logo recortado con DrawingML, posiciones de la relación/factura según los adjuntos, clic derecho, selección múltiple y retiro transaccional solo del listado ARS. Se añaden o amplían `tests/test_private_insurance_exporter.py`, `tests/test_monthly_ars_form_state.py` y `tests/test_monthly_batch_removal.py`. Las reglas y la validación funcional detallada están en `MONTHLY_ARS_LAYOUT_AND_SELECTION_QA.md`; las notas de usuario están en `RELEASE_1.2.8.md`.

Se conservan recibos, admisiones, pacientes, fechas de servicio, precios, fórmulas, permisos y configuración de conexión. Los formatos se aplican a nuevas exportaciones; no reescriben Excel ya emitidos. El ZIP público no incluye credenciales ni bases hospitalarias. No se modifica el esquema de la base central.

## PRUEBAS

Regresión específica de lanzador, actualización y paquete con 1.2.8: **36 PASS**, cero fallos, errores u omisiones. Comando:

```
python -m coverage run --branch --data-file=output/release-128-version.coverage -m pytest tests/test_sigeh_update.py tests/test_release_packaging_fresh_install.py tests/test_portable_launcher.py -q --tb=short --junitxml=output/release-128-update-final.xml
```

Las unitarias, integración PostgreSQL descartable, rollback, auditoría, permisos, GUI, selección, filtros, límites y documentos del cambio funcional se detallan en el informe de listados. Esa lógica permanece idéntica al contenido medido; solo cambian la identidad de versión y sus expectativas para preparar este release. No se hace QA destructivo sobre producción.

## RESULTADOS

La suite completa se recollectó después del incremento de versión y terminó en once grupos aislados: **PASS**. Se recogieron y ejecutaron **2050 casos regulares**, 2049 PASS, cero FAIL, cero errores y un skipped de capacidad opcional de PostgreSQL real. Además, **74 subtests PASS**: **2123 comprobaciones aprobadas** y una omitida. No faltan casos ni hay casos adicionales respecto a la colección; los IDs aleatorios de parámetros se contrastan por función y cantidad.

Todos los grupos corresponden al fingerprint de las fuentes finales `1125ddc88f65a14cb09d6f4b7909ed99050dd8f737e5c513a71b0f915fbdd3f6`. Evidencia: `output/release-128-full-summary.json`, `release-128-full-suite-completeness.json`, `release-128-final-fingerprint.json` y once XML/logs. No se atribuyen a 1.2.8 los resultados de la regresión anterior con 1.2.7.

## COBERTURA

Herramienta real: coverage.py con ramas. Lógica funcional nueva/modificada: **573/577 líneas = 99,31 %**, **104/108 ramas = 96,30 %**. Persistencia crítica del retiro y documentos modificados: **100 % líneas/ramas**. El ámbito medido son las funciones modificadas y el módulo nuevo; no es cobertura de todo SIGEH. Evidencia: `output/ars-list-coverage-summary.json`.

La nueva asignación de `APP_VERSION` se mide además con la regresión de actualización: **1/1 línea modificada, 100 %**. Ramas: N/A — asignación constante. Evidencia: `output/release-128-version-scope-coverage.json`.

## CALIDAD

Ruff formatter/lint de producto y pruebas de versión: PASS; Mypy del módulo de producto: PASS. Repetición del análisis funcional: formatter PASS, lint/tipos diferencial PASS, cero diagnósticos nuevos frente a HEAD, sin supresiones. Se preservan los diagnósticos heredados. Evidencia: `output/release-128-version-quality.json` y `ars-list-static-summary.json`.

Radon: complejidad nueva máxima 10, persistencia máxima 9. Excepciones heredadas del constructor de tabla/documentos documentadas en el informe funcional, sin aumento. Pylint duplicate-code: 0 líneas duplicadas, 0,000 % en los tres archivos funcionales examinados. Bandit del módulo nuevo/exportador: cero hallazgos. SQL estático y parametrizado; permisos en UI y backend, IDs validados y retiro en una sola transacción. No se introducen secretos ni cambios de credenciales. Las fuentes funcionales no cambian después de estas mediciones.

## BUILD

Ambos builds y el empaquetado: **PASS, exit 0**.

```
python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/release-128-app --workpath build/release-128-app build_app.spec
python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/release-128-updater --workpath build/release-128-updater build_updater.spec
python release_packaging.py --dist output/release-128-app/SIGEH --updater output/release-128-updater/SIGEH_Updater.exe --output output/release-1.2.8 --version 1.2.8
```

Evidencia: `output/release-128-app-build.log`, `release-128-updater-build.log` y `release-128-packaging.log`. Diecinueve comprobaciones de bytecode empaquetado/fuentes PASS, incluyendo los dos módulos funcionales de este release, el monolito, producto, lanzador y actualizador. Las advertencias heredadas de imports aceleradores opcionales no impiden construir ni ejecutar los modos de prueba. No se modifica código de producción después del build.

## QA

ZIP final: CRC, tamaño y SHA-256 de **1822 archivos PASS**; identidad interna/externa 1.2.8 PASS; ausencia de logs de ejecución, configuración privada y bases operacionales PASS. SHA-256 del ZIP: `3ab321a264cfa62abbabd3ad4be9594a360d553c02be6bb204f46adde23bb54f`.

La comparación de nombres con 1.2.7 detectó `lanzador_log.txt`, generado por el smoke test antes del empaquetado. La comprobación específica de exclusión falló, se retiró el archivo de la carpeta de build y se volvió a empaquetar. Se repitieron las seis comprobaciones del ZIP y ambos ciclos de instalación sobre el paquete limpio: todos PASS. La prueba de exclusión final pasa; no quedan archivos agregados/eliminados frente al conjunto de nombres de 1.2.7. Se conserva la evidencia anterior con `before-cleanup`, y los resultados finales en `output/release-128-runtime-log-green.json` y `release-128-package-validation.json`. No cambia código ni bytecode, y el paquete anterior permaneció como borrador sin activar el canal público.

Cuatro smoke tests previos al empaquetado PASS y seis comprobaciones del ZIP extraído PASS: lanzador, CLI del actualizador, paquete de Admisión, PDF, visor y reportes, todos exit 0.

Se comprueba el checksum del ZIP 1.2.7 antes de usarlo como instalación inicial. Actualización desde el código del actualizador instalado 1.2.7 a 1.2.8: PASS. Segunda instalación con código del actualizador instalado 1.2.8: PASS. Ambos ciclos usan filesystem real y health check de los ejecutables. Configuración cifrada e historial sintético se conservan byte por byte, y siguen presentes los dos pacientes de prueba. Solo se sustituye el callback final de apertura de sesión para evitar contactar producción. Se reaplica el mismo paquete para comprobar otro ciclo; no se afirma probar una versión futura distinta. Evidencia: `output/release-128-package-validation.json`.

Microsoft Excel real: recálculo y exportación del ejemplo sintético PASS, once pacientes, RD$ 8.495,60 en relación/factura/total. Revisión visual del logo, datos fiscales, tabla, totales y firma PASS. Cuatro verificaciones reales de ratón/teclado Qt PASS. Evidencia y capturas en `output/ars-list-layout-qa/` y `ars-list-mouse-qa.json`.

Cuatro pasadas finales: funcionalidad PASS (logo, posiciones, menú, selección, retiro y conservación de datos); regresiones PASS (suite completa con 1.2.8, integración y actualizaciones); clean code PASS (responsabilidades, deduplicación, métricas y manejo de errores); QA PASS (cobertura, análisis, build, smoke, actualización y revisión visual). `git diff --check`: PASS. Los resultados/scripts sintéticos permanecen en `output/`, fuera del producto y del conjunto publicado.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad, unitarias, límites | PASS |
| Integración, transacciones y rollback | PASS |
| Cobertura, formatter, lint/tipos diferencial y análisis estático | PASS |
| Complejidad documentada, duplicación y seguridad | PASS |
| Build, empaquetado, smoke y actualización | PASS |
| Regresión completa | PASS |
| QA final de cuatro pasadas | PASS |
| Publicación y canal de actualización | PASS |
| Capacidad opcional de PostgreSQL real | N/A — requiere entorno explícito y no valida este ajuste |
| Impresión física en el hospital | N/A — requiere su impresora; Excel/PDF sí se valida |

## PUBLICACIÓN

Publicado el **2026-10-05T12:32:11Z** como estable y latest: https://github.com/jhoelproo/SIGEH/releases/tag/v1.2.8. Tag sobre `27c27fb2892ed13d8e94b961d14965462e7ed99b`.

Cuatro assets remotos completos, tamaños y SHA-256 idénticos al paquete limpio validado. Se verificaron antes de publicar el borrador y otra vez después. La API pública consultada con `sigeh_update` devuelve 1.2.8, resuelve el manifest/checksum remoto, detecta actualización desde 1.2.7 y rechaza volver a instalar la misma 1.2.8. Evidencia: `output/release-128-draft-verification.json` y `release-128-publication-verification.json`.

## PROBLEMAS PENDIENTES

No quedan problemas pendientes en el alcance de este release. La impresión física requiere la impresora del hospital; el motor Excel/PDF sí se comprobó. Los incidentes del hospital diferidos en el informe de 1.2.7 siguen fuera del alcance de este ajuste; no se presentan como resueltos. La publicación deja disponible la actualización, sin afirmar que ya se haya instalado en las estaciones del hospital.

**ESTADO FINAL: APROBADO PARA ENTREGA**
