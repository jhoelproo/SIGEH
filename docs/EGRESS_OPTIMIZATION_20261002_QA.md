# Optimización de transferencia — 2 de octubre de 2026

## IMPLEMENTACIÓN

- `admission_turn_row_cache.py`: caché en memoria de un único turno, protegida por lock. Cada refresco consulta identificadores y huellas calculadas centralmente sobre la fila completa. Solo descarga filas nuevas o modificadas. Una edición de JSON sin incremento de versión también invalida la huella. El resultado mantiene el orden central y devuelve copias independientes. Borrados, cambios de turno y errores no se convierten en confirmaciones incorrectas.
- `admission_v15_adapter.py`: el listado y resumen del turno usan esta lectura incremental. Se conservan los campos completos necesarios para edición, Excel y reportes, las filas locales pendientes y las reglas de identidad. Una falla central sigue el manejo existente de desconexión; la caché no se presenta como confirmación central cuando falla la consulta de huellas.
- `CALCULOS_QT.py`: el resumen de facturación selecciona explícitamente los 29 campos utilizados por `AdmissionAttention`, sin recuperar el JSON clínico completo.
- `transfer_budget.py`: valor predeterminado de tres estaciones, confirmado por el usuario. Cuota orientativa por estación: 1.666.666.666 bytes por ciclo. Se mantienen las configuraciones explícitas existentes y las alertas al 50 %, 70 % y 85 %. Las operaciones de atención y facturación continúan por encima del presupuesto.
- Pruebas nuevas: `tests/test_admission_turn_row_cache.py` y `tests/test_admission_turn_row_cache_postgres.py`.
- Pruebas adaptadas/ampliadas: historial V15, dataset actual, estabilidad de conteos, resumen de facturación y presupuesto.

No se ejecutaron escrituras, eliminación de históricos, cambios de esquema o QA destructivo contra producción. Los cambios previos para vincular recibos permanecen en el workspace y forman parte del paquete de evaluación.

## PRUEBAS

La primera ejecución de las pruebas nuevas falló al no existir todavía el módulo; tras implementarlo pasaron. La regresión del dataset detectó un fake SQL que no devolvía las nuevas huellas: se actualizó para representar la nueva consulta y se mantuvo la comprobación de que una edición central prevalece sobre la copia local antigua.

Se prueban: cero y una fila; inserción, edición, eliminación y orden; modificación de JSON sin cambio de versión; cambio de fuente/turno; error en cabeceras y descarga; reintento de filas desaparecidas entre consultas; independencia de copias; refrescos simultáneos; parámetros sin interpolación; turno inválido; conteos, Excel, turnos vacíos, desconexión y sincronización incremental existente.

Las pruebas PostgreSQL crean un servidor desechable en loopback y una tabla temporal con pacientes sintéticos. Nunca tienen fallback a producción.

## RESULTADOS Y MEDICIÓN

- Prueba específica PostgreSQL y caché: **12 pruebas disponibles**; resultados finales en XML de regresión y `output/egress-cache-postgres.xml` (la primera pasada tenía 10, antes de agregar dos casos unitarios).
- Suite relacionada inicial: **126 passed, 0 failed, 0 skipped** (`output/egress-cache-related.xml`).
- Presupuesto, facturación e integración inicial: **62 passed, 0 failed, 0 skipped** (`output/egress-budget-summary.xml`).
- Pruebas focalizadas finales con cobertura: **110 passed, 0 failed, 0 skipped** (`output/egress-cache-final-focused.xml`).
- Regresión completa: **1.835 passed, 60 subtests passed, 0 failed, 1 skipped**. Diez procesos/grupos terminaron con código de salida 0. La única omisión final es el dry-run de capacidad real que requiere habilitación explícita, ajeno a esta optimización. Evidencia: `output/egress-cache-regression-summary.json`, `output/egress-cache-regression-groups.json` y XML/logs de los diez grupos.

Las primeras pasadas omitieron algunas integraciones por estar apagado el servidor local de QA. El cluster temporal antiguo tampoco arrancó por faltar un directorio interno. Se creó un cluster desechable nuevo en loopback, se repitieron los cuatro grupos afectados y esas integraciones pasaron. Las omisiones iniciales no se contabilizan como PASS.

