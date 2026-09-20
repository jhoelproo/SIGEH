# Apertura del centro actual de reportes

## Implementación y causa

ReportsDialog inicializa QDialog directamente y omite el constructor de LegacyReportsDialog. Los métodos heredados intentaban leer `_report_document_worker` antes de que existiera, causando AttributeError al pulsar Abrir PDF. Qt registraba la excepción sin mostrar un diálogo al usuario.

CALCULOS_QT.py inicializa ahora los dos workers documentales. No cambia consultas, archivos históricos, permisos ni cálculos. tests/test_report_delivery_recovery.py prueba la ventana actual y la antigua; cubre Abrir PDF y Vista previa e imprimir con un PDF sintético y el worker real. La recuperación de datos se sustituye en esta prueba para evitar acceso a producción.

## Pruebas y resultados

RED: la prueba del centro actual falló con AttributeError por `_report_document_worker`; la ventana antigua pasó. GREEN: ambos botones del centro actual abren el visor.

- Regresión relacionada: 62 passed, 0 failed.
- Suite completa con coverage: 1576 passed, 29 skipped, 60 subtests passed, 0 failed; 372.52 segundos.
- Evidencia: output/report-opening-final.log y output/report-opening-final.xml.
- Cobertura de las dos líneas de producción modificadas: 2/2, 100%. Ramas nuevas: N/A, son asignaciones. JSON: output/report-opening-coverage.json.

## Calidad

Formatter Ruff de las pruebas: PASS. Lint de pruebas: PASS. Comparación Ruff de CALCULOS_QT.py contra HEAD: 327 hallazgos previos y 327 actuales, cero nuevos. Compileall y diff --check: PASS. Complejidad: no añade decisiones; duplicación: solo las dos asignaciones de estado necesarias en constructores independientes, sin nueva lógica duplicada. Tipos: no cambia firmas ni contratos; no se afirma tipado íntegro del módulo heredado.

## Build y QA

PASS: python -m PyInstaller --noconfirm --distpath output/report-opening-fixed-build --workpath build/release-121 build_app.spec.

PASS: ejecutable resultante con --self-test-pdf y --self-test-report-viewer, ambos código 0. Evidencia: output/report-opening-build.log y output/report-opening-smoke.json. Estos autodiagnósticos comprueban el motor empaquetado; el recorrido completo del botón del centro actual se prueba en la integración GUI de código fuente.

Seguridad: no se modifican SQL, permisos ni configuración. QA usa datos sintéticos. N/A: migraciones y prueba de impresora, no afectados. NO VERIFICADO: apertura de los reportes específicos del hospital desde sus estaciones.

## Cuatro pasadas

Funcionalidad: causa reproducida y corregida. Regresión: suite relacionada y completa. Clean code: cambio mínimo de inicialización. QA: cobertura, lint diferencial, formato, compilación y smoke test ejecutados.

ESTADO FINAL: APROBADO PARA ENTREGA del cambio local validado. No publicado ni instalado en el hospital; conserva la numeración 1.2.1 como compilación de prueba.
