# SIGEH 1.3.6 · QA de producción

Fecha: 5 de noviembre de 2026. ESTADO FINAL: APROBADO PARA ENTREGA.

## IMPLEMENTACIÓN

Se publican los cambios ya validados de turno, historial, intervalos de fechas, geometría de ventanas, administración y fusión de ARS, eliminación del catálogo y materiales únicos por recibo. La preparación de esta release solo añade la versión 1.3.6, sus expectativas de prueba y documentación.

Archivos de aplicación: `CALCULOS_QT.py`, `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`, `ADMISION_PYSIDE6_V15/qt_compat.py`, `admission_contract.py`, `admission_v15_adapter.py`, `admission_visit_dates.py`, `billing_material_cart.py`, `medication_material_dialog.py`, `ars_management.py`, `ars_manager_workspace.py`, `catalog_removal.py`, `admission_history_layout.py`, `sigeh_product.py` y `version_config.json`.

Pruebas: seis archivos existentes del catálogo/materiales adaptados a la nueva regla, nueve archivos nuevos `test_nov04_*`/`test_nov05_history_viewport.py` y `tests/test_sigeh_update.py` actualizado para 1.3.6. Notas: `docs/RELEASE_1.3.6.md` y este informe.

Se preservan permisos efectivos, identificadores, estados, importes históricos, perfiles fiscales y tarifas del destino. La publicación no ejecuta fusiones o eliminaciones del hospital. Sin cambios adicionales en impresión, reglas contables o credenciales.

## PRUEBAS Y RESULTADOS

Los lotes se solapan y no se suman como pruebas distintas.

| Lote ejecutado | Resultado |
|---|---|
| Regresión relacionada de la implementación | 678 passed + 29 subtests; cuatro errores iniciales de preparación PostgreSQL. Los cuatro casos pasaron al iniciar el entorno local desechable. Los 682 casos quedaron verificados. |
| Admisión final | 136 passed, 0 failed. |
| Interfaz final | 17 passed, 0 failed. |
| Historial nativo y fechas | 26 passed, 0 failed. |
| Release, actualización, preservación, recuperación y compatibilidad de contraseñas | 133 passed, 0 failed, 0 skipped. |

Unitarias y condiciones de borde: listas vacías, carga/error/reintento, permisos, origen/destino inexistentes, selección obsoleta, fechas inválidas e invertidas, extremos inclusivos y materiales manuales/automáticos compartidos. Integración: PostgreSQL desechable, lectura desde segunda conexión, DELETE real, auditoría y rollback. Nunca QA destructivo en producción.

TDD registrado para los errores de controles, calendario, superposición, contraste y nueva versión. La prueba de versión falló con 1.3.5 y pasó tras el incremento a 1.3.6.

Evidencia local bajo `output/`: `nov04-regression.log`, `nov05-pg-retry.log`, `nov05-final-admission.log`, `nov05-final-ui.log`, `nov05-final-history.log`, `release-136-tests.log`, `release-136-update-tests.xml` y `release-136-version-red.log`.

## COBERTURA

coverage.py con ramas, sobre los cambios semánticos respecto de la base:

- Funcionalidad: 615/616 líneas, **99.84%**; 146/150 ramas, **97.33%**. Supera también el objetivo crítico 95%/90%.
- Cuatro módulos nuevos: 254/254 líneas y 44/44 ramas, **100%**.
- Incremento de versión adicional: 1/1 línea, **100%**; ramas N/A porque es una asignación constante.

No se atribuye esa cobertura a toda la aplicación heredada. Evidencia: `nov04-coverage-summary.json`, `nov04-coverage.json`, `release-136-version-coverage.json` y `release-136-version-coverage-summary.json`.

## CALIDAD

- Formatter PASS: archivos nuevos/pruebas y rangos modificados de archivos heredados.
- Ruff PASS: cero errores nuevos.
- mypy PASS: cero errores nuevos mediante comparación con la base y shadow-file; módulos nuevos sin errores.
- Bandit/análisis estático PASS: cero hallazgos nuevos.
- Radon PASS: complejidad máxima 10 en funciones nuevas; sin incremento en funciones grandes heredadas. El cambio de versión no añade funciones ni ramas.
- Duplicación PASS: Pylint no detectó bloques nuevos duplicados de seis líneas. No se inventa un porcentaje global.
- Higiene PASS: `git diff --check`.

