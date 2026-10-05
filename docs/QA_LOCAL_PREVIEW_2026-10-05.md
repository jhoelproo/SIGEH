# Prueba local y evaluación de publicación — 2026-10-05

## IMPLEMENTACIÓN

- Se inició el ejecutable compilado `output/workspace-build/SIGEH/CALCULOS_QT.exe`, con los cambios de diseño, consistencia recibo/listado, autorización rápida y NSS destacado.
- Esta ejecución no modifica código de aplicación. Archivo añadido: este informe.
- Entorno exclusivo de prueba: PostgreSQL `127.0.0.1:55432`, base `sigeh_self_pay_visual`; PROGRAMDATA, LOCALAPPDATA, APPDATA y Admisión aislados bajo `output/self-pay-interactive/`.
- Cuenta ficticia: `vista_local` / `VistaLocal2026`. Se verificaron sus credenciales mediante la función real de aplicación.
- Se conservaron el catálogo y los datos previos de la demo. Se añadieron tres recibos ficticios y un expediente de SENASA CONTRIBUTIVO con las funciones reales de guardado/listado/corrección: un paciente listo y dos por revisar.
- No se publicó una actualización ni se realizaron pruebas sobre datos de producción.

## PRUEBAS

Comando ejecutado en esta revisión:

```powershell
python -X utf8 -m pytest tests/test_portable_launcher.py tests/test_release_packaging_fresh_install.py tests/test_sigeh_visual_login_updater_20260824.py -q --junitxml=output/local-preview-release-checks.xml
```

- Unitarias/regresión: resolución y conservación de configuración, lanzador, empaquetado e interfaz del actualizador.
- Integración local adicional: credenciales reales de demo, guardado de tres recibos, creación de expediente, inclusión de los tres y corrección NSS/autorización del primero. Se comprobó que solo uno cumple los requisitos de listo.
- Casos alternativos: autorización sin NSS sigue por revisar; recibo sin autorización sigue por revisar.
- Los casos de límites, rollback, conflictos, permisos y sincronización están en la QA previa del mismo código; no se atribuyen a una nueva ejecución de esta tarea.

## RESULTADOS

- Pruebas ejecutadas ahora: **40 PASS, 0 FAIL, 0 skipped**. Una advertencia heredada de deprecación de PyPDF2.
- Comprobaciones de preparación de la demo: PASS; tres incluidos, uno listo, dos por revisar.
- Evidencia: `output/local-preview-release-checks.xml`, `output/local-preview-release-checks.log`, `output/self-pay-interactive/compiled-preview-data.json` y `compiled-preview-launch.json`.
- Evidencia previa: suite aplicable de 2.245 casos, **2.244 PASS, 0 FAIL/error, 1 skipped** optativo; después del último ajuste HTML/CSS, **40 pruebas relacionadas PASS**. Consulte los informes enlazados al final para el alcance y los comandos originales.

## COBERTURA

- N/A para cambios de lógica en esta tarea: solo inicio, datos ficticios e informe.
- Medición previa real del código nuevo/modificado: **100 % líneas / 97,42 % ramas**; funciones críticas de consistencia **100 % / 100 %**. No se recalculó cobertura durante este inicio local.
- Evidencia previa: `output/workspace-coverage-summary.json` y `output/workspace-critical-coverage.json`.

## CALIDAD

- `git diff --check`: PASS en esta revisión.
- Formatter/lint/tipos/análisis estático: N/A para este informe; PASS previo para el código de aplicación modificado, sin errores nuevos. Excepciones heredadas de formato y complejidad documentadas en la QA del rediseño.
- Complejidad máxima nueva medida previamente: 10. Sin nueva lógica de aplicación en esta tarea.
- Duplicación: sin nuevos bloques de aplicación; análisis previo Pylint sin nuevos hallazgos. No se declara un porcentaje global estimado.

## BUILD

Build final ya ejecutado y comprobado:

```powershell
python -X utf8 -m PyInstaller --noconfirm --distpath output/workspace-build --workpath output/workspace-build-cache build_app.spec
```

- PASS, 251,207 segundos; no se recompiló porque no cambió el código desde ese build.
- Se verificó que nueve módulos y los archivos HTML/CSS empaquetados coinciden con las fuentes finales: `output/nss-badge-build-verification.json`.
- Versión que declara esta compilación: **1.2.8**. Aún no es un nuevo paquete de publicación con identificador propio.

