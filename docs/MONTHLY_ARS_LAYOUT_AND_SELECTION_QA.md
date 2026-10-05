# Listados ARS: encabezado, menú contextual y retiro múltiple

Validación local del 5 de octubre de 2026. Alcance: exportación del expediente mensual y acciones de su tabla de pacientes.

## IMPLEMENTACIÓN

- `private_insurance_exporter.py`: recorte nativo DrawingML del logo institucional, con las medidas de MONUMENTAL de los documentos adjuntos. La imagen original permanece intacta. Encabezado anclado en A1; metadatos desde la fila 7 en la relación y desde la fila 6 en la factura. Firma en B24:B27, columnas fiscales ajustadas y factura con área A1:F29, a una página.
- `CALCULOS_QT.py`: selección por Ctrl/Shift, clic derecho con las funciones existentes, seleccionar visibles, limpiar selección y retirar seleccionados. El botón indica la cantidad. Corregir identificación requiere un único recibo seleccionado. Se respetan permisos, expedientes cerrados y operaciones en curso.
- `monthly_batch_removal.py`: retiro de pertenencia al listado en una sola transacción; bloqueo del expediente y de las filas, validación de selección vigente, motivo y auditoría individual. Un fallo revierte toda la operación.
- Pruebas: `tests/test_private_insurance_exporter.py`, `tests/test_monthly_ars_form_state.py`, `tests/test_monthly_batch_removal.py`.

Se conservan recibos, admisiones, datos de pacientes, fechas de servicio, importes y fórmulas vinculadas. Las filas ocultas por la búsqueda se excluyen del retiro. No se añaden exclusiones permanentes ni documentos a la nube. Los cuatro Excel adjuntos fueron abiertos únicamente para lectura; el recorte se uniforma a partir del ejemplo MONUMENTAL, cuyas medidas difieren ligeramente de los otros ejemplos.

## PRUEBAS

Las pruebas de regresión del logo y de selección múltiple fallaron antes de su corrección: faltaba el recorte y la tabla solo admitía una fila. La posición de la firma también falló antes del cambio. Durante QA en Excel se detectó que el dibujo necesitaba geometría rectangular para imprimirse; se reprodujo con una prueba y se corrigió.

Unitarias: selección individual y múltiple, filtrado, menú, cancelación, motivo vacío, fechas inválidas, permisos, estados de botones, identificadores inválidos, duplicados, alias del mismo registro, datos inexistentes y cambios simultáneos.

Integración PostgreSQL: una base descartable de loopback, con retiro mixto de recibo/atención, auditoría, conservación de los registros originales, expediente ajeno intacto, rollback por selección vencida y rollback por fallo de auditoría. No se utiliza la base del hospital.

GUI: señales y acciones reales de Qt, Ctrl, Shift, clic derecho sobre selección y exclusión de filas ocultas. Apertura de la ventana, edición, actualización, cierre y reapertura de expedientes cubiertos por la suite de formularios existente. Se evita sustituir la cobertura de interfaz por pruebas que solo comparen el código.

## RESULTADOS

Pruebas específicas finales: **47 PASS**, 14 subtests PASS, 0 FAIL, 0 errores, 0 skipped. Comando:

```
python -m coverage run --branch --data-file=output/ars-list-final.coverage -m pytest tests/test_private_insurance_exporter.py tests/test_monthly_ars_form_state.py tests/test_monthly_batch_removal.py tests/test_monthly_ars_candidate_selection.py -q --tb=short --junitxml=output/ars-list-final-specific.xml
```

La suite relacionada previa, que incluye la integración completa Admisión → Facturación → listado, registró 59 PASS y 14 subtests PASS. Suite completa final: **2.050 casos recogidos y ejecutados**, 2.049 PASS, 0 FAIL, 0 errores y un skipped opcional de capacidad real de PostgreSQL. Además, 74 subtests PASS: **2.123 comprobaciones aprobadas** y una omitida. No faltan casos ni hay casos adicionales respecto a la colección.

Los once grupos se ejecutaron en procesos aislados. Todos corresponden al fingerprint de las fuentes finales `74b4f917d98982485e0386e2e454ca82760fe85e09bb39b270d077b26d9fc733`. Tras ordenar el import del módulo nuevo para eliminar un diagnóstico de lint se reinició la regresión completa; no se atribuyen resultados de la fuente anterior a la versión final. Evidencia: `output/ars-list-final-specific.xml`, `ars-list-full-summary.json`, `ars-list-full-suite-completeness.json`, `ars-list-final-fingerprint.json` y once XML/logs de grupos.

## COBERTURA

Herramienta: coverage.py, con ramas reales. Ámbito medido: funciones modificadas del monolito, funciones modificadas del exportador y módulo nuevo completo; no se afirma cobertura total de SIGEH.

