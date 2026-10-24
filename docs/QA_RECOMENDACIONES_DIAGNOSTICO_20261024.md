# QA — Recomendaciones por diagnóstico — 24/10/2026

ESTADO FINAL: APROBADO PARA ENTREGA

Alcance: implementación y validación local; sin publicación en producción.

## IMPLEMENTACIÓN

- Modificados: CALCULOS_QT.py y catalog_workspace_design.py.
- Nuevos: diagnosis_recommendations.py, diagnosis_recommendation_store.py, diagnosis_recommendation_schema.py, diagnosis_recommendation_ui.py y diagnosis_recommendation_admin.py.
- Migración aditiva: migrations/20261024170830_diagnosis_recommendations.sql, creada inicialmente mediante Supabase CLI. No se eliminaron ni reescribieron datos históricos.
- Pruebas nuevas: tests/test_diagnosis_recommendations.py, tests/test_diagnosis_recommendation_runtime.py, tests/test_diagnosis_recommendation_postgres.py y tests/test_diagnosis_recommendation_window.py.
- La activación inicial también funciona cuando la base pública ya es compatible: instala únicamente el esquema privado faltante, bajo bloqueo y comprobación posterior. No depende de repetir las migraciones históricas ni ejecuta DDL en el sondeo periódico.
- Sugerencias al principio del mismo catálogo, banda morada, diagnóstico y botón Añadir normal. Nunca se añaden solas.
- Aprende de recibos guardados PENDIENTE y FACTURADO; pesos predeterminados 1 y 2. Usa los dos primeros diagnósticos distintos y un medicamento principal por diagnóstico. Mínimo predeterminado: 10 recibos y frecuencia del 60%; hasta 3 laboratorios y 2 imágenes.
- General/Emergenciología, Pediatría y Ginecología se procesan por separado. Equivalencias conocidas se normalizan al generar; coincidencias dudosas requieren selección. No se crea un campo adicional con el texto original.
- Solo el administrador configura diagnósticos, abreviaturas, prioridades, recomendaciones, bloqueos y asociaciones ambiguas. Se comprueban usuario activo y rol en PostgreSQL al guardar, con auditoría y control de versión.
- Una edición reemplaza el aporte del recibo. Reimprimir o pulsar Añadir no entrena; enviar a papelera retira el aporte.
- Configuración y resúmenes compartidos por cambios, con copia local SQLite y reinicio de caché cuando cambia la generación central o retrocede su revisión. No se consultan recibos al escribir el diagnóstico. Restricciones del proveedor activan espera de 900 segundos mediante el control existente.
- Conservados: importes, tarifas, cantidades, materiales automáticos, honorarios por ARS, vínculos, impresión y reportes. Se mantuvieron los cambios locales anteriores de cantidad e importación Word.

Configuración: Opciones avanzadas → Recomendaciones por diagnóstico. El facturador solo ve las sugerencias dentro del catálogo.

## PRUEBAS

- Unitarias: 46 del algoritmo/instalación y 35 de runtime/caché/tareas.
- Integración: 17 con PostgreSQL desechable, incluyendo activación en instalaciones existentes, dos clientes, transacciones, rollback, concurrencia, auditoría, permisos y diálogo de administración.
- GUI: 16 con ventanas Qt reales: añadir, cantidad/precio, normalización, cancelación, edición, equivalencias, accesibilidad y ciclo de arranque/cierre.
- Boundary cases: 9/10/11 recibos, umbral de frecuencia, diagnósticos duplicados, primeros dos, texto vacío, abreviaturas ambiguas, valores inválidos y caché corrupta.
- RED/GREEN: se conservaron fallos reproducidos antes de corregir límites, prioridades, recorte del editor, activación en instalaciones existentes y equivalencias. El desempate del medicamento principal es consistente entre aprendizaje y pantalla, y escribir un diagnóstico simple no ejecuta comparaciones difusas innecesarias.
- Regresión completa en 67 grupos, con perfiles aislados y datos sintéticos. Huella de los archivos de producto verificada antes y después.

## RESULTADOS

- 3391 casos recogidos; JUnit registra 3465 verificaciones, incluyendo 74 subtests.
- PASS: 3464; FAIL: 0; ERROR: 0; SKIP: 1.
- Las 114 pruebas nuevas: PASS.
- Omisión existente: test_real_capacity_dry_run_preserves_operational_counts exige habilitación explícita de una base para la prueba de capacidad; no es una prueba del recomendador.
- Durante una ejecución anterior del QA, el grupo 41 tuvo cuatro errores de preparación: PostgreSQL temporal no encontraba pg_notify. Se conserva el diagnóstico; el grupo completo pasó con un servidor temporal nuevo sin eliminar pruebas. La regresión final vuelve a ejecutar el conjunto completo con el código final.
- En la regresión final, el primer intento del grupo 39 observó WinError 5 al reemplazar el marcador JSON del arranque existente en una prueba concurrente. Se conserva el primer registro y se repite el grupo completo; el módulo afectado, sigeh_product.py, no fue modificado por esta función.

## COBERTURA

coverage.py con ramas; combinación exclusiva de los grupos finales satisfactorios. En archivos heredados se mide el cambio semántico contra la copia del árbol de trabajo anterior a esta función.