## QA

- Inicio real de ejecutable visible: PASS. Proceso 19020; ventana `Bienvenido - Hospital Provincial`, Windows la reportó respondiendo.
- Inicialización real con PostgreSQL local: PASS. Registro `BOOT_STAGE_OK stage=UI_READY`; servicios operativos y sincronización inicializados correctamente.
- Smoke previo del build final: cuatro PASS, códigos de salida 0 — lanzador, PDF, visor y reportes.
- Regresión visual previa: Qt real, temas claro/oscuro, ventanas de 1366/1680; PDFs normales, largos y sin NSS. Último NSS: posición izquierda, contraste medido 8,71:1, seis páginas revisadas.
- Seguridad de esta ejecución: PASS; destino local comprobado antes de preparar ejemplos, datos ficticios y directorios aislados. No se desactivaron permisos de aplicación.
- Revisión manual del usuario, impresión física y jornada con las computadoras del hospital: **NO VERIFICADO**.

## QUALITY GATES

| Gate | Estado | Alcance |
| --- | --- | --- |
| Inicio local y datos para probar | PASS | Ejecutable real, bootstrap, cuenta y ejemplos |
| Unitarias/regresión de actualización | PASS | 40 pruebas ejecutadas ahora |
| Integración local de datos | PASS | Tres recibos y listado, uno listo/dos por revisar |
| Límites | PASS | Evidencia previa del cambio; sin nueva lógica |
| Coverage | PASS | Medición previa, alcance nuevo/modificado |
| Formatter/lint/tipos/static | PASS | Evidencia previa; sin código nuevo en esta revisión |
| Complejidad/duplicación | PASS | Código nuevo previo; excepciones heredadas documentadas |
| Build de aplicación | PASS | Build final y comparación de fuentes |
| Smoke local | PASS | Inicio real más cuatro smoke previos |
| Seguridad local | PASS | Base y datos aislados |
| QA automatizada del cambio | PASS | Evidencia previa y comprobaciones de esta revisión |
| Validación manual hospitalaria/impresión | NO VERIFICADO | Requiere probar en el entorno operativo |
| Número nuevo y paquete final de publicación | NO VERIFICADO | Compilación aún identificada como 1.2.8 |
| Actualización instalada hacia ese paquete final | NO VERIFICADO | El nuevo paquete todavía no se ha preparado |

## CUATRO PASADAS

1. Funcionalidad: ejecutable y ejemplos disponibles para probar. PASS.
2. Regresión: 40 pruebas de actualización y evidencia previa relacionada. PASS para el alcance ejecutado.
3. Clean code: ninguna modificación adicional de aplicación; evidencia previa conservada. PASS.
4. QA: arranque real y datos locales PASS; publicación final pendiente de los gates anteriores.

## PROBLEMAS PENDIENTES

Antes de publicar: completar revisión manual, asignar número de versión nuevo, preparar el paquete definitivo y comprobar instalación/actualización hacia ese paquete preservando configuración y datos.

Recorrido recomendado en la demo:

1. Entrar con `vista_local`; revisar Facturación, añadir ítems y guardar un recibo ficticio.
2. En Listados de ARS, abrir el expediente de SENASA CONTRIBUTIVO y probar filtros, selección múltiple, clic derecho y corrección de datos.
3. En Historial, añadir solo la autorización al paciente ficticio 3; comprobar que aparece al editar y en el listado, sin marcarlo listo mientras falten NSS/especialidad.
4. Corregir NSS y especialidad desde el listado, volver al recibo y abrir su PDF para comprobar persistencia, posición y legibilidad del NSS. Retirar varios seleccionados del listado y comprobar que los recibos siguen en Historial.

La demo local está disponible. La publicación en producción permanece pendiente.

**ESTADO FINAL: NO APROBADO PARA ENTREGA** — referido a la publicación en producción; faltan los gates explícitos de validación operativa y paquete final.

Informes previos: [QA del rediseño](QA_WORKSPACE_REDESIGN_2026-10-05.md), [QA de posición/color del NSS](QA_NSS_POSITION_COLOR_2026-10-05.md).
