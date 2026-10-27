# QA de SIGEH 1.3.5

Fecha: 2026-10-27. Alcance: actualizaciones incrementales, descargas reanudables y progreso. Compilación local; GitHub continúa en 1.3.4.

## IMPLEMENTACIÓN

Archivos de aplicación modificados:

- `update_download.py`: transferencias HTTP con caché por SHA-256, continuación, recuperación de 416, sustitución segura si el servidor ignora Range y rechazo de contenido corrupto.
- `update_payload.py`: extracción segura y reconstrucción completa en staging; valida hashes, rutas de Windows, archivos duplicados, enlaces y metadatos. Elimina su directorio de trabajo fallido después de verificar que está contenido en staging.
- `update_preparation.py`: selección del incremental, respaldo completo y formato de progreso.
- `release_delta.py`: comparación de índices por archivo, incremental y tratamiento de valores predeterminados personalizables.
- `sigeh_update.py`: descubrimiento y validación de incrementales del mismo repositorio y versión base. Conserva la validación del ZIP completo.
- `release_packaging.py`: generación opcional con `--base-archive`, manifest y quinto asset; conserva el paquete completo.
- `lanzador.py`: descarga y preparación en QThread, progreso limitado a diez eventos por segundo y entrega al instalador externo existente. Contraste del progreso según tema.
- `build_app.spec`: declara los tres nuevos módulos de ejecución como archivos requeridos.
- `sigeh_product.py`, `version_config.json`: versión 1.3.5.

Pruebas nuevas: `test_update_download.py`, `test_incremental_update.py`, `test_update_preparation.py`, `test_incremental_channel.py`, `test_update_download_ui.py`, `test_update_http_integration.py`. Actualizadas: `test_sigeh_update.py` y `test_oct23_update_controls.py` (versión y enlace de los métodos extraídos en su doble de prueba).

Preservado: reglas contables, recibos e identificadores, permisos, configuración, logo, usuarios, horarios, atención, impresión, reportes y sincronización. No hay migraciones ni QA contra producción.

## PRUEBAS

- Unitarias: caché válida/corrupta, hashes inválidos, tamaños desconocidos, contenido incompleto, 200/206/403/404/410/416/500, rangos inconsistentes, versión incorrecta, archivos ausentes y alterados, ZIPs inválidos, rutas peligrosas, enlaces y colisiones de nombres.
- Integración HTTP: servidor aislado en 127.0.0.1 interrumpe una respuesta. Primera invocación conserva 524.288 bytes; la segunda solicita `Range: bytes=524288-` y completa los 786.432 bytes sin duplicarlos.
- Integración del instalador: preservación y reversión ante health check fallido usando datos sintéticos. El paquete real reconstruye los 1.823 archivos y los compara con el índice completo.
- GUI: QTimer continúa funcionando mientras el QThread prepara archivos; señales, errores, recuperación a la app local, temas, progreso y entrega del payload. Capturas de ambos temas; texto medido dentro de la barra con Segoe UI.
- Regresión: suite completa del actualizador, preservación, primer salto, versiones anteriores, migración, contraseñas, impresión/PDF, reportes, continuidad, permisos, temas y recomendaciones.

## RESULTADOS

Últimas ejecuciones: 211 pruebas del actualizador y suites relacionadas; 305 de regresión ampliada. Total: **516 PASS, 0 FAIL, 0 SKIPPED**. Comandos y resultados en `output/download-runner.py`, `output/download-tests.log`, `output/download-verified-tests.xml`, `output/download-regression-runner.py`, `output/download-regression-repeat.log` y `output/download-regression-tests.xml`.

La primera ejecución ampliada obtuvo 304 PASS y 1 FAIL: `test_concurrent_processors_do_not_duplicate_evidence`, por `SerializationFailure` en la normalización compartida. El código de recomendaciones y su prueba son idénticos al tag publicado v1.3.4 (`git diff --exit-code v1.3.4 -- diagnosis_recommendation_store.py tests/test_diagnosis_recommendation_postgres.py`, exit 0). La repetición completa pasó 305/305. Se conserva el primer fallo en `output/download-regression-initial.xml` y `output/download-regression-tests.log`; no se modificó ni omitió la prueba. Investigar la condición intermitente preexistente queda fuera de este cambio.

Advertencias heredadas: deprecación PyPDF2 y `record_property` con JUnit xunit2. No se rebajaron umbrales ni se añadieron exclusiones.

## COBERTURA

Coverage.py con ramas, sobre módulos nuevos y funciones cuyo AST cambió respecto al estado inicial: **501/501 líneas, 100 %; 146/148 ramas, 98,65 %**.

