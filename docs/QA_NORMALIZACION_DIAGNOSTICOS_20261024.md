# Normalización y selección de diagnósticos — QA 24/10/2026

## IMPLEMENTACIÓN

Fuentes modificadas:

- `diagnosis_text.py` — normalización pura, correcciones ortográficas limitadas, expansión O/I, identidad de aprendizaje y presentación con punto final.
- `diagnosis_recommendations.py` — sugerencias de diagnóstico, equivalencias, unificación de variantes, vocabulario confiable y lectura compatible de asociaciones heredadas.
- `diagnosis_recommendation_store.py` — aprendizaje canónico, reprocesado incremental de datos anteriores, persistencia de revisión y lectura de conflictos.
- `diagnosis_recommendation_schema.py` — columna aditiva de revisión en observaciones e índice de recuperación. La preparación idempotente existente aplica el cambio al conectarse.
- `diagnosis_recommendation_ui.py` — selección de diagnósticos mientras se escribe; teclado, ratón, separadores y normalización previa al guardado.
- `diagnosis_recommendation_admin.py` — buscador con X, recuento, filtrado sin borrar filas y resolución explícita de conflictos heredados.

Pruebas añadidas: `tests/test_diagnosis_text.py`, `tests/test_diagnosis_completion.py`, `tests/test_diagnosis_admin_search.py`, `tests/test_diagnosis_normalization_store.py`. Actualizadas las expectativas de presentación en `tests/test_diagnosis_recommendation_runtime.py` y `tests/test_diagnosis_recommendation_window.py`.

Comportamiento comprobado:

- Los puntos de puntuación no crean diagnósticos diferentes. Los decimales, como 37.5, conservan su significado.
- `CEFALEA DE OI`, `DE O/I` y `DE O.I.` se convierten en `CEFALEA DE ORIGEN A INVESTIGAR`. El campo completo del recibo termina con un único punto, por ejemplo `ABORTO INCOMPLETO; CEFALEA DE ORIGEN A INVESTIGAR.`.
- Las variantes de aborto, sibilancias, broncoespasmo, hipertensión y artralgia mostradas por el usuario se unifican con la escritura corregida.
- Crisis hipertensiva general, tipo urgencia y tipo emergencia conservan tres identidades diferentes. Aborto completo e incompleto permanecen distintos.
- Las opciones de cefalea aparecen al escribir; elegir una sustituye únicamente el segmento activo y conserva los demás diagnósticos.
- Traumas, artralgias, artrosis, esguinces, fracturas, contusiones y luxaciones agrupan sufijos anatómicos simples para aprender. El recibo conserva la zona y lateralidad. Se conservan los subtipos y los calificadores clínicos; no se acorta una frase si hacerlo descartaría un modificador adicional.
- Las variantes históricas se reprocesan por lotes sin modificar los recibos, identificadores, conceptos ni importes históricos. Las observaciones escritas por un cliente anterior quedan identificadas con revisión 0 y pueden corregirse nuevamente.
- El buscador administrativo encuentra diagnósticos, equivalencias, asociaciones e ítems pendientes, sin distinguir mayúsculas ni acentos. El guardado conserva las filas ocultas.
- Las asociaciones heredadas contradictorias y las equivalencias acumuladas sobre el límite pueden abrirse y corregirse. Guardar exige resolverlas; no se descartan equivalencias silenciosamente.

Se mantienen el mínimo de recibos, los dos primeros diagnósticos distintos, los pesos por estado y especialidad, los permisos de administrador y la selección manual de ítems. No se modifican contabilidad, Admisión ni el motor de impresión.

## PRUEBAS

Unitarias: puntuación, O/I, ortografía conocida, correcciones de letras repetidas/transpuestas, diferencias clínicas, agrupación anatómica, casos vacíos, decimales, límites de equivalencias y conflictos.

Qt real: escritura, selección con Enter/flechas/ratón, Escape, X, retorno a escritura manual, segmentos múltiples, cancelación, búsqueda por equivalencias, filas ocultas, edición, guardado y presentación a 1280/1920 píxeles.

PostgreSQL desechable en localhost: preparación aditiva desde esquema anterior, idempotencia, SELECT/INSERT/UPDATE, reemplazo de evidencia, rollback, clientes distintos, reprocesado de evidencia antigua, RLS y conservación de recibos e ítems originales.

Regresiones relacionadas: documentos del recibo, estados, importes, materiales, importación Word, catálogo, cantidades y actualización.

Se reprodujeron las dos discrepancias originales de aprendizaje antes de corregirlas. La revisión adicional encontró y corrigió el reprocesado con clientes anteriores y la inaccesibilidad de configuraciones heredadas contradictorias o con demasiadas equivalencias.

## RESULTADOS

