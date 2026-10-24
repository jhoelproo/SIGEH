# QA — Honorarios por especialidad y materiales vinculados

Fecha: 23/10/2026. Validación local con datos sintéticos. Producción no modificada.

## IMPLEMENTACIÓN

- `CALCULOS_QT.py`: columnas de cobro por especialidad en Honorarios por ARS; botón Materiales vinculados en el catálogo administrativo; control de adición manual y guardado; coordinación del carrito y actualización de caché por versión.
- `billing_item_rules.py`: tres especialidades, General equivalente a Emergenciología; cantidades enteras y asignaciones proporcionales.
- `billing_item_rule_store.py`: persistencia transaccional, comprobación del administrador contra la base, auditoría y protección de honorarios históricos.
- `billing_material_cart.py`: altas, incrementos, disminuciones y eliminación de consumibles; conservación de cantidades manuales y materiales compartidos.
- `medication_material_dialog.py`: búsqueda, selección y unidades por medicamento; estado de carga, error y guardado; filas legibles.
- `receipt_continuity.py`: conservación local de reglas de materiales y honorarios para reiniciar sin conexión.
- `migrations/20261023184143_ars_specialty_medication_materials.sql`: migración aditiva generada mediante Supabase CLI, validada sobre PostgreSQL desechable. Tabla nueva con RLS, sin permisos públicos ni políticas abiertas. No se ejecutó contra producción.
- Pruebas nuevas: `test_ars_specialty_material_rules.py`, `test_ars_material_rules_postgres.py`, `test_billing_material_cart.py`, `test_ars_material_dialogs.py`, `test_ars_material_rule_edges.py` y `test_ars_material_window.py`.

Se preservaron tarifas, recargos, importes históricos, conceptos ya guardados, impresión, reglas de cobertura, autenticación y modificaciones anteriores ajenas a esta tarea.

## FUNCIONAMIENTO

1. En Honorarios por ARS, marcar Emergenciología, Pediatría o Ginecología donde se permita cobrar. Desmarcar impide añadir honorarios manualmente y guardarlos como nuevos cargos. No mostrar aviso conserva su función de recordatorio.
2. En Catálogo → Medicamentos, seleccionar el medicamento y abrir Materiales vinculados. Marcar consumibles y unidades por cada unidad de medicamento. Guardar sin selección establece una configuración explícita sin materiales automáticos.
3. Al añadir el medicamento, los materiales se multiplican por su cantidad. Los cambios de cantidad y la eliminación ajustan las unidades añadidas por ese borrador, conservando las cantidades manuales.
4. Un medicamento sin configuración conserva los automatismos anteriores. Una configuración explícita los sustituye para evitar la doble adición.
5. Los medicamentos y materiales ya presentes al cargar un recibo histórico se conservan; configurar relaciones nuevas no recalcula recibos existentes. Solo los incrementos explícitos añadidos al borrador generan nuevas asignaciones.
6. Los materiales inactivos o ausentes producen un mensaje y evitan una adición parcial del medicamento. No se sustituyen por otro artículo.

## PRUEBAS Y RESULTADOS

- Suite completa aplicable: **3,300 PASS**, **0 FAIL**, **0 errores**, **1 SKIP**; 3,227 casos recogidos, más subtests registrados en JUnit. 60 procesos/grupos aislados.
- Unitarias: especialidades, alias General, políticas inválidas, cantidades 0/1/300/1000/máximos, valores negativos, decimales, booleanos y duplicados.
- Integración PostgreSQL: lectura por conexiones distintas, altas y cambios de relaciones, selección vacía, nombres modificados, inactivos, rollback por error de auditoría, restricciones, RLS, permisos obsoletos o falsificados, importes históricos y rechazo atómico de honorarios nuevos.
- Regresión: Admisión, recibos, tarifas, autorización, usuarios, continuidad local, listados, PDF, reportes y actualizaciones.
- Qt: botones reales, selección, filtros, campos habilitados, errores, reintentos, guardado, cierre durante una consulta, reapertura de caché, carrito y ventanas de 1280 y 1920 píxeles.
- Regresión visual reproducida: el paquete anterior dejó el campo de unidades en 17 px; la prueba falló con `17 >= 36`. Tras corregir la altura, pasó. Evidencia RED/GREEN conservada.

SKIP: test_real_capacity_dry_run_preserves_operational_counts: explicit real PostgreSQL capacity integration test

## COBERTURA

Herramienta: coverage.py con ramas; módulos nuevos completos y cambios semánticos en archivos heredados, comparados con la copia de trabajo previa a esta tarea.

