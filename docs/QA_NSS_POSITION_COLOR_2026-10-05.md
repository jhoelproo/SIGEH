# Ajuste de NSS: posición y contraste

ESTADO FINAL: APROBADO PARA ENTREGA

## IMPLEMENTACIÓN

- `pdf_engine/template.html`: NSS debajo del nombre del paciente, alineado con el inicio del texto de esa columna.
- `pdf_engine/styles.css`: etiqueta con texto azul en negrita, fondo celeste, borde y acento azul. Utiliza el ancho disponible y permite ajustar identificadores largos sin desbordar.
- `tests/test_receipt_nss_layout.py`: comprobaciones geométricas y de contraste con el Chromium real del renderizador de recibos.

Se preservaron el NSS completo, los ceros iniciales, el escape de HTML, los cargos, los totales y las reglas de facturación. No se modificaron consultas, permisos ni persistencia. La clase anterior de encabezado dejó de utilizarse y se eliminó su CSS.

## PRUEBAS

Las cuatro nuevas pruebas fallaron antes de la corrección por el NSS desplazado a la derecha: `output/nss-badge-red.xml` y `output/nss-badge-red.log`. Después pasaron.

Casos de borde: NSS de 9 y 24 dígitos, dato ausente y nombre largo con identificador de 40 dígitos como prueba de resistencia del diseño. Esta última prueba no cambia el límite operativo del documento.

Las pruebas de integración comprueban con CSS de impresión:

- Alineación del NSS al borde izquierdo de su columna, con tolerancia inferior a un píxel.
- Posición debajo del nombre y separación entre ambos.
- Ausencia de desbordamiento horizontal y conservación del texto completo.
- Fondo visible, peso de fuente y contraste mínimo 4,5:1.

Las pruebas existentes cubren HTML escapado, identificadores con ceros iniciales, ausencia de NSS y renderizado de recibos de los distintos tipos.

## RESULTADOS

| Ejecución | Passed | Failed/errores | Skipped |
| --- | ---: | ---: | ---: |
| Específica NSS: unitarias e integración | 15 | 0 | 0 |
| Suite relacionada final | 40 | 0 | 0 |
| Smoke del ejecutable final | 4 | 0 | 0 |

Las filas se solapan; no se suman. Evidencia JUnit: `output/nss-badge-specific.xml` y `output/nss-badge-regression.xml`.

Comando de regresión:

```powershell
python -X utf8 -m pytest tests/test_receipt_nss_layout.py tests/test_receipt_nss_design.py tests/test_self_pay_receipt_rendering.py tests/test_historical_document_compatibility.py tests/test_report_pdf_integrity.py tests/test_receipt_document_flow.py -q --tb=short --junitxml=output/nss-badge-regression.xml
```

La suite completa de aplicación se había ejecutado para el rediseño anterior. Para este ajuste exclusivo de HTML/CSS se ejecutó la suite aplicable al renderizado; no se presenta la suite completa anterior como una nueva ejecución posterior al ajuste.

## COBERTURA

Líneas/ramas de aplicación: N/A para este ajuste, porque solo se modificaron HTML/CSS y no se añadió lógica Python de aplicación. No se afirma un porcentaje de cobertura sobre CSS. La distribución se comprueba mediante cuatro casos de integración con el navegador real.

Código crítico de facturación/persistencia: N/A, sin modificaciones. Las mediciones anteriores permanecen documentadas en `QA_WORKSPACE_REDESIGN_2026-10-05.md` y no se atribuyen a una nueva medición de este ajuste.

## CALIDAD

- Formatter/lint del test: PASS, `ruff format --check` y `ruff check`.
- Type checker del test: PASS, `mypy --follow-imports skip --ignore-missing-imports`.
- Sintaxis CSS: PASS, análisis real con tinycss2 de reglas y declaraciones; también aplicado por Chromium en las pruebas.
- `git diff --check`: PASS.
- Complejidad de aplicación: N/A, cambios declarativos. Radon mide máximo 9 en los tests y 5 en el cálculo de contraste.
- Duplicación de lógica de aplicación: N/A, sin funciones nuevas o modificadas. No se añadieron copias de bloques de cálculo.
- Formatter específico HTML/CSS: N/A, el proyecto no tiene una comprobación configurada para estos archivos; se respetó el formato existente y se verificó la sintaxis.

Bandit sobre el archivo de pruebas señaló ocho alertas B101 por las aserciones de pytest. Se revisaron: son comprobaciones intencionales de prueba, no controles de seguridad de aplicación. No se desactivó la regla ni se añadieron supresiones. No se presenta ese escaneo del test como un resultado sin alertas.

## BUILD

```powershell
python -X utf8 -m PyInstaller --noconfirm --distpath output/workspace-build --workpath output/workspace-build-cache build_app.spec
```

Resultado: PASS, build final completado en 251,207 segundos. Evidencia: `output/nss-badge-build.log`.

Se verificó la igualdad de la plantilla y CSS empaquetados con los archivos actuales, además de los nueve módulos principales. Evidencia: `output/nss-badge-build-verification.json`.

## QA

Se regeneraron y renderizaron las seis páginas de tres PDFs: NSS normal, NSS/nombre largos y dato ausente. Se revisaron todas visualmente: sin superposición del NSS con el nombre, diagnóstico o ARS. Se verificó el texto y el total RD$ 7.737,20. Cada muestra conserva dos páginas, igual que antes del ajuste.

Medición real del navegador: texto `rgb(18, 63, 140)`, fondo `rgb(231, 241, 255)`, contraste **8,71:1**. Evidencia: `output/nss-badge-layout-summary.json`.

Cuatro smoke tests del ejecutable final: inicio, PDF, visor y reportes. Todos terminaron con código 0. Se emplearon directorios aislados y datos sintéticos.

Seguridad: PASS para el alcance, con escape HTML comprobado y sin nuevos accesos a datos o cambios de autorización.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad y distribución | PASS |
| Unitarias aplicables | PASS |
| Integración del navegador/PDF | PASS |
| Regresión relacionada | PASS |
| Casos de borde | PASS |
| Lint/formato/tipos del test | PASS |
| Análisis de sintaxis HTML/CSS | PASS |
| Build | PASS |
| Smoke del ejecutable | PASS |
| Seguridad del cambio | PASS |
| QA visual final | PASS |
| Cobertura de líneas/ramas | N/A — HTML/CSS declarativos, sin lógica nueva de aplicación |
| Complejidad/duplicación de aplicación | N/A — sin funciones modificadas |
| Impresión física | NO VERIFICADO — revisión por renderizado, sin impresora hospitalaria |

## CUATRO PASADAS

1. Funcionalidad: posición a la izquierda y mayor color/contraste. PASS.
2. Regresión: texto, totales y tipos de recibo preservados. PASS.
3. Clean code: CSS anterior sin uso eliminado, reglas acotadas al NSS. PASS.
4. QA: pruebas, análisis, seis páginas revisadas, build y smoke. PASS para entrega local.

## PROBLEMAS PENDIENTES

No quedan fallos detectados del ajuste. La impresión física no se ejecutó; la verificación visual se realizó sobre los PDFs finales renderizados.
