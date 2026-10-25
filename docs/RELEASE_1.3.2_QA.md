# SIGEH 1.3.2 — Diagnósticos y recomendaciones sin bloqueos

## IMPLEMENTACIÓN

- `diagnosis_recommendations.py`: las sugerencias proceden del catálogo base y de la configuración administrativa, con descarte de opciones incompletas, compuestas o con variantes cercanas mal escritas. El texto histórico sin revisar se conserva para aprendizaje, pero no se promociona automáticamente al autocompletado. Se reutilizan los índices y resultados de búsqueda.
- `diagnosis_recommendation_ui.py`: un catálogo por modelo; la validación, reconstrucción del índice y persistencia de caché de las sincronizaciones se ejecutan en el trabajador. La interfaz recibe el modelo preparado. Los resultados cancelados no sobrescriben la caché ni la ventana.
- `diagnosis_recommendation_admin.py`: preparación de datos en segundo plano, carga de hasta 25 filas y presupuesto de 8 ms por lote, señales bloqueadas durante inserciones, modelos de opciones compartidos e índice de búsqueda reutilizable. La lectura se puede cancelar sin esperar en la interfaz y se descartan resultados tardíos. El guardado conserva su protección contra cierre intermedio y operaciones duplicadas.
- `sigeh_product.py`, `version_config.json`: versión 1.3.2.
- Pruebas: nuevo `tests/test_diagnosis_responsiveness.py`; ampliado `tests/test_diagnosis_recommendation_runtime.py`; actualizado `tests/test_sigeh_update.py`.
- Notas de versión: `RELEASE_1.3.2.md`.

Se mantienen recibos, conceptos, importes, identidades, configuración local, permisos y asociaciones. No se modifican Admisión, impresión ni esquema SQL. Las elecciones del facturador siguen siendo manuales.

## PRUEBAS

Los cinco casos iniciales reprodujeron los fallos antes de corregirlos: sugerencias históricas incorrectas, promoción de texto no revisado, reconstrucción repetida del catálogo, filtrado por cada celda y bloqueo durante la carga grande.

Regresiones de texto: ejemplos enviados de cefalea, variantes de origen a investigar, casos incompletos y compuestos, nombres administrativos válidos, equivalencias, grados y crisis hipertensivas distintas, límites y aislamiento de la caché de búsquedas.

Qt: selección, teclado, ratón, X, escritura manual, foco, filtrado, filas ocultas conservadas, carga vacía, carga de 800 diagnósticos, límite de tiempo por lote, cierre con X, cancelación y salida, resultados tardíos, fallo de conexión, guardado y prevención de duplicados. Se comprueba el hilo que valida, crea el índice y escribe la caché.

Integración PostgreSQL desechable: aprendizaje, permisos, sincronización por cambios, dos clientes, persistencia, rollback y conservación del historial. Regresión adicional de importes, materiales, documentos, importación Word y actualizador.

## RESULTADOS

| Suite final | PASS | FAIL | ERROR | SKIP |
|---|---:|---:|---:|---:|
| Módulo y nuevas regresiones | 239 | 0 | 0 | 0 |
| Ventanas, PostgreSQL y cantidades | 46 | 0 | 0 | 0 |
| Actualizador, importes, documentos y Word | 180 | 0 | 0 | 0 |
| Total de casos distintos | 465 | 0 | 0 | 0 |

La suite completa de todo el repositorio no se repitió; el total corresponde a los módulos del alcance y sus regresiones relacionadas. Se conserva el aviso heredado de deprecación de PyPDF2.

Un primer intento ubicó una prueba SQLite en OneDrive y fue rechazado por la protección de almacenamiento privado. Se repitió con perfiles temporales locales; no se modificó ni debilitó esa protección. Una regresión detectó comparación difusa durante la preparación ordinaria del catálogo; se sustituyó por una comprobación limitada de una edición y se repitieron las suites finales.

## RENDIMIENTO

Comparación local con las fuentes publicadas en `v1.3.1`, usando datos sintéticos y Qt nativo. Son tiempos del entorno de QA; la consulta después del cambio usa el índice ya preparado en memoria.

| Medición | 1.3.1 | 1.3.2 |
|---|---:|---:|
| Consulta de sugerencias, 5.000 grupos, mediana | 440.33 ms | 0.014 ms |
| Consulta de sugerencias, percentil 95 | 459.05 ms | 0.053 ms |
| Primera llamada GUI, 401 diagnósticos y 75 asociaciones | 3.804,84 ms | 3,88 ms |
| Mayor intervalo entre eventos de la ventana | 3.805,99 ms | 26,94 ms |
| Carga GUI completa | 3.815,51 ms | 349,45 ms |

Preparación del índice: 423,51 ms una vez por modelo; preparación administrativa: 96,39 ms. Durante las sincronizaciones y la recarga administrativa esas tareas se ejecutan fuera del hilo de interfaz. Se conservaron las 401 filas de diagnósticos y las 75 asociaciones. Gates de rendimiento de QA: consulta p95 < 25 ms, primer lote < 30 ms, intervalo de eventos < 75 ms y filas preservadas: PASS.

## COBERTURA

Coverage.py con ramas, combinado sobre las suites del módulo e integración; medición por funciones nuevas o modificadas frente a `v1.3.1`:

- Líneas: 99,44% — 536/539.
- Ramas: 98,10% — 155/158.
- Normalización e índice de sugerencias modificados: 100% líneas y ramas.
- El objetivo crítico de 95% líneas y 90% ramas se cumple. La actualización adicional de versión no introduce ramas.

## CALIDAD

- Formatter y Ruff: PASS en siete fuentes/pruebas modificadas.
- Mypy: PASS en cuatro fuentes.
- Bandit: PASS en las tres fuentes con cambios funcionales; la identidad de producto solo cambia su constante de versión.
- Radon: complejidad máxima 9 en los módulos del alcance.
- Pylint duplicate-code, umbral de seis líneas: cero bloques antes y después; ninguna duplicación nueva detectada.
- `git diff --check`: PASS. No se redujeron thresholds ni se añadieron supresiones.

Los problemas de lint generales heredados fuera del alcance permanecen fuera de esta entrega.

## BUILD

PASS:

`python -X utf8 -m PyInstaller --noconfirm --distpath output/recommendations-app --workpath output/diagnosis-v131-collect output/diagnosis-v131-collect.spec`

`python -X utf8 -m PyInstaller --noconfirm --distpath output/release-132-updater --workpath output/release-132-updater-build build_updater.spec`

Se reutiliza el grafo verificado de dependencias, se recompilan ejecutables y módulos y se conserva el recurso SQL de la distribución anterior. El código empaquetado de 29 entradas coincide con las fuentes finales.

## QA

- ZIP exacto: 1.823 archivos comprobados por tamaño, SHA-256 y CRC; conserva todas las rutas del paquete 1.3.1. No contiene credenciales, bases operacionales ni archivos temporales.
- Seis smoke checks del ZIP final: lanzador, ayuda del actualizador, paquete V15, PDF, visor e informes: PASS.
- Actualización con el actualizador real de 1.3.1 al paquete 1.3.2: PASS. Ocho archivos sintéticos preservados por hash; configuración del hospital, horarios, logo, usuarios, conexión protegida e historial. Las dos bases conservan sus dos registros y el health check real pasa. Solo se captura el reinicio del lanzador para evitar una sesión real.
- Capturas nativas inspeccionadas: `output/diagnosis-responsive-admin.png` y `output/recommendations-admin-rules.png`. Se verificó texto legible y controles en el tema de la aplicación. La primera captura del arnés sin fuente explícita se sustituyó tras registrar Segoe UI; no se cambió la tipografía de producción.
- Seguridad: permisos administrativos y validación en operaciones conservados. Sin QA destructivo ni conexión de prueba contra producción.
- Cuatro pasadas: funcionalidad PASS; regresión PASS; clean code PASS; QA PASS.

## PUBLICACIÓN

SHA-256 del ZIP: `594fb91de049f881475013d0d69e7eebd4b6198cbb44e0f418010c777cdb0bd0`.

Código de entrega: `7a43028377467954e2962746f4f501d4b947e3ef`, rama `codex/release-1.3.2`, etiqueta `v1.3.2`.

Publicada como estable y latest: [SIGEH 1.3.2](https://github.com/jhoelproo/SIGEH/releases/tag/v1.3.2). Fecha de GitHub: `2026-10-25T01:16:31Z` (24/10/2026, 21:16 en America/La_Paz).

Comprobación remota: PASS. Los cuatro assets están subidos y sus tamaños y SHA-256 calculados por GitHub coinciden con los locales. Los manifiestos y checksum se descargaron y compararon byte por byte. El cliente real de actualizaciones obtuvo 1.3.2 desde el canal estable, validó el manifiesto y checksum y la reconoce como posterior a 1.3.1. No se descargó nuevamente el ZIP completo: su digest de GitHub se contrastó con el ZIP local verificado.

## QUALITY GATES

| Gate aplicable | Resultado |
|---|---|
| Funcionalidad, unitarias, integración, regresión y límites | PASS |
| Cobertura y objetivo crítico | PASS |
| Formatter, lint, tipos y análisis estático del cambio | PASS |
| Complejidad y duplicación | PASS |
| Build y correspondencia de código/recursos | PASS |
| Rendimiento y respuesta de eventos | PASS |
| Smoke, preservación en actualización y seguridad focalizada | PASS |
| Publicación estable y cliente de actualizaciones | PASS |
| Impresora y estaciones físicas del hospital | N/A — comprobación operativa fuera del entorno de entrega |

## PROBLEMAS PENDIENTES

La versión está disponible para actualizar. La instalación efectiva en cada equipo del hospital no se realiza desde este entorno. Las definiciones personalizadas continúan bajo configuración administrativa; el historial no se convierte por sí solo en un catálogo revisado.

## EVIDENCIA

`output/diagnosis-responsive-red.xml`, `diagnosis-responsive-final.xml`, `diagnosis-responsive-integration.xml`, `release-132-regression.xml`, `diagnosis-responsive-combined-coverage.json`, `diagnosis-responsive-quality.json`, `diagnosis-responsive-complexity.json`, `diagnosis-responsive-duplication-before.log`, `diagnosis-responsive-duplication-after.log`, `diagnosis-responsive-benchmark.json`, `release-132-build.log`, `release-132-updater-build.log`, `release-132-packaging.log`, `release-132-package-validation.json`, `release-132-validation.log`, `release-132-draft-verification.json`, `release-132-publication-verification.json` y los cuatro artefactos de `output/release-1.3.2/`.

ESTADO FINAL: APROBADO PARA ENTREGA