| Ámbito | Líneas | Ramas |
|---|---:|---:|
| Módulos nuevos | 100.0% | 100.0% |
| Cambios totales | 99.21% | 99.4% |
| Lógica crítica modificada | 99.21% | 99.4% |

La cobertura parcial o de paquetes anteriores no se incluyó en la medición final. Se verificó la huella de los archivos fuente contra la ejecución completa final.

## CALIDAD

- Formatter Ruff: PASS en módulos nuevos, pruebas y rangos modificados de archivos heredados.
- Lint Ruff: PASS; cero errores nuevos frente a la copia inicial.
- Tipos mypy: PASS en módulos nuevos; cero errores nuevos en archivos heredados. No se afirma que todo el código heredado carezca de errores previos.
- Análisis estático y seguridad Bandit: PASS; sin hallazgos nuevos.
- Complejidad Radon: máximo 9 en los módulos nuevos. Las funciones heredadas grandes conservaron o redujeron su complejidad; no se hizo una refactorización general de `db_init` ni otros módulos ajenos.
- Duplicación Pylint: PASS; cero bloques nuevos detectados, sin exclusiones añadidas para ocultarlos.
- Persistencia: parámetros SQL, permisos reales antes de escritura, auditoría y rollback. Sin secretos nuevos ni QA destructivo contra datos reales.

## BUILD

La compilación con `build_app.spec` completó análisis, archivos Python y ejecutables, pero falló al reemplazar una DLL bloqueada en la carpeta anterior.

Comando final ejecutado: `python -m PyInstaller --noconfirm --distpath output/ars-materials-final --workpath output/ars-materials-collect-work output/ars-materials-collect.spec`.

Este paso reutilizó el análisis de dependencias ya completado y recompiló los archivos Python, ejecutables y distribución en una carpeta nueva, sin cambiar módulos de la aplicación.

Resultado: **PASS**. Se comprobó que el código compilado del ejecutable y de los módulos nuevos corresponde al código fuente final.

Paquete: `output/ars-materials-final/SIGEH/`. Versión local sobre la base actual; no se publicó una versión nueva ni se cambió el canal de producción. Un intento de reconstrucción en la carpeta anterior falló por una DLL bloqueada; se conservó esa carpeta y se generó la entrega en una ruta nueva.

## QA FINAL — CUATRO PASADAS

1. Funcionalidad: PASS — requisitos contrastados con pruebas unitarias, controles reales y PostgreSQL.
2. Regresiones: PASS — suite completa y casos relacionados.
3. Clean Code: PASS — responsabilidades separadas, permisos y transacciones centralizados, sin nueva duplicación detectada.
4. QA: PASS — cobertura, formatter, lint, tipos, análisis estático, build y smoke.

Se ejecutaron seis smoke tests del paquete: lanzador, ayuda del actualizador, integración de Admisión, PDF, visor y reportes; todos terminaron en código 0. La actualización compilada sobre una instalación sintética conservó seis archivos comprobados por hash, logo, nombre, horarios, configuración de conexión y usuario sintético. El reinicio interactivo se sustituyó por un stub para no iniciar una sesión real ni conectar a producción.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias y casos de borde | PASS |
| Integración PostgreSQL y Qt | PASS |
| Regresión completa | PASS |
| Cobertura y objetivo crítico | PASS |
| Formatter y lint | PASS |
| Tipos y análisis estático sin errores nuevos | PASS |
| Complejidad y duplicación nueva | PASS |
| Build y smoke del paquete | PASS |
| Seguridad aplicable | PASS |
| QA final | PASS |
| Despliegue y cambios de esquema en producción | N/A — fuera de esta entrega local |

## PROBLEMAS PENDIENTES

No quedan fallos conocidos en los requisitos validados. La publicación de la versión y la aplicación del esquema central en producción corresponden a un despliegue posterior; esta entrega no los ejecutó. La sincronización se comprobó con conexiones independientes y cachés locales, sin operar dos computadoras físicas.

## EVIDENCIA

En `output/`: `ars-materials-suite-status.json`, `ars-materials-suite-manifest.json`, los XML y logs de los grupos, `ars-materials-coverage-summary.json`, `ars-materials-coverage.json`, `ars-materials-quality.json`, `ars-materials-package-validation.json`, `ars-materials-build-collect.log`, `ars-materials-height-red.log`, `ars-materials-height-green.log`, `ars-materials-configuration.png`, `ars-materials-specialties.png`, `ars-materials-window-1280.png` y `ars-materials-window-1920.png`.

**ESTADO FINAL: APROBADO PARA ENTREGA**
