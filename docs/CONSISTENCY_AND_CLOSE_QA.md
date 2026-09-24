# Consistencia de pacientes, precios y cierre cuantitativo

Validación del 24 de septiembre de 2026, sobre `42a405d` (optimización de sincronización), con base publicada SIGEH 1.2.2. Versión candidata: 1.2.3. El usuario confirmó que realizó las comprobaciones previas en el hospital y autorizó publicar al completar estos cambios.

## A. Precio

Se reprodujo el error: convertir RD$ 6.00 al costo base con recargo de 35% redondeaba `6 / 1.35` a `4.44`; volver a aplicar el recargo daba `5.994`, mostrado como `5.99`. La prueba previa falló en cinco casos. No era suficiente cambiar la presentación del número.

`billing_money.py` calcula con Decimal desde texto, conserva 12 decimales en el costo intermedio y redondea a centavos al obtener el precio efectivo. Se usan sumas decimales para subtotales y total del carrito. La migración cambia los campos monetarios de REAL a NUMERIC y conserva sus valores; no sustituye arbitrariamente precios 5.99. Una prueba PostgreSQL guarda y reabre 6.00, 5.99, 0.10 y 999999.99, además de sus subtotales por tres unidades.

## B. Datos del paciente

La reconstrucción de la hoja consultaba el último evento, aunque la proyección ya conservaba `latest_payload_json`. Después de compactar eventos podía perder edad, teléfono y dirección. El resolvedor ahora usa primero el snapshot persistente y conserva el evento como compatibilidad cuando no existe ese snapshot. Se comprueban años, meses y días, teléfono, dirección y embarazo.

El maestro existente permite editar nombre, cédula, NSS, teléfono, dirección, nacionalidad y ARS. Edad/unidad/sexo/embarazo pertenecen al snapshot de la atención y se conservan; esta tarea no inventa campos nuevos en el editor del maestro.

## C. Propagación de siete días

La edición utiliza `global_patient_id`, no el nombre ni los identificadores que están siendo corregidos. La ventana es inclusiva entre la hora de edición menos siete días y la hora de edición. Las pruebas cubren mismo día, un día, siete días exactos, siete días más un microsegundo, fechas futuras y otro paciente.

Maestro, auditoría, proyección y eventos incrementales se actualizan dentro de la misma transacción. Una prueba provoca un error después de actualizar el maestro y comprueba que todo se revierte. Se conserva la identidad de atención, turno, fuente, generación y datos clínicos; los recibos no se reescriben. La adquisición de bloqueos sigue el orden atención antes que maestro para evitar invertir el orden de la sincronización.

## D. Baseline

`billing_reporting_policy.baseline_at` conserva `2026-09-23T00:00:00-04:00`, correspondiente a America/Santo_Domingo. Se crea una sola vez; reinstalar no lo reinicia. `enabled_at` distingue la activación del nuevo modelo de los cierres antiguos.

Se excluye del pendiente inicial el backlog anterior al baseline. No se borran admisiones, recibos ni historia. Una prueba con 500 atenciones prebaseline verifica cero pendientes y conserva las 500 entradas.

## E. Recibos

La autoridad es `recibos` en PostgreSQL, que contiene la versión efectiva del recibo, no la tabla de revisiones. Un recibo persistido válido, incluso PRELIMINAR, resuelve la atención vinculada. Un claim o una autorización escrita sin guardar no la resuelve. Se excluyen recibos eliminados, anulados, cancelados o inválidos según sus estados; se utiliza la ventana temporal del turno y todas las estaciones.

## F. Históricos

Los recibos guardados durante el turno sin vínculo canónico válido se cuentan como históricos sin vínculo. Los que tienen autorización de al menos cuatro dígitos forman un subconjunto. No se suman dos veces ni se enlazan por nombre/NSS. Los recibos vinculados a una atención excluida no se consideran trabajo facturado en este cierre.

## G. Pendientes

El cálculo separa pendientes del turno actual, pendientes del turno inmediatamente anterior y pendientes históricas. Un recibo central vinculado descuenta el pendiente aunque se haya guardado desde otra estación. Los recibos anteriores al turno también resuelven el pendiente, pero no se vuelven a contar como trabajo del turno actual.

## H. ARS y exportación

Tarjetas, Control por ARS, PDF y Excel proceden de la misma captura. No se consulta de nuevo el estado actual para reconstruir un cierre. La prueba integral con 50 recibos de RD$ 6.00 verifica 50 recibos y RD$ 300.00 en la captura, la lectura y Excel. El Excel cuantitativo omite las listas técnicas de IDs.

## I. Idempotencia

