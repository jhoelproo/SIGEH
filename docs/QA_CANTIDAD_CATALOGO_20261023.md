# Cantidad del catálogo junto al buscador — QA

## IMPLEMENTACIÓN

- `CALCULOS_QT.py`: la etiqueta Cant: forma parte del contenedor original de cantidad, sin duplicar el selector ni las flechas.
- `catalog_workspace_design.py`: mueve ese mismo contenedor al lugar del selector de orden, junto al buscador; elimina el selector, su evento y los modos por precio. Conserva favoritos primero y el orden alfabético habitual.
- `billing_workspace_design.py`: elimina el ajuste de ancho del selector retirado.
- `tests/test_workspace_design.py`: adapta los controles de las pruebas y verifica selección, precio, paginación y orden habitual.
- `tests/test_catalog_quantity_toolbar.py`: verifica la posición real, flechas, escritura, límites y adición con materiales vinculados.

Se conservan búsqueda, favoritos, selección, paginación, rango de 1 a 300, reinicio de cantidad después de añadir, tarifas y cálculos. No se modifica persistencia, impresión ni permisos.

## PRUEBAS

RED: tres casos fallaron antes del cambio por la ubicación inferior del control. La primera regresión general detectó además 218 px de alto en el listado de 1280, por debajo del mínimo existente de 220. Se igualó la altura del selector a la de sus flechas y se comprobaron 223 px y cinco filas completas, sin bajar el umbral de la prueba. GREEN: pasaron los cinco casos específicos y esa regresión, incluida la adición con materiales vinculados.

GUI: ventana real a 1280×680, 1366×728 y 1920×1000; control visible y alineado con la búsqueda, sin selector de orden ni etiqueta duplicada. Escritura manual, flechas y límites 1/300; cantidad 3 de medicamento produce 6 materiales y el campo vuelve a 1. QA visual sobre capturas de 1280 y 1920.

La integración existente con bases PostgreSQL desechables y la regresión general se ejecutaron dentro de la suite completa. No se utilizó la base productiva para QA.

## RESULTADOS

Suite completa: **3,323 PASS**, **0 FAIL**, **0 errores**, **1 SKIP**. 3,250 casos recogidos y subtests adicionales en JUnit; 61 grupos aislados.

Incidencias conservadas en evidencia: una ejecución concurrente superó el límite de 200 ms de una prueba existente de escritura SQLite (1.088 ms). Sus 77 pruebas de grupo pasaron al repetirse por separado, con las mismas fuentes y límites; se reutilizaron los otros 60 grupos aprobados. Antes de corregir la altura también ocurrió un cierre nativo Qt en una corrida; no se reprodujo en la regresión final. Estos resultados previos no se ocultan ni se cuentan como aprobados.

SKIP: prueba optativa de capacidad real ajena al cambio. La huella de fuentes se comprobó al terminar la suite; no se alteró código durante su ejecución.

## COBERTURA

Coverage.py real con ramas y comparación de cambios semánticos contra la copia previa al ajuste. Líneas modificadas: **100.0%** (8/8). Ramas modificadas: **N/A — no se añadieron condiciones ejecutables**. No se presenta como cobertura integral del monolito.

## CALIDAD

Formatter Ruff: **PASS** en rangos modificados y pruebas. Lint Ruff, tipos mypy y seguridad/análisis estático Bandit: **PASS**, sin diagnósticos nuevos frente a la copia inicial. No se afirma que todo el monolito esté completamente tipado.

Complejidad Radon: construcción del catálogo 4→4; reordenamiento 3→3; ajuste de pantalla 6→5. `_build_ui` heredada permanece en 28, sin incremento. Duplicación Pylint: cero bloques nuevos detectados; no se modificaron umbrales ni exclusiones.

## BUILD

**PASS**: `python -m PyInstaller --noconfirm --distpath output/catalog-quantity-final --workpath output/catalog-quantity-final-build output/catalog-quantity-collect.spec`.

Se reutiliza el análisis completo de dependencias previo y se recompilan archivos PYZ, ejecutables y distribución. No hay imports nuevos. Se contrastó el código compilado con las tres fuentes modificadas.

## QA

Seis smoke tests del ejecutable local: lanzador, actualizador, paquete V15, PDF, visor e informes: **PASS**. Perfil sintético aislado y conexión deliberadamente inválida en loopback.

Cuatro pasadas: funcionalidad **PASS**; regresión **PASS**; clean code **PASS**; QA **PASS**.

## QUALITY GATES

| Gate | Resultado |
|---|---|
| Funcionalidad, unitarias, regresión e integración existente | PASS |
| Límites y controles Qt | PASS |
| Cobertura aplicable | PASS |
| Formatter, lint, tipos y análisis estático del cambio | PASS |
| Complejidad y duplicación | PASS — sin aumento heredado |
| Build, smoke, seguridad focalizada y QA final | PASS |
| Migración / QA destructivo contra producción | N/A — ajuste de interfaz |
| Capacidad real optativa | N/A — ajena al cambio |

## PROBLEMAS PENDIENTES

Ningún fallo funcional detectado en este ajuste. **Publicación: pendiente**; el cambio está implementado y compilado localmente. No se cambió la versión ni se publicó otro release después de 1.2.14.

Evidencia: `output/catalog-quantity-*`, incluidos los logs RED/GREEN, JUnit, cobertura, calidad, compilación, smoke y capturas.

**ESTADO FINAL: APROBADO PARA ENTREGA** del ajuste local.