| Ejecución | PASS | FAIL | ERROR | SKIP |
|---|---:|---:|---:|---:|
| Suite del módulo y nuevas regresiones | 233 | 0 | 0 | 0 |
| Ventanas reales, workspace y cantidades | 49 | 0 | 0 | 0 |
| Documentos, importes, Word y actualizador | 111 | 0 | 0 | 0 |
| Total de las suites relacionadas ejecutadas | 393 | 0 | 0 | 0 |

La suite completa de todo el repositorio no se ejecutó en esta tarea. El total anterior corresponde a los módulos del alcance y sus regresiones relacionadas; no reutiliza el recuento de la entrega anterior.

El primer intento PostgreSQL estuvo bloqueado por el acceso TCP del sandbox. Se ejecutó la integración en localhost con el entorno de QA autorizado. Dos fallos del arnés de prueba —identidad sintética duplicada e intento de cambiar aislamiento después de una consulta— se corrigieron en las pruebas; no se presentan como fallos de la aplicación.

## COBERTURA

Medición real con Coverage.py y ramas, sobre la suite final de 233 pruebas:

| Ámbito | Líneas | Ramas |
|---|---:|---:|
| Funciones nuevas o modificadas | 99.05% (626/632) | 97.13% (169/174) |
| Módulo nuevo de texto | 100% | 100% |
| Funciones modificadas de persistencia | 100% | 100% |

El esquema SQL declarativo se comprobó mediante integración con PostgreSQL. La métrica diferencial por funciones no cuenta líneas internas del literal SQL como ramas Python.

## CALIDAD

- Ruff check: PASS en seis fuentes y seis archivos de pruebas del cambio.
- Ruff formatter: PASS en los doce archivos del alcance.
- Mypy: PASS, sin incidencias en las seis fuentes.
- Bandit: PASS en las seis fuentes.
- Radon: complejidad máxima 9; todas las funciones del alcance permanecen dentro del objetivo 10.
- Pylint duplicate-code: cero bloques antes y después en los módulos del alcance; ninguna duplicación nueva detectada con umbral de seis líneas.
- `git diff --check`: PASS.

Los diagnósticos globales de lint heredados fuera de estos módulos no se han corregido ni se presentan como resueltos.

## BUILD

PASS. Se reutilizó el grafo de dependencias verificado del paquete 1.3.0 y se recompilaron PYZ, ejecutables y distribución, incorporando explícitamente el módulo nuevo `diagnosis_text`:

`python -X utf8 -m PyInstaller --noconfirm --distpath output/recommendations-app --workpath output/diagnosis-v131-collect output/diagnosis-v131-collect.spec`

Se comparó el código compilado de nueve módulos con las fuentes finales: PASS. Los imports nuevos se limitan a componentes ya empaquetados de Qt y biblioteca estándar.

Un intento inicial sin la ruta explícita de navegadores falló al localizar Chromium; el build usa los recursos Playwright instalados. Un análisis completo posterior se interrumpió antes del empaquetado durante la última revisión. La distribución aprobada es la recompilación final comprobada por código compilado y smoke, no esos intentos.

## QA

- Seis smoke checks del ejecutable local: PASS — lanzador, actualizador, paquete V15, PDF, visor e informes.
- Capturas nativas inspeccionadas: `output/diagnosis-completion-popup.png` y `output/diagnosis-v131-admin-search.png`.
- Seguridad: SQL parametrizado; permisos administrativos conservados; RLS conservado; pruebas con perfil sintético y PostgreSQL desechable. No se ejecutó QA destructivo ni SQL manual contra producción.
- Cuatro pasadas: funcionalidad PASS; regresión PASS; clean code PASS; QA PASS.

## QUALITY GATES

| Gate aplicable al cambio | Resultado |
|---|---|
| Funcionalidad | PASS |
| Unitarias, integración, regresiones y límites | PASS |
| Cobertura general y objetivo crítico | PASS |
| Formatter, lint, tipos y análisis estático del alcance | PASS |
| Complejidad y duplicación | PASS |
| Build y coincidencia de código compilado | PASS |
| Smoke y seguridad focalizada | PASS |
| QA final local | PASS |
| Publicación y comprobación en estaciones productivas | N/A — esta entrega prepara los cambios locales |

## PROBLEMAS PENDIENTES

- La ortografía no incluida en el vocabulario o equivalencias puede requerir confirmación del facturador o configuración administrativa. No se infiere automáticamente un subtipo clínico diferente.
- La entrega local fue seguida por la publicación estable 1.3.1; ver `RELEASE_1.3.1_QA.md`. La comprobación en estaciones físicas queda pendiente. La versión 1.3.0 conserva sus artefactos originales.

## EVIDENCIA

`output/diagnosis-v131-complete.xml`, `diagnosis-v131-window.xml`, `diagnosis-v131-regression.xml`, `diagnosis-v131-coverage.json`, `diagnosis-v131-quality.json`, `diagnosis-v131-duplication-before.log`, `diagnosis-v131-duplication-after.log`, `diagnosis-v131-build-final.log`, `diagnosis-v131-smoke.json` y las capturas citadas.

ESTADO FINAL: APROBADO PARA ENTREGA
