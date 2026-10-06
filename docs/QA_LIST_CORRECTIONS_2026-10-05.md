# Correcciones de búsqueda, nombres, especialidades y foco

ESTADO FINAL: APROBADO PARA ENTREGA.

Trabajo iniciado el 5 de octubre y validado el 6 de octubre de 2026. Base de comparación: `351d6b3`. Compilación local; la versión pública 1.2.9 no se republicó.

## Implementación

- `monthly_candidate_search.py` y `CALCULOS_QT.py`: búsqueda explícita por recibo en «Añadir pacientes de la ARS». «Todos» también busca recibos. Prioriza coincidencia exacta y admite números cortos y búsqueda parcial. Conserva la selección por identidad del candidato.
- `receipt_patient_correction.py`, `receipt_list_consistency.py` y `CALCULOS_QT.py`: nombre editable en la corrección del listado, sincronización con el recibo y los listados pendientes. Cuando existe un vínculo explícito con Admisión, actualiza el paciente central y esa atención. El documento, el historial y los datos se guardan en la misma transacción. Una corrección obsoleta se rechaza antes de sobrescribir un nombre más reciente.
- `billing_specialties.py` y `CALCULOS_QT.py`: selector editable con EMERGENCIOLOGÍA, PEDIATRÍA, GINECOLOGÍA y GENERAL; búsqueda sin distinguir acentos, sugerencias por proximidad y conservación de especialidades personalizadas. Recupera la especialidad de Admisión cuando falta en el recibo y conserva una corrección explícita al volver a guardar el recibo.
- `receipt_history_focus.py`, `receipt_history_design.py` y `CALCULOS_QT.py`: devolución del foco al buscador después de filtros, cancelación del desplegable, limpieza y finalización/error de consulta. Conserva el foco de los campos avanzados, fechas personalizadas y diálogos activos. Evita que Enter active otro botón del diálogo.

Se preservan importes, ítems, ARS, fecha, número de recibo, autor, permisos y estados existentes. Los listados ya enviados conservan su fotografía histórica. Corregir un nombre sin NSS o autorización no inventa datos ni convierte al paciente en «Listo».

## Pruebas

Regresiones añadidas en `tests/test_monthly_receipt_corrections.py`, `tests/test_billing_specialties.py`, `tests/test_receipt_patient_correction.py` y `tests/test_monthly_patient_name_postgres.py`.

Se actualizaron dos expectativas heredadas conforme al nuevo comportamiento solicitado:

- `tests/test_integral_emergency_to_monthly_list.py`: una atención vinculada con especialidad GENERAL debe recuperarla, en lugar de permanecer vacía.
- `tests/test_receipt_edit_guards_postgres.py`: la corrección del nombre debe actualizar el paciente y la atención, manteniendo la fecha de Admisión. La prueba ahora proporciona una identidad central válida. Antes permitía modificar solo el recibo con una proyección que no identificaba al paciente central. La nueva protección rechaza ese vínculo incompleto antes de escribir; `output/list-corrections-old-name-contract.xml` registra ese rechazo y `output/list-corrections-name-contract-green.xml` la prueba actualizada aprobada.

Casos cubiertos: búsqueda exacta/parcial, selección que sobrevive al filtro, vacíos, entradas sin números, Unicode y apóstrofos, nombres de 1/160/161 caracteres, especialidades de 100/101 caracteres, valores personalizados, vínculos ausentes/eliminados/cambiados, permisos, correcciones obsoletas, duplicados, nombre y fecha cambiados juntos, ida y vuelta entre pantallas, rollback ante fallo del documento, listados enviados y conservación de importes.

Se reprodujeron los defectos antes de corregirlos: `output/list-corrections-red.xml` contiene 9 fallos y 2 casos aprobados. La regresión adicional de nombre y fecha produjo una violación de unicidad antes de guardar ambos valores de forma atómica; `output/list-corrections-name-date-green.xml` demuestra el caso corregido.

## Resultados y cobertura

