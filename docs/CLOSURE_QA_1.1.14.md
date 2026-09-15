# QA de SIGEH 1.1.14 — 15 de septiembre de 2026

## Implementación

- `CALCULOS_QT.py`: detecta el esquema incompleto de cierres durante el arranque, instala la reparación y calcula pendientes heredados aun cuando falten cierres anteriores. Excluye las autorizaciones anteriores al inicio del turno.
- `billing_closure_recovery.py`: agrega idempotentemente `global_attention_id`, conserva una fecha única de activación y selecciona solamente cierres posteriores. Consulta el inicio exacto del relevo, incluidas las fracciones de segundo.
- `sigeh_product.py`, `version_config.json` y `tests/test_sigeh_update.py`: versión 1.1.14.
- `tests/test_closure_schema_upgrade.py` y `tests/test_central_closure_recovery_118.py`: regresiones de migración, activación, herencia, cortes temporales y PDF persistido.
- `docs/RELEASE_1.1.14.md`: alcance e instrucciones de actualización.

Se preservan las reglas existentes de elegibilidad, identidad, registro del PDF, apertura, despacho a impresión e historial. No se implementó la función de extranjeros ni se regeneraron cierres históricos.

## Pruebas y resultados

La comprobación de compatibilidad falló antes de agregar la tabla/columna requerida. La integración reprodujo el rollback por columna inexistente y comprobó la reparación. La prueba con relevo de precisión fraccionaria falló antes de usar la fecha exacta de la base y pasó después.

- Específicas de cierres, interfaz y migración: **28 passed**.
- Suite completa final: **1553 passed, 0 failed, 1 skipped, 60 subtests passed**, 494.75 segundos.
- Dos avisos de deprecación existentes en dependencias; ningún fallo.
- La prueba omitida es la prueba de capacidad PostgreSQL real, habilitada únicamente mediante una variable explícita. No se ejecutó carga contra producción.
- Integración ejecutada con PostgreSQL 17 local aislado, no contra producción. El clúster temporal fue detenido y eliminado.
- Casos de borde: fecha anterior/igual/posterior a la activación; autorización ausente/vacía/presente; antes/durante/después del turno; relevo con microsegundos; cierres históricos ausentes; migración repetida; esquema parcial; PDF generado, registrado y recuperado.

Comando de regresión: `python -m coverage run --branch -m pytest tests -q -rs --junitxml=output/release-1114-regression-final.xml`, con Qt offscreen y PostgreSQL de prueba local.

## Cobertura

Medida con coverage.py sobre la ejecución final, sin estimaciones:

| Alcance modificado | Líneas ejecutables | Ramas |
| --- | --- | --- |
| CALCULOS_QT.py | 27/27, 100 % | 4/4, 100 % |
| billing_closure_recovery.py | 13/13, 100 % | 2/2, 100 % |
| Funciones nuevas completas | 22/22, 100 % | 4/4, 100 % |

Los porcentajes corresponden al cambio y a sus funciones nuevas, no a toda la aplicación heredada. Evidencia local: `output/release-1114-coverage.json` y `output/release-1114-diff-coverage.json`.

## Calidad

- Ruff check: PASS para el módulo de recuperación y las pruebas modificadas. Comparación de CALCULOS_QT.py contra el commit base: **0 hallazgos nuevos**; no se ocultan los hallazgos heredados.
- Ruff format: PASS para recuperación, pruebas y funciones nuevas extraídas del módulo heredado.
- Mypy: PASS en `billing_closure_recovery.py --follow-imports=skip`. No se afirma tipado completo de la aplicación heredada.
- Compileall y `git diff --check`: PASS.
- Radon: complejidad máxima de funciones nuevas **8**; recuperación **6**. Las funciones heredadas de orquestación conservan deuda previa (captura 49, preparación 14); no se realizó una refactorización masiva. La extracción reduce la complejidad de captura.
- jscpd: **0 clones, 0 % de duplicación** en las funciones nuevas y recuperación, 200 líneas analizadas.

## Build y paquete

PASS: PyInstaller para `build_app.spec` y `build_updater.spec`, con distpath `output/release-114-build` y `output/release-114-updater` y workpath separado. Aplicación, lanzador y actualizador compilados.

PASS: `prepare_release` produjo ZIP público, checksum y dos manifiestos. Se extrajo el ZIP, comprobó CRC, tamaño y SHA-256 de **1822 archivos** y verificó la versión interna/externa 1.1.14.

SHA-256 del ZIP: `6a459c98a72f89851b3b1222586be004666e8725d7712173cc3a206ffa5331d2`.

## QA y seguridad

- PASS: ejecutable extraído `SIGEH.exe --self-test`, con recuperación de configuración desde una instalación anterior sintética.
- PASS: `CALCULOS_QT.exe --self-test-pdf` y `--self-test-reports`; salida PDF real verificada.
- PASS: integración de cierre con PDF real, persistencia, recuperación y solicitudes de apertura/impresión comprobadas mediante dobles de prueba.
- PASS: revisión del cambio: parámetros SQL enlazados; fragmentos SQL dinámicos constantes; sin credenciales incorporadas al paquete público; diagnóstico de producción solo de lectura; QA de escritura limitado al clúster local.
- **NO VERIFICADO: impresión física en la impresora del hospital.** No hay acceso a ese dispositivo. La entrega al mecanismo de impresión está probada por software; papel, controlador y cola deben funcionar en la estación principal.

## Cuatro pasadas y quality gates

Funcionalidad, regresiones, clean code y QA final revisados. Gates de entrega de software:

| Gate | Estado |
| --- | --- |
| Funcionalidad | PASS |
| Unitarias, integración, regresión y bordes | PASS |
| Cobertura del cambio | PASS |
| Formatter, lint y análisis estático del cambio | PASS |
| Type checker del módulo aplicable | PASS |
| Complejidad y duplicación nuevas | PASS |
| Build y smoke del paquete | PASS |
| Seguridad del cambio | PASS |
| QA final de software | PASS |
| Capacidad de producción | N/A — cambio funcional, prueba de carga real no habilitada |
| Impresión física hospitalaria | NO VERIFICADO — dispositivo no disponible en este entorno |

## Limitaciones y despliegue

Cerrar las versiones anteriores en todas las estaciones antes de iniciar 1.1.14: clientes anteriores no conocen la nueva fecha de activación. La primera apertura actualiza el esquema central y fija la activación; no mueve ese corte en aperturas posteriores. La generación automática aplica a los siguientes cierres. Las atenciones pendientes anteriores siguen participando en el cálculo de herencia, sin generar sus antiguos reportes.

El cambio se valida como entrega de software; no certifica la impresora física ni una instalación ya realizada en los equipos del hospital. Evidencia completa de ejecución local en `output/closure-final-tests.log`, `output/closure-build.log` y el XML de regresión.