| Archivo / alcance modificado | Líneas | Ramas |
|---|---:|---:|
| diagnosis_recommendations.py | 100.0% | 98.65% |
| diagnosis_recommendation_store.py | 99.47% | 98.33% |
| diagnosis_recommendation_schema.py | 100.0% | 100.0% |
| diagnosis_recommendation_ui.py | 99.04% | 98.21% |
| diagnosis_recommendation_admin.py | 99.62% | 95.83% |
| CALCULOS_QT.py | 100.0% | 100.0% |
| catalog_workspace_design.py | 100.0% | 100.0% |

- Total nuevo/modificado: 99.57% de líneas; 98.02% de ramas.
- Lógica crítica del alcance: 99.57% de líneas; 98.02% de ramas.
- Quality gates: mínimo 90%/85% por archivo; objetivo crítico 95%/90%. Sin reducir umbrales.

## CALIDAD

- Formatter: PASS, Ruff en archivos nuevos/pruebas y rangos modificados heredados.
- Lint: PASS, Ruff; cero errores nuevos.
- Type checker: PASS, mypy; módulos nuevos y comparación con la base de los archivos heredados.
- Análisis estático/seguridad: PASS, Bandit; cero hallazgos nuevos.
- Complejidad: PASS, Radon; máximo 9 en módulos nuevos y 10 en código modificado del catálogo. Complejidad heredada superior a 10 permanece sin incremento, incluida db_init (55).
- Duplicación: PASS, Pylint R0801 con umbral de 6 líneas; 0 líneas duplicadas (0%) en módulos nuevos y 0 bloques nuevos respecto de los archivos heredados.
- No se añadieron exclusiones para ocultar diagnósticos.

## BUILD

Comando ejecutado:

    python -X utf8 -m PyInstaller --noconfirm --distpath output/recommendations-app --workpath output/recommendations-build build_app.spec

Resultado: PASS, salida 0. Candidato: output/recommendations-app/SIGEH.
La comprobación del código compilado coincide con los ocho módulos verificados; hashes registrados.

## QA

Cuatro pasadas realizadas:

1. Funcionalidad: reglas anteriores contrastadas con implementación y pruebas.
2. Regresiones: suite completa y flujo existente de Facturación/Admisión/listados.
3. Clean code: revisión del diff contra la base de trabajo, responsabilidades, duplicación y complejidad.
4. QA: cobertura, análisis, build, GUI y smoke del ejecutable.

- Interfaz revisada mediante capturas reales de 1280 y 1920 píxeles. Editor de administración comprobado con ventana de 752 píxeles, filas completas y desplazamiento horizontal.
- Smoke: seis comprobaciones PASS — lanzador, actualizador, paquete, PDF, visor e informes.
- Seguridad: parámetros SQL, rol activo comprobado al persistir, rol sin privilegios rechazado por el esquema privado y transacciones sin cambios parciales.
- Prueba sintética de 10.000 registros: resumen 4.581 ms, ítems 3.832 ms. Planificador sin forzar índices.
- Sondeo sin cambios: cero cambios y 119 bytes de JSON de respuesta. Esta cifra excluye el tráfico del protocolo y no representa el egreso total de Supabase.
- Ningún QA destructivo ni publicación contra producción.

## QUALITY GATES

| Gate | Resultado |
|---|---|
| Funcionalidad | PASS |
| Unit tests | PASS |
| Regression tests | PASS |
| Integración PostgreSQL/SQLite/Qt | PASS |
| Coverage y boundary tests | PASS |
| Formatter y lint | PASS |
| Type checker y análisis estático | PASS |
| Complejidad y duplicación | PASS |
| Build | PASS |
| Smoke test | PASS |
| Seguridad del alcance | PASS |
| QA final local | PASS |
| Publicación / QA en producción | N/A — entrega local, no solicitado aquí |

## PROBLEMAS PENDIENTES

- Sin fallos nuevos conocidos dentro del alcance.
- Producción no publicada ni modificada por esta entrega. No se ejecutó una validación en dos computadoras físicas; la sincronización se validó con dos clientes PostgreSQL y almacenamiento SQLite real.
- Persisten avisos heredados de PyPDF2 y avisos de cobertura de módulos no importados en grupos particulares; la cobertura final combina todos los grupos.
- Se observó un acceso denegado intermitente en la prueba concurrente heredada del marcador de arranque. La repetición final pasó; no se ha cambiado ese módulo ni se afirma haber corregido la causa de esa incidencia.

## EVIDENCIA

- output/recommendations-suite-status.json y recommendations-suite-manifest.json.
- output/recommendations-suite-*.xml / .log; primer fallo de infraestructura archivado como recommendations-suite-41-first-attempt.* y recommendations-suite-41-first-postgres.log.
- output/recommendations-suite-39-first-attempt.* conserva la incidencia de acceso concurrente al marcador de arranque.
- output/recommendations-coverage-summary.json y recommendations-coverage.json.
- output/recommendations-quality.json y recommendations-duplication.json.
- output/recommendations-build-execution.json, recommendations-build-final-complete.log, recommendations-build-result.json y recommendations-smoke.json.
- output/recommendations-postgres-performance.json.
- output/recommendations-1280.png, recommendations-1920.png y recommendations-admin-*.png.