La suite completa se ejecutó en doce procesos de prueba agrupados, dos simultáneos. Las fuentes y los tests permanecieron congelados durante esta ejecución: fingerprint SHA-256 `559b58a39ac8c27a152fb586f4be4ffa640e8615948f6fa3fe5e63bc29551852`.

| Ejecución | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| Suite completa aplicable | 2,357 + 74 subpruebas | 0 | 1 |
| Pruebas específicas con cobertura | 124 | 0 | 0 |
| Interfaz nativa de Windows | 21 | 0 | 0 |
| Smoke del ejecutable | 4 | 0 | 0 |

Las ejecuciones específicas y nativas repiten casos de la suite; no se suman como pruebas únicas. La prueba omitida es `test_real_capacity_dry_run_preserves_operational_counts`, que exige activar expresamente una base PostgreSQL de capacidad. Es opcional y no corresponde a estos cambios; no se sustituyó por producción. Las integraciones de nombres, Admisión, guardado, rollback y concurrencia sí se ejecutaron.

Medición real con Coverage.py, combinando únicamente los doce archivos de cobertura de la ejecución final y las pruebas específicas de las mismas fuentes:

| Alcance | Líneas | Ramas |
| --- | ---: | ---: |
| Cuatro módulos nuevos completos | 100% | 100% |
| Sentencias modificadas de `CALCULOS_QT.py` | 100% | 93.75% |
| Sentencias modificadas de `receipt_list_consistency.py` | 100% | 100% |
| Sentencias modificadas de `receipt_history_design.py` | 100% | N/A: sin ramas |
| Código nuevo y sentencias modificadas, combinado | **100%** | **99.02%** |

Son 314 líneas ejecutadas y 101 de 102 ramas. Se mide el cambio semántico respecto al commit base, con AST/tokens y los datos de Coverage.py; no se presenta como cobertura total del monolito heredado. La lógica nueva de corrección, identidad, selección y foco alcanza 100% de líneas y ramas. Se cumplen los umbrales 90%/85% y los objetivos críticos 95%/90%.

Evidencia: `output/list-corrections-results.json`, `output/list-corrections-current-group-*.xml`, `output/list-corrections-unit-current.xml`, `output/list-corrections-coverage.json` y `output/list-corrections-coverage-summary.json`.

Comandos usados; las comparaciones de calidad y cobertura fijan el commit base para conservar su significado después del commit de entrega. El helper de PostgreSQL se usa cuando la instancia local de QA está detenida:

```powershell
python -X utf8 output/list-corrections-local-postgres.py
python -X utf8 output/list-corrections-current-suite.py
python -X utf8 output/list-corrections-results.py
python -m coverage json --data-file=output/.coverage-list-corrections-combined -o output/list-corrections-coverage.json
python -X utf8 output/list-corrections-coverage-scopes.py
python -X utf8 output/list-corrections-static.py
python -X utf8 output/list-corrections-format-check.py
python -m mypy --check-untyped-defs --follow-imports=skip --ignore-missing-imports monthly_candidate_search.py billing_specialties.py receipt_history_focus.py receipt_patient_correction.py receipt_list_consistency.py receipt_history_design.py
python -X utf8 output/list-corrections-code-and-smoke.py
```

## Calidad

Ruff, mypy, Bandit, Radon y Pylint se ejecutan realmente mediante `output/list-corrections-static.py`. Además, mypy con `--check-untyped-defs` comprueba los seis módulos pequeños, sin incidencias. Se usan los enum completos de Qt y el tipo del editor de un combo previamente habilitado como editable. El monolito se compara con el commit base `351d6b3`: el criterio es cero errores nuevos, sin ocultar su deuda previa.

Complejidad máxima de los módulos auxiliares: 10. Los métodos pequeños modificados del monolito permanecen en 10 o menos; el filtrado baja de 20 a 1.