Evidencia: `nov04-quality-quality.json` y `release-136-quality-quality.json`. El gate de diseño Antislop de la implementación se documentó por regla en `output/nov05-delivery-report.md`; no se rediseñó interfaz durante el empaquetado de producción.

## BUILD

```text
python -m PyInstaller --noconfirm --distpath output/release-136-app --workpath output/release-136-work output/nov04-collect.spec
python -X utf8 release_packaging.py --dist output/release-136-app/SIGEH --updater output/nov04-build/SIGEH/SIGEH_Updater.exe --output output/release-1.3.6 --version 1.3.6 --base-archive output/release-1.3.5/SIGEH-1.3.5-windows-x64.zip
```

PASS. Trece módulos empaquetados comprobados contra la fuente final. Auxiliar de actualización sin cambios reutilizado del paquete estable verificado. Metadatos internos y externos 1.3.6.

| Artefacto | Tamaño en bytes | SHA-256 |
|---|---:|---|
| ZIP completo | 310313515 | `5a20d18c547248a2559745724e84c3f85924814ee087ab1cb8b7f56251d2461f` |
| Incremental 1.3.5 → 1.3.6 | 36189935 | `051da21feb0a981a54341246278e843b885e6edb82326b8b64f17f5e6149c5e2` |

Se verificó también el SHA-256 del ZIP base 1.3.5 publicado. Se entregan cinco assets: completo, checksum, manifest, índice recursivo e incremental.

## QA

Cuatro pasadas: funcionalidad por requisito, regresión relacionada, clean code y comprobaciones finales de entrega.

- Seis smoke tests de ejecutables, todos con salida 0: lanzador, auxiliar, integración V15, PDF, visor y reportes.
- Guard público PASS, 1827 archivos. Sin bases operativas, credenciales ni logs de prueba. El guard detectó una cola vacía y un log creados por el smoke; se verificó que la cola no contenía registros y se retiraron de la distribución antes de empaquetar.
- CRC de ambos ZIP y SHA-256 de cada archivo del índice recursivo PASS.
- Reconstrucción del incremental real PASS: todos los archivos coinciden con el objetivo completo.
- Instalación real del payload 1.3.6 sobre una copia sintética de 1.3.5, respaldo y health checks de los ejecutables PASS. Solo se sustituyó el lanzamiento final interactivo por un registro de llamada.
- Conservación byte por byte de ocho archivos de configuración y datos sintéticos PASS: nombre, horarios, seguridad, logos, usuarios e historial.
- Una primera ejecución aislada dentro de OneDrive fue rechazada correctamente por la protección de réplica privada y revirtió a la versión anterior. La prueba final se ejecutó en Temp local privado y pasó. No se debilitó esa protección.
- GUI de implementación PASS a 1024/1280/1920, claro/oscuro, búsqueda/foco, calendario visible y acciones sin superposición. Contraste de los pares evaluados superior a 4.5:1; no es una certificación global de accesibilidad.

Evidencia: `release-136-build-validation.json`, `release-136-package-validation.json`, `release-136-build.log`, `release-136-smoke.log`, `release-136-package-qa.log`, `nov05-contrast.json` y capturas `nov05-history-*`/`nov04-ars-*`.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias, regresión e integración | PASS |
| Cobertura y condiciones de borde | PASS |
| Formatter, lint, tipos y análisis estático | PASS |
| Complejidad y duplicación nueva | PASS |
| Build, paquete completo e incremental | PASS |
| Smoke, preservación, seguridad y QA final | PASS |
| Nuevas migraciones destructivas | N/A: no se requieren ni se ejecutaron. |

## PROBLEMAS PENDIENTES

Sin fallos pendientes detectados en el alcance validado. Permanecen avisos heredados de deprecación de PyPDF2/ttkbootstrap. No se afirma una prueba física simultánea en dos estaciones del hospital ni una nueva prueba de impresora, cuya lógica no cambió en esta entrega.

El código de la release se etiqueta desde la rama validada `codex/admission-ars-materials-nov04`; la rama remota `main`, con una línea de desarrollo anterior diferente, se conserva sin sobrescritura. La publicación estable y la detección desde 1.3.5 se verifican después de subir todos los assets.