Comparación con las mismas 100 filas sintéticas, detalle de 20 KB y 30 refrescos sin cambios, ejecutando SQL real en PostgreSQL local:

| Medida | Lectura completa | Lectura incremental |
|---|---:|---:|
| Payload JSON recibido | 60.464.520 bytes | 2.246.244 bytes |
| Filas recibidas | 3.000 completas | 100 completas + 3.000 cabeceras |
| Reducción de payload | — | **96,285 %** |

El dataset devuelto fue idéntico en los 30 refrescos. Evidencia: `output/egress-turn-cache-comparison.json`. El número de cabeceras aumenta; el objetivo es reducir bytes, no fingir que desaparecen las comprobaciones de cambios. Esta medición no incluye protocolo/TLS y no equivale a egress mensual facturado.

Comprobación agregada de solo lectura sobre el turno activo en producción: 71 filas, 204.148 bytes de representación JSON completa y 5.396 bytes de cabeceras (aproximadamente 97,36 % menos por refresco sin cambios). No se descargaron nombres, identidades ni detalles clínicos. Las huellas necesitan leer los registros en el servidor; el costo de CPU debe observarse en operación real.

## COBERTURA

Herramienta: coverage.py 7.15.4 con `--branch`. Evidencia: `output/egress-cache-coverage-final.json`.

- Nuevo módulo de caché: **42/42 líneas; 6/6 ramas: 100 % / 100 %**.
- `transfer_budget.py`: **161/161 líneas; 40/40 ramas: 100 % / 100 %**.
- Función de resumen de facturación: **39/39 líneas; 12/12 ramas: 100 % / 100 %**.
- Constructor del proxy y cargador del turno: **9/9 y 3/3 líneas**, sin ramas, 100 %. No se usa el porcentaje global de módulos heredados para atribuir cobertura al código nuevo.

## CALIDAD

- Ruff: PASS en módulos nuevos, presupuesto y pruebas nuevas. Análisis diferencial real de los módulos principales: `CALCULOS_QT.py` conserva 327 diagnósticos previos, sin nuevos; `admission_v15_adapter.py`, cero. El test heredado del resumen conserva un E402 preexistente, sin nuevos. Se normalizan referencias a números de línea desplazados, sin ocultar clases de errores.
- Mypy: PASS en caché y presupuesto. Diferencial: principal 969 → 969 errores existentes; adaptador 42 → 42, cero nuevos. Evidencia: `output/egress-cache-types-diff.json`.
- Formatter Ruff: PASS en módulos pequeños y pruebas nuevas. Las convenciones de los archivos heredados se preservan; no se reformatea masivamente código ajeno a la tarea.
- Radon: complejidad máxima del código de caché **7**; función de configuración de presupuesto **4**. No se incrementa complejidad de las funciones principales integradas.
- Pylint duplicate-code, mínimo seis líneas: PASS, sin hallazgos en los cuatro módulos de producción revisados (`output/egress-cache-duplication.txt`).
- Bandit: PASS en caché y presupuesto, sin hallazgos (`output/egress-cache-security.json`). Las identidades permanecen parametrizadas; no se modifican autorización ni RLS.
- `py_compile` y `git diff --check`: PASS.

## BUILD

Comando ejecutado:

```powershell
python -m PyInstaller --noconfirm --log-level WARN --distpath output/egress-cache-app --workpath build/egress-cache-app build_app.spec
```

Resultado: **PASS, exit code 0**. Se conservan advertencias de imports opcionales mypyc. Log: `output/egress-cache-build.log`. Paquete de evaluación: `output/egress-cache-app/SIGEH`; sin publicación de release ni actualización automática de los equipos.

Se verificó que caché, adaptador, presupuesto y módulo principal empaquetados coinciden con las fuentes finales mediante comparación de bytecode, nombres y constantes: **4/4 PASS**, `output/egress-cache-package-modules.json`.

## QA