La captura central se ejecuta en la transacción que confirma `PRIMARY_USER_HANDOFF`, con `transition_id` único y clave fuente/turno. Una prueba con diez reintentos y dos conexiones conserva una captura y un registro del relevo. Otra comprueba que una transición distinta no puede reemplazar el cierre. Un fallo de captura revierte la transacción; un fallo posterior de PDF se reintenta como efecto documental.

Se conservan las protecciones existentes de revisión esperada, generación, turno anterior y recuperación de la respuesta perdida. Los registros de relevo antiguos sin identificador siguen siendo compatibles. Las filas del nuevo snapshot no admiten UPDATE ni DELETE.

## J. PDF

El nuevo modelo muestra cantidades, movimiento de pendientes e importes; no imprime pacientes individuales. Un caso con 5.000 pendientes generó dos páginas. Los reportes anteriores conservan su versión lógica y sus datos históricos.

## K. Validación

La ejecución completa inicial terminó con 1.710 pruebas y 60 subpruebas aprobadas. Después de los ajustes finales, dos ejecuciones conjuntas abortaron en un hilo Qt del módulo de importación. Ese módulo se ejecutó en un proceso independiente: 8/8 PASS. El resto terminó con 1.706 PASS, dos fallos y una omisión. Los fallos identificaron la versión interna aún en 1.2.2 y el doble de prueba del diálogo sin el nuevo estado de envío. Ambos se corrigieron; la regresión posterior de actualización, diálogo, carrito y paquete terminó con **64/64 PASS**.

Consolidando por identidad de prueba la última ejecución de cada caso: **1.724 PASS, 0 FAIL, 1 SKIPPED**; además, 60 subpruebas de la suite fueron aprobadas. La omisión es la prueba opcional `RUN_REAL_CAPACITY_INTEGRATION`, que usa una conexión provisionada expresamente y no pertenece al comportamiento modificado. No se ocultan los abortos ni se cuentan como pasadas las ejecuciones incompletas. Evidencia: `output/consistency-final-suite.xml`, `consistency-import-suite.xml`, `consistency-final-regressions.xml` y sus registros.

Se usó PostgreSQL 17 desechable en loopback, con bases de prueba independientes; ninguna escritura de QA se hizo contra producción. Se comprobaron INSERT, SELECT, UPDATE, rollback, unicidad, concurrencia de dos conexiones, diez reintentos, inmutabilidad, reinstalación y ausencia de filas. El test de propagación comprueba que el maestro y las atenciones revierten juntos ante error.

| Regla funcional | Resultado |
|---|---|
| Backlog prebaseline excluido sin borrar las 500 entradas | PASS |
| Nuevas pendientes pasan al turno siguiente | PASS |
| Recibos centrales de otras estaciones reducen pendientes | PASS |
| Históricos sin vínculo y autorización como subconjunto | PASS |
| Recibo vinculado resuelve; claim no resuelve | PASS |
| Atención excluida no pendiente ni facturada | PASS |
| 50 recibos producen 50 y RD$ 300, no cero | PASS |
| Doble clic / diez clics / dos conexiones | PASS |
| Recuperación de respuesta perdida conserva transición | PASS |
| Fallo posterior de PDF no habilita otro relevo | PASS |
| 6.00 y 5.99 conservados en catálogo, PostgreSQL y carrito Qt | PASS |
| Edad, teléfono y dirección recuperados del snapshot | PASS |
| Correcciones mismo día, un día y siete días inclusivos | PASS |
| Más de siete días, otro paciente y recibos intactos | PASS |

### Cobertura y calidad

Medición real con `coverage.py --branch`, combinando las ejecuciones separadas. En líneas ejecutables agregadas/modificadas: **334/339 (98,53%)**; ramas: **85/88 (96,59%)**. El modelo central de cierre y dinero tienen **100% de líneas y ramas**; los seis módulos nuevos tienen 100% de líneas, con una rama defensiva pendiente en la relectura concurrente de datos demográficos. Las otras rutas no cubiertas están en integración heredada: error por snapshot ausente, serialización final de totales del formulario y guardia redundante de reentrada. Evidencia: `output/consistency-final-coverage.json` y `output/consistency-changed-coverage.json`.

- Formatter: PASS en módulos y pruebas nuevos; `git diff --check`: PASS. Se conserva el formato del código heredado.
- Ruff: PASS en código y pruebas nuevos. Diferencial contra HEAD: cero errores nuevos. Los avisos heredados siguen presentes (327 en `CALCULOS_QT.py`, 85 en V15 y uno en el renderizador).
- Mypy: PASS en los seis módulos nuevos, con `--check-untyped-defs --follow-imports=silent`. El tipado integral del sistema heredado no se declaró limpio.
- Bandit: PASS en seis módulos nuevos. El SQL señalado inicialmente se reemplazó por SQL literal; comprobación repetida sin hallazgos.
- Radon: complejidad máxima nueva 8. Se preservan las funciones extensas heredadas fuera del alcance; no se declara que todo el repositorio tenga complejidad ≤10.
- Pylint `duplicate-code --min-similarity-lines=6`: PASS en seis módulos nuevos. Es detección de duplicación nueva, no una estimación porcentual del repositorio entero.

