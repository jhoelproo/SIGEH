# SIGEH 1.3.3 — QA de reparación de impresión

Fecha: 2026-10-25. Fuente publicada: `040a8eba8ad6630ad6b1d2d5cf536d4261faf05d`.

## Implementación
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: restablece SumatraPDF para el envío Windows, solicita Carta/doble cara para hojas y reimpresiones, conserva errores pendientes entre etapas.
- `admission_pyside6/legacy_backend.py`: propaga el error real y solicita doble cara.
- `pdf_printing.py`: configura papel antes del trabajo y separa copias impares.
- Versión, notas y pruebas actualizadas. Formularios, tarifas y contabilidad preservados.

## Pruebas y resultados
- Reproducción inicial: 4 pruebas FAIL antes de corregir (`output/print-oct25-red.xml`).
- Unitarias, integración PDF/SQLite y regresión de impresión: 139 PASS, 0 FAIL, 0 skipped.
- Recibos, informes y actualización: 126 PASS, 0 FAIL, 0 skipped.
- Total de las dos ejecuciones finales: 265 PASS.
- Bordes: copias impares, orientación mixta, errores de envío, timeout y etapas fallidas conservadas.
- Evidencia: `output/print-oct25-final.xml`, `output/release-133-regression.xml`.

## Cobertura y calidad
- Funciones modificadas: 158/158 líneas (100%), 68/68 ramas (100%).
- Complejidad máxima modificada: 9. Duplicación nueva detectada: 0 bloques (Pylint).
- Formatter: PASS en funciones modificadas; lint: 0 errores nuevos.
- Types: PASS en helper PDF/backend; V15 conserva 11 errores previos, 0 nuevos.
- Análisis estático/seguridad diferencial: 0 hallazgos nuevos; 213 previos en el alcance heredado.
- Evidencia: `output/print-oct25-quality.json`, `output/print-oct25-static-summary.json`, `output/print-oct25-duplication.log`.

## Build y actualización
- PyInstaller aplicación y actualizador: PASS (`output/release-133-build.log`, `output/release-133-updater-build.log`).
- Paquete: 1823 archivos verificados, CRC/tamaños/SHA correctos, 9 módulos compilados comparados con fuente, Sumatra idéntico al incluido en fuente.
- Smoke: 6 comprobaciones PASS. Actualizador anterior aplicado en perfil desechable: 8 archivos de configuración conservados y 2 bases SQLite sin pérdida de filas.
- ZIP SHA256: `ccc30240a452ad2417ccd1a030856ddada1d2e8ac473337515082f091d65353b`.
- Publicación estable y cliente real de actualización: PASS (`output/release-133-publication-verification.json`).

## QA y quality gates
Funcionalidad de software, unitarias, integración, regresión, bordes, cobertura, formatter, lint diferencial, types diferencial, análisis estático diferencial, complejidad, duplicación, build, smoke y revisión de seguridad: PASS.

QA físico Canon: NO VERIFICADO. Esta computadora solo dispone de Microsoft Print to PDF y OneNote. El usuario todavía no probó 1.3.3 en el hospital. No se enviaron trabajos de impresión durante QA ni se alteraron impresoras.

Se revisaron visualmente las tres páginas Carta del formulario GENERAL generado. El motor recibe un solo trabajo con `duplexlong`; la salida física esperada es páginas 1/2 en la primera hoja y 3 en el frente de la segunda.

Fuentes: [SumatraPDF argumentos](https://www.sumatrapdfreader.org/docs/Command-line-arguments), [impresión](https://github.com/sumatrapdfreader/sumatrapdf/blob/master/docs/md/Printing.md), [Qt QPrinter](https://doc.qt.io/qt-6/qprinter.html).

## Problemas pendientes
Comprobar salida física en la Canon. Permiso individual de preliminares y asociaciones por ARS continúan en una actualización posterior.

ESTADO FINAL: NO APROBADO PARA ENTREGA — QA físico pendiente. El paquete de reparación está publicado para validación en el hospital.
