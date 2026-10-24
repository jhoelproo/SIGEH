# Impresión nativa y papel Carta — 22/10/2026

Preparado sobre el código de SIGEH 1.2.13. Esta tarea no cambia versión ni publica una release.

## Diagnóstico confirmado

- General: tres páginas de 612 × 792 puntos (Carta).
- Pediatría: una página de 612 × 792 puntos (Carta).
- Ginecología: una página de 595,44 × 842,4 puntos (A4). Su contenido se colocaba sobre la plantilla conservando ese tamaño. Esto explica la diferencia de papel enviada al controlador.
- `imprimir_pdf()` iniciaba un proceso externo de Sumatra por copia, sin `-print-settings paper=letter`. Entre copias añadía 0,7 s de espera. `Popen()` se consideraba envío aceptado sin comprobar la salida del proceso.
- `_procesar_salida_atencion()` abría el visor antes de enviar a imprimir. Una apertura lenta por ShellExecute retrasaba la llamada de impresión.

No se dispone de una medición en la impresora hospitalaria que atribuya los 20 s completos a una sola de esas etapas. No se presenta esa hipótesis como una causa raíz física demostrada.

Referencias técnicas contrastadas: [impresión y papel/copias por CLI en Sumatra](https://www.sumatrapdfreader.org/docs/Command-line-arguments), [pintura con QPrinter en workers](https://doc.qt.io/qt-6/threads-modules.html) y [API de QPrinter](https://doc.qt.io/qt-6/qprinter.html).

## Implementación

`pdf_printing.py` utiliza Qt PDF y el motor de impresión de Windows que ya incluye la aplicación. No inicia Sumatra en la ruta normal. Cada trabajo posee su documento, impresora y painter; funciona dentro del worker de salida existente. Renderiza a 300 dpi, conserva proporciones, establece Carta y mantiene la orientación del documento. Las copias forman un único trabajo; si el controlador no admite copias múltiples, se generan dentro del mismo trabajo, en orden.

Antes de comenzar se valida el documento y se prepara la primera página. Se comprueban `begin`, `newPage`, renderizado, `end` y estado del controlador. Si falla un envío iniciado, se aborta y se registra el error: no se intenta imprimir automáticamente otra vez con otro motor. No se elimina ninguna página para acelerar General.

El respaldo de Sumatra sólo se usa si no hay aplicación Qt activa. Busca el ejecutable empaquetado y usa una sola orden `-print-settings paper=letter,fit,Nx`, con comprobación de código de salida y timeout de 30 s. macOS/Linux reciben Carta y todas las copias en una orden.

`admission_pdf_layout.py` ajusta proporcionalmente el documento de Admisión completado a Carta. El ajuste se aplica después de colocar los datos; así conserva la alineación entre formulario y contenido. No modifica las plantillas originales ni PDFs históricos. La transformación registra su stream como objeto indirecto: la prueba verifica tanto la estructura como lectura del nombre sintético mediante Qt PDF, además de la revisión visual con Poppler.

La impresión de la atención se envía antes de abrir el visor y antes del Excel. Un fallo del visor no reimprime el documento ni revoca su estado de generación correcto. Los dispositivos de impresión de comparación, factura HTML y cierre en `CALCULOS_QT.py` también parten de Carta; los reportes horizontales conservan su orientación.

## Archivos de aplicación modificados

- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`
- `CALCULOS_QT.py`
- `pdf_printing.py` (nuevo)
- `admission_pdf_layout.py` (nuevo)

Pruebas/benchmark: `test_pdf_printing.py`, `test_admission_letter_printing.py`, `test_billing_letter_paper.py`, `benchmark_admission_printing.py` y actualización de `test_pdf_output_performance_20260822.py`.

## Mediciones

Cinco ejecuciones por especialidad, datos sintéticos y dispositivo de salida Qt a PDF. Se incluye la creación/configuración del dispositivo en el tiempo de impresión. No se enviaron hojas a una impresora física.

| Hoja | Generación media | Impresión a PDF media | Máximo impresión a PDF |
| --- | ---: | ---: | ---: |
| General, 3 páginas | 47,2 ms | 1.980,5 ms | 2.433,2 ms |
| Pediatría | 47,2 ms | 647,8 ms | 686,5 ms |
| Ginecología | 171,3 ms | 526,2 ms | 584,4 ms |

Evidencia: `output/letter-printing/samples/benchmark.json`. Estos tiempos miden renderizado/entrega al dispositivo de prueba, no salida de papel ni rendimiento de un driver hospitalario. No son una comparación física antes/después.

## QA y regresión

Suite relacionada final: **148 PASS, 0 FAIL, 0 SKIPPED**, una advertencia por deprecación preexistente de PyPDF2; 17,78 s. Incluye PDF/impresión, papel en recibos/reportes, integridad y snapshots, compatibilidad histórica, cierre/Excel y contención SQLite. Evidencia: `output/letter-printing/tests.xml` y `tests.log`.

La prueba de caracterización anterior a la corrección confirmó Ginecología A4 y ausencia de impresión nativa (2 PASS / 2 FAIL); evidencia `before.xml`. Un primer intento usó D:, que devolvió un error de hardware; el QA se trasladó a un directorio privado temporal de C:. Las pruebas SQLite no se ejecutan dentro de OneDrive. No se hizo QA destructivo contra una base productiva.

Casos cubiertos: Carta en todas las páginas, lectura efectiva del contenido de Ginecología, errores de documento/controlador/render/página/finalización, copias con y sin soporte del driver, fallo del visor después de imprimir, ausencia de impresora, motor de respaldo ausente/rechazado, impresión dentro del worker y conservación del flujo de salida.

QA visual: renderizado con Poppler de PDFs generados e impresos. Se revisaron General, Pediatría y Ginecología con datos sintéticos; Ginecología conserva el formulario completo en Carta. Los encabezados y posiciones de los formularios existentes no se rediseñaron.

Windows emitió diagnósticos SEH `0x80040155` durante la enumeración de impresoras del equipo de QA; los procesos continuaron y las pruebas finalizaron con código 0. Se conservan en el log y no se presentan como una validación del controlador físico del hospital.

## Calidad

| Gate | Resultado | Alcance |
| --- | --- | --- |
| Funcionalidad y límites automatizados | PASS | Carta, copias, errores, lectura real, orden de envío |
| Unitarias/integración/regresión aplicable | PASS | 148 pruebas; dispositivos PDF y bases temporales |
| Cobertura | PASS | Los dos módulos nuevos: 100 % líneas y ramas con coverage.py; no es cobertura del monolito |
| Lint/análisis estático | PASS | Ruff en módulos/pruebas nuevos; 0 diagnósticos nuevos frente a HEAD en ambos monolitos |
| Formatter | PASS | Seis archivos nuevos; sin reformateo masivo de módulos heredados |
| Tipos aplicables | PASS | Mypy `--check-untyped-defs pdf_printing.py admission_pdf_layout.py`; tipos integrales del monolito NO VERIFICADOS |
| Complejidad del cambio | PASS | Módulos nuevos máximo 8; wrapper de impresión 43→9; respaldo 9; POSIX 3; coordinador 9→10. Renderizador heredado 25 sin nueva complejidad |
| Duplicación nueva | PASS | jscpd, 0 clones / 0 % en ambos módulos nuevos |
| Compilación Python | PASS | Cuatro archivos de aplicación |
| Build | PASS | PyInstaller `build_app.spec`; salida 0, ejecutables de QA en perfil temporal |
| Smoke del ejecutable final | PASS | `--check-v15-package`: Admisión, Historial y configuración; `--self-test-pdf`: recibo sintético; ambos salida 0 |
| Seguridad focalizada | PASS | Parámetros de procesos como listas, sin shell; logs nuevos sin nombre/NSS/cédula; no fallback después de envío fallido |
| Impresora física hospitalaria | NO VERIFICADO | No está conectada a este entorno |

Comandos de validación y resultados reales se registran bajo `output/letter-printing/`. La cobertura se mide con `coverage run --branch`; los resultados de QA no implican prueba de dos estaciones o impresión hospitalaria.

Las líneas modificadas ejecutables de V15 (39/39) y Facturación (4/4) también se ejecutaron; las 12 ramas modificadas medidas de V15 están cubiertas. Evidencia: `coverage.json` y `diff-coverage.json` (recalculado tras el cambio final). Se completaron las cuatro pasadas de funcionalidad, regresión, revisión de código y QA; la prueba física permanece pendiente.

Build reproducido con Python 3.14:

```text
py -3.14 -m PyInstaller --noconfirm --clean --log-level WARN --distpath %LOCALAPPDATA%/SIGEH_Print_QA_20261022/dist --workpath %LOCALAPPDATA%/SIGEH_Print_QA_20261022/build build_app.spec
```

Después de ajustar la organización de imports y extraer el envío POSIX, se ejecutó el mismo comando sin `--clean`. Ambas compilaciones terminaron con código 0. Se comprobaron los imports nuevos en el archivo PYZ y los CLI de diagnóstico sobre el binario final. Logs: `build.log` / `build-final.log`. El smoke usa un perfil aislado, directorio de trabajo separado y URL deliberadamente inválida en loopback; no inicia sesión ni registra pacientes.

SHA-256 del `CALCULOS_QT.exe` final de QA: `8E332117693E1D6D30E992786A3A416FF062ACE9E01198CBDFA1459F321C6B54`. Ruta: `%LOCALAPPDATA%/SIGEH_Print_QA_20261022/dist/SIGEH`. Evidencia de smoke: `%LOCALAPPDATA%/SIGEH_Print_QA_20261022/smoke/v15-final.json` y `receipt-final.pdf`. No es un paquete publicado ni una instalación hospitalaria actualizada.

## Conservación del comportamiento existente

La atención debe estar persistida antes de comenzar la salida, igual que antes. Se conservan la identidad, copias configuradas, worker, cola de salida, limpieza temporal, PDF original de archivo y Excel. No se modificaron pacientes, recibos persistidos, turnos, PRIMARY, representante, configuración productiva ni reglas de sincronización. No se cambió la impresora predeterminada de Windows.

## Validación física pendiente

Con el ejecutable validado, imprimir una hoja de General, Pediatría y Ginecología en la impresora hospitalaria. Comprobar tamaño Carta sin selección manual, contenido completo, copias y tiempo desde la orden hasta la cola y hasta el movimiento de papel. Los eventos `PDF_PRINT_START`, `PDF_PRINT_DISPATCH` y `PDF_PRINT_SUBMITTED` permiten separar demora de la aplicación de demora del controlador/impresora. Un tiempo físico de 20 s aún reproducible requiere medir esa etapa; no se declara eliminado por una prueba a PDF.

ESTADO FINAL: NO APROBADO PARA ENTREGA — validación física de impresión pendiente.