### Build y QA

Comandos:

```text
python -m PyInstaller --noconfirm --distpath output/consistency-release-app --workpath build/consistency-release-app build_app.spec
python -m PyInstaller --noconfirm --distpath output/consistency-release-updater --workpath build/consistency-release-updater build_updater.spec
python release_packaging.py --dist output/consistency-release-app/SIGEH --updater output/consistency-release-updater/SIGEH_Updater.exe --output output/release-1.2.3 --version 1.2.3
```

**Build final: PASS**, tanto aplicación como actualizador. El paquete final reconstruido pasó PDF, visor gráfico, PDF/Excel de reportes, exportación de consumo y carga V15 (todos devolvieron 0). El lanzador sin conexión configurada devolvió 5 (CONFIGURATION_MISSING); con configuración sintética local devolvió 0. No se insertaron credenciales en el paquete público. Se comprobó el SHA-256 del ZIP y de todos sus archivos al extraerlo en una carpeta nueva. Evidencia: `output/consistency-build-app-final.log`, `output/consistency-build-updater-final.log`, `output/consistency-final-smoke/results.json`.

ZIP: `SIGEH-1.2.3-windows-x64.zip`. SHA-256: `ba92aa7a71342292c71f2619e911e818e6fdb9b5ccf332421cded9da63b69bbb`.

Se inspeccionaron visualmente las dos páginas del cierre con 5.000 pendientes: tarjetas completas, tabla de movimientos, ARS y gráficos sin listados de pacientes ni cortes. No se probó una impresora física; la generación y apertura del documento sí se ejecutaron.

Las cuatro pasadas revisaron: requisitos e identidades; regresiones de recibos, turnos y reportes; responsabilidades, bloqueos, SQL y duplicación; pruebas, cobertura, visor y paquete. La optimización previa conserva su comparación sintética de 90,5% menos payload, que no garantiza 5 GB mensuales.

### Archivos y comportamiento preservado

Código nuevo: `billing_money.py`, `admission_demographics.py`, `admission_handoff_ui.py`, `billing_close_model.py`, `billing_close_store.py`, `billing_close_report.py`.

Integración modificada: `CALCULOS_QT.py`, `patient_directory.py`, `admission_v15_adapter.py`, `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`, `report_documents.py`, `report_engine/html_renderer.py`, `report_engine/report_template.html`. Identidad de versión: `sigeh_product.py` y `version_config.json`. Se agregaron doce archivos de pruebas y se actualizaron cuatro pruebas existentes. Las notas de sincronización conservan la evidencia histórica y registran la confirmación del usuario.

Permanecen el historial central, los recibos antiguos, la identidad de paciente/atención/turno, los permisos, la lógica de reportes v1/v2 y los flujos no relacionados. La funcionalidad de extranjeros continúa fuera del alcance.

### Quality Gates finales

| Gate | Estado | Evidencia / alcance |
|---|---|---|
| Funcionalidad | PASS | Matriz de reglas anterior |
| Unit tests / condiciones de borde | PASS | Dinero, intervalos, categorías, estados e idempotencia |
| Integración | PASS | PostgreSQL real desechable, Qt, PDF y Excel |
| Regresión | PASS | Último resultado de 1.724 pruebas; módulos Qt aislados como se documenta |
| Cobertura | PASS | 98,53% líneas; 96,59% ramas modificadas |
| Formatter / lint | PASS | Formato nuevo; diferencial sin errores nuevos |
| Tipos / análisis estático | PASS | Módulos nuevos comprobados; legado fuera del tipado integral |
| Complejidad | PASS | Máximo 8 en módulos nuevos; legado preservado |
| Duplicación | PASS | Sin duplicación nueva detectada con el umbral indicado |
| Build | PASS | Aplicación y actualizador finales |
| Smoke | PASS | ZIP nuevo, hashes, lanzador configurado, visor, PDF/Excel, V15 y consumo |
| Seguridad aplicable | PASS | SQL parametrizado, snapshot inmutable, ausencia de PHI en log y de credenciales en ZIP |
| QA final | PASS | Cuatro pasadas y revisión visual de ambas páginas |
| Impresora física / QA productivo | N/A | No se cambia el controlador de impresión; no se usó producción para pruebas |

Limitaciones reales: la suite Qt monolítica abortó y se ejecutó por procesos separados; la prueba de capacidad opcional permanece omitida. No se midió una nueva jornada real ni se promete un máximo mensual de transferencia. El PostgreSQL de pruebas se detuvo al terminar.

**ESTADO FINAL: APROBADO PARA ENTREGA**.