Excepción heredada documentada: `save_receipt_with_items` es la orquestación transaccional existente y pasa de 185 a 190 en Radon. Se mantiene su transacción, controles y formato para evitar una refactorización extensa de facturación fuera del alcance. La identidad, validación, bloqueo y corrección del nombre se extraen a funciones pequeñas del módulo nuevo; ninguna supera 8. Ruff verifica el formato de los módulos nuevos y los métodos pequeños modificados; la función heredada conserva su convención existente.

Pylint compara duplicación entre los seis módulos pequeños con bloques mínimos de seis líneas. Se informan bloques detectados, sin inventar un porcentaje de duplicación global.

Resultado: cero bloques duplicados detectados en ese alcance, cero errores de Ruff y mypy en los módulos comprobados, cero errores nuevos de lint/tipos en el monolito y cero hallazgos de Bandit. Evidencia: `output/list-corrections-static-summary.json`, `output/list-corrections-types-untyped.log`, `output/list-corrections-main-format.json` y `output/list-corrections-legacy-complexity.json`.

## Build

Comando ejecutado:

```powershell
python -X utf8 -m PyInstaller --noconfirm --log-level WARN --distpath output/list-corrections-app --workpath build/list-corrections-app build_app.spec
```

Build: PASS. `output/list-corrections-built-source.json` verifica que los siete módulos incluidos coinciden con el código actual, incluyendo bytecode y tabla de líneas.

## QA

- Interfaz nativa de Windows: 21 casos aprobados en `output/list-corrections-native-current.xml`.
- Capturas reales del editor y búsqueda en temas claro/oscuro; historial a 1366, 1680 y 1920 píxeles, en `output/list-corrections-visual/`. Se comprobaron visibilidad y límites de los controles y se revisaron las capturas.
- Ejecutable: cuatro smoke tests aprobados, con resultados en `output/list-corrections-smoke.json`: lanzador, PDF, visor del PDF y reportes.
- Integración y concurrencia: PostgreSQL desechable en loopback, con datos sintéticos; nunca se ejecuta QA destructivo contra producción.
- Seguridad: parámetros SQL, vínculos por UUID/identidad de origen, controles de rol existentes, rechazo de vínculos cambiados y rollback. Los tests empaquetados usan una conexión sintética inaccesible; no necesitan credenciales del hospital.

## Quality Gates

| Gate | Resultado |
| --- | --- |
| Funcionalidad | PASS |
| Unit tests y boundary cases | PASS |
| Integración, transacciones y rollback | PASS |
| Regresión completa aplicable | PASS |
| Cobertura | PASS: 100% líneas / 99.02% ramas del cambio |
| Formatter | PASS en módulos/tests nuevos y scopes modificados; convención heredada conservada en la orquestación transaccional |
| Lint | PASS: cero errores nuevos |
| Type checker | PASS: seis módulos, también con `--check-untyped-defs`; cero errores nuevos en el monolito |
| Static analysis | PASS |
| Complejidad | PASS con excepción heredada documentada: 10 máximo en los módulos pequeños; 190 en la orquestación existente |
| Duplicación | PASS: cero bloques de seis líneas detectados en módulos revisados |
| Build | PASS |
| Smoke del paquete e interfaz | PASS |
| Seguridad aplicable | PASS |
| Benchmark externo de capacidad | N/A: opcional, base específica no habilitada |
| QA final | PASS |

Cuatro pasadas realizadas: requisitos y persistencia; regresiones entre componentes; nombres/responsabilidades/duplicación/errores/complejidad; pruebas, cobertura, herramientas, build, interfaz y smoke. No se debilitó ninguna configuración ni se añadió una exclusión para ocultar errores.

## Problemas pendientes

Sin defectos nuevos detectados en el alcance validado. Permanece la deuda heredada del monolito, identificada expresamente en la sección de calidad. PyPDF2 y algunos hooks opcionales del empaquetado emiten avisos heredados; las comprobaciones del ejecutable pasaron.

La vista interactiva se lanzó con `output/launch_list_corrections_preview.py`, configuración aislada y la base sintética local `sigeh_list_corrections_preview`. No se publicó una nueva versión en GitHub ni se modificaron datos del hospital durante QA.
