# Validación de SIGEH 1.2.2

La causa fue reproducida en el centro moderno: sus métodos heredados consultaban `_report_document_worker` antes de inicializarlo. Qt registraba `AttributeError` y el usuario no recibía una ventana de error. El constructor actual inicializa ahora los procesos de apertura y exportación.

## Resultados

- Regresión relacionada: 62 passed, 0 failed.
- Validación final de versión y reportes: 79 passed, 0 failed.
- Suite completa: 1576 passed, 29 skipped, 60 subtests passed, 0 failed.
- Cobertura de producción modificada: 2/2 líneas, 100%; ramas nuevas N/A.
- Ruff de pruebas y lint diferencial del módulo heredado: PASS, cero hallazgos nuevos.
- Formatter, compileall y diff check: PASS.
- Build PyInstaller y autodiagnósticos PDF/visor del ejecutable: PASS.

Compilación definitiva de aplicación y actualizador con PyInstaller: PASS. Autodiagnósticos del ejecutable 1.2.2 para lanzador, PDF, visor y reportes: PASS (cuatro códigos de salida 0). Integridad ZIP: PASS. Tamaño: 309826922 bytes. SHA-256 coincide con el manifiesto y archivo de checksum: `60d0f0305b7b8d9385368aaaeeb29e9f26b3f0675fbd8391531acc7e965fc5ac`.

La impresora física y la apertura en las estaciones del hospital permanecen NO VERIFICADAS localmente. Complejidad y duplicación nuevas: N/A (dos inicializaciones de atributos, sin funciones ni ramas nuevas). Tipado adicional: N/A para estas inicializaciones. No se modifican permisos, consultas ni datos de producción.

**ESTADO FINAL: APROBADO PARA ENTREGA** del cambio de software.