| Ámbito | Líneas | Ramas |
| --- | ---: | ---: |
| Conjunto modificado | 573/577 = 99,31 % | 104/108 = 96,30 % |
| Persistencia crítica del retiro | 55/55 = 100 % | 22/22 = 100 % |
| Encabezado y documentos modificados | 158/158 = 100 % | 40/40 = 100 % |

Las cuatro líneas/ramas no cubiertas corresponden a alternativas heredadas de la construcción de la ventana y a una fila completamente válida del renderizador de la tabla. El retiro, sus wrappers, validaciones, menú, selección y sus callbacks están cubiertos. Evidencia: `output/ars-list-final-coverage.json`, `output/ars-list-coverage-summary.json`.

## CALIDAD

Ruff formatter: PASS de todas las funciones nuevas/modificadas, sin reformatear el monolito completo. Ruff lint y Mypy: PASS diferencial, cero diagnósticos nuevos frente a HEAD; se conservan diagnósticos heredados. Los números de línea incrustados en los mensajes de redefinición se normalizan para comparar el mismo diagnóstico desplazado. No se introducen supresiones ni se debilitan configuraciones.

Radon: máximo 10 en lógica nueva; módulo transaccional máximo 9. Excepciones heredadas revisadas: `_fill_patients` baja de 22 a 19, `_write_relation_sheet` mantiene 16 y `_write_global_invoice_sheet` mantiene 14. Son constructores de documentos/tabla existentes; se conservan sus campos y reglas para evitar una refactorización general fuera del ajuste solicitado. No se añaden funciones nuevas con esa complejidad.

Pylint duplicate-code, umbral de seis líneas: PASS, 0 líneas duplicadas, 0,000 % en los tres archivos de producción comprobados. Bandit: PASS del módulo nuevo y exportador, cero hallazgos. SQL estático y parametrizado; entradas se validan y los permisos también se comprueban en el backend. No hay secretos nuevos ni cambios en credenciales.

Evidencia: `output/ars-list-static-summary.json`, logs Ruff/Mypy antes y después, `ars-list-duplication-metrics.log`, `ars-list-complexity.json`, `ars-list-final-bandit.json`.

## BUILD

**PASS**, exit 0:

```
python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/ars-list-build --workpath build/ars-list-build build_app.spec
```

Se comprueba que el bytecode empaquetado de `CALCULOS_QT`, `monthly_batch_removal` y `private_insurance_exporter` coincide con las fuentes actuales: tres PASS. Las advertencias de imports aceleradores opcionales del build no impidieron construir ni ejecutar los modos de prueba. Evidencia: `output/ars-list-build.log`, `output/ars-list-build-verification.json`.

## QA

Microsoft Excel real, sin guardar modificaciones sobre los adjuntos: apertura del ejemplo sintético, recálculo completo y exportación a PDF PASS. Once pacientes sintéticos, total calculado RD$ 8.495,60, igual en relación, factura y total general. Se inspeccionaron visualmente ambas páginas renderizadas: logo visible y recortado, datos fiscales, tabla, totales y firma. Se conservan las imágenes originales dentro del XLSX.

Cuatro smoke tests del ejecutable empaquetado: lanzador, generación PDF, visor y reportes, todos exit 0. Cuatro comprobaciones de ratón/teclado de Qt PASS. Capturas locales de tabla y menú y ejemplo sintético en `output/ars-list-layout-qa/`; no contienen datos reales de pacientes.

Cuatro pasadas finales: funcionalidad PASS (logo, posiciones, menú, selección y retiro); regresión PASS (suite completa y correspondencia con la colección); clean code PASS (responsabilidades, deduplicación, métricas y errores); QA PASS (unitarias, integración, cobertura, análisis, build, smoke y revisión visual). `git diff --check`: PASS. Los scripts y resultados de QA quedan bajo `output/`, fuera del producto y del conjunto de archivos publicados.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad, unitarias, límites | PASS |
| Integración PostgreSQL/archivos y rollback | PASS |
| Cobertura | PASS |
| Formatter, lint/tipos diferencial, análisis estático | PASS |
| Complejidad nueva y excepciones heredadas documentadas | PASS |
| Duplicación y seguridad | PASS |
| Build y smoke | PASS |
| Regresión completa y QA final | PASS |
| Capacidad opcional de PostgreSQL real | N/A — requiere entorno explícito y no valida este ajuste |
| Impresión física en el hospital | N/A — requiere su impresora; se valida el motor de Excel/PDF |
| Publicación de un nuevo release | N/A — este ajuste se prepara localmente |

## PROBLEMAS PENDIENTES

No quedan problemas pendientes en el alcance implementado. La impresión física no se ha ejecutado. Los cambios se aplican a nuevas exportaciones; no modifican Excel emitidos previamente. Este trabajo prepara el ajuste local y no publica un release adicional.

**ESTADO FINAL: APROBADO PARA ENTREGA**