Smoke del ejecutable construido, esperando la terminación de cada proceso: creación de PDF, motor gráfico de reportes y generación de reportes: **3/3 exit code 0**. Evidencia: `output/egress-cache-smoke.json`.

Las cuatro pasadas finales cubren funcionalidad, regresiones, responsabilidades/duplicación y QA. La mejora se verifica en laboratorio; consumo mensual real después de instalar en las tres estaciones: **NO VERIFICADO**. Tampoco se reduce retroactivamente el consumo ya registrado ni los 456 MB de tamaño de base observados en las capturas.

## QUALITY GATES

| Gate | Estado | Evidencia / alcance |
|---|---|---|
| Funcionalidad | PASS | Dataset equivalente; cambios, borrados, identidad y errores |
| Unit tests | PASS | Nuevas reglas y presupuesto |
| Integración | PASS | SQL real en PostgreSQL local desechable |
| Regresión | PASS | 1.835 pruebas y 60 subpruebas, cero fallos |
| Cobertura | PASS | 100 % líneas/ramas en caché, presupuesto y resumen modificado |
| Boundary tests | PASS | Vacío, una fila, turnos inválidos, errores, reintentos y concurrencia |
| Formatter | PASS | Módulos nuevos y regiones modificadas; sin reformateo masivo |
| Lint | PASS | Cero diagnósticos nuevos; deuda heredada declarada |
| Static analysis | PASS | Ruff, py_compile, Bandit y revisión manual |
| Type checker | PASS | Módulos pequeños limpios; diferencial principal/adaptador cero nuevos |
| Complejidad | PASS | Máxima nueva 7; configuración 4 |
| Duplicación | PASS | Cero hallazgos de duplicate-code en módulos revisados; no se afirma porcentaje global |
| Build | PASS | PyInstaller exit 0; cuatro módulos coinciden con fuentes finales |
| Smoke test | PASS | Tres checks del ejecutable, exit 0 |
| Seguridad | PASS | Parámetros, lock y aislamiento de QA; cero hallazgos Bandit nuevos |
| QA de laboratorio | PASS | Cuatro pasadas y regresión completa |
| Dry-run opcional de capacidad real | N/A | Requiere habilitación explícita; no es necesario para el cambio de lectura |
| Comparativa de jornada normal en tres equipos | NO VERIFICADO | El usuario autorizó publicar para medir después de instalar; no es bloqueo previo al despliegue |
| QA final para producción | PASS | Validaciones locales y paquete 1.2.6; publicación de prueba expresamente autorizada |

**ESTADO FINAL: APROBADO PARA ENTREGA**. El usuario autorizó aplicar la versión para observar la reducción real. El consumo mensual queda pendiente de medición y no se garantiza la cuota de 5 GB.

## PROBLEMAS PENDIENTES

- Comparar consumo inicial/final y exportaciones locales de las tres estaciones durante una jornada normal después de instalar el paquete. Alertas locales son estimaciones; el panel de Supabase sigue siendo la medición de transferencia facturada.
- Si existe `budget.json` con otro número de estaciones, su configuración explícita prevalece: usar `stations: 3` para este hospital. El día de corte se mantiene configurable.
- La capacidad de la base requiere un trabajo separado de preservación/archivo de documentos y mantenimiento; este cambio no borra información para bajar el porcentaje.
- No se puede garantizar una cuota mensual de 5 GB con un escenario de laboratorio ni con dos días de capturas.


## Publicación 1.2.6

- Build de aplicación y actualizador: PASS, PyInstaller, exit 0; logs `output/egress-release-*-build.log`.
- Pruebas de versión/empaquetado/actualización: 23 PASS; lanzador y actualización visual: 33 PASS. Dos expectativas de versión fallaron primero y se actualizaron al incremento 1.2.6; posterior ejecución sin fallos.
- Cinco módulos empaquetados coinciden con las fuentes: PASS.
- PDF, visor y reportes del ejecutable final: tres checks PASS, exit 0.
- ZIP, hashes de archivos, versiones y lanzador/actualizador: evidencia `output/egress-release-zip-verification.json`.