| Archivo | Líneas | Ramas |
|---|---:|---:|
| lanzador.py (funciones modificadas) | 100 % | 92,86 % |
| release_delta.py | 100 % | 100 % |
| release_packaging.py (funciones modificadas) | 100 % | 100 % |
| sigeh_update.py (funciones modificadas) | 100 % | 100 % |
| update_download.py | 100 % | 100 % |
| update_payload.py | 100 % | 98 % |
| update_preparation.py | 100 % | 100 % |

No cubierto: entrega externa en plataforma distinta a Windows y condición defensiva que impide borrar un directorio de trabajo fuera de staging. Las rutas críticas de integridad, reanudación, instalación y reversión tienen pruebas. Evidencia: `output/.coverage-download`, `output/download-coverage.json`, `output/download-coverage-summary.json`.

## CALIDAD

PASS: Ruff formatter en módulos nuevos/pruebas y rangos modificados del código heredado; Ruff lint, Mypy, Bandit y análisis de duplicación Pylint sin problemas nuevos respecto al estado inicial. Complejidad máxima de funciones nuevas/modificadas: **10**; `DownloadWorker.run` baja de 14 a 3 y `prepare_release` de 15 a 9. Cero bloques de duplicación nuevos detectados.

La comprobación diferencial no declara limpio todo el código heredado. Evidencia ejecutada con `output/download-quality.py`; resultados y logs `output/download-quality-*`. No se han cambiado configuraciones de herramientas para ocultar hallazgos.

## BUILD

Python 3.14 y PyInstaller, PASS:

```
python -X utf8 -m PyInstaller --noconfirm --distpath output/download-app --workpath output/download-build output/download-collect.spec
python -X utf8 -m PyInstaller --noconfirm --distpath output/download-updater --workpath output/download-updater-build build_updater.spec
python -X utf8 release_packaging.py --dist output/download-app/SIGEH --updater output/download-updater/SIGEH_Updater.exe --output output/release-1.3.5 --version 1.3.5 --base-archive output/release-1.3.4/SIGEH-1.3.4-windows-x64.zip
```

La compilación usa el grafo de dependencias verificado de la distribución anterior y recompila los fuentes actuales. El validador comprueba los módulos compilados contra los fuentes, todos los hashes, CRC, ausencia de bases operacionales/credenciales, SumatraPDF y conservación de datos.

## QA

- PASS: seis smoke tests del paquete congelado: lanzador, ayuda del instalador, paquete V15, generación de recibo PDF, visor y reportes.
- PASS: instalación incremental real de 1.3.4 a 1.3.5 con el código compilado del instalador anterior; health check del ejecutable nuevo y comparación de ocho archivos preservados, dos bases SQLite y dos filas por base. El reinicio final se intercepta para evitar abrir una sesión real.
- PASS: controles de integridad, traversal, nombres reservados, enlaces y destinos ajenos a staging.
- PASS: UI de progreso en claro y oscuro. Evidencia: `output/download-ui-light.png`, `output/download-ui-dark.png`, `output/download-ui-qa.json`.
- PASS: cuatro pasadas finales de funcionalidad, regresiones, clean code y QA en el alcance de esta mejora.

Evidencia del paquete final: `output/download-package-validation.json` y `.log`. Resumen con hashes y tamaños: `RELEASE_1.3.5_EVIDENCE.json`.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad del actualizador | PASS |
| Unit tests y boundary cases | PASS |
| Integración HTTP, filesystem e instalador | PASS |
| Regresión aplicable | PASS (última ejecución completa; incidencia inicial documentada) |
| Coverage y código crítico | PASS |
| Formatter / lint / tipos / análisis estático | PASS (diferencial) |
| Complejidad / duplicación | PASS |
| Build / paquete / smoke tests | PASS |
| Seguridad aplicable | PASS |
| QA final del actualizador | PASS |
| Cambios de esquema o pruebas contra producción | N/A: no se modificó persistencia de negocio |
| Impresora física Canon | N/A: controlador y binario de impresión intactos; no se ensayó físicamente |
| Descarga del nuevo paquete desde GitHub/CDN | N/A para entrega local: no está publicado; descarga remota NO VERIFICADA, protocolo probado por HTTP local |

## PROBLEMAS PENDIENTES

- Publicación de 1.3.5 y validación posterior del canal remoto. La autorización de este trabajo fue implementar la mejora; no se cambió el release estable.
- Instalar una vez el cliente nuevo mediante el paquete completo en equipos con 1.3.4 o anteriores. El porcentaje ahorrado corresponde al paquete construido; depende de los archivos que cambien en cada versión y no garantiza velocidad del proveedor/CDN.
- Investigar el fallo intermitente preexistente de concurrencia de recomendaciones descrito arriba.

La aprobación corresponde a la entrega local del actualizador. La publicación y su verificación remota permanecen pendientes.

**ESTADO FINAL: APROBADO PARA ENTREGA**
