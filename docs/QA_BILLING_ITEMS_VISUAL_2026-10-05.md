# Ajuste visual de catálogo y recibo — prueba local del 2026-10-05

## IMPLEMENTACIÓN

Alcance: únicamente la presentación del catálogo y del recibo en Facturación, siguiendo la referencia del usuario. Se conservaron las reglas de facturación, los precios, los permisos, la persistencia y las funciones existentes.

Archivos de aplicación modificados o añadidos en esta revisión:

- `catalog_workspace_design.py`: un solo renderizador para las filas, encabezados alineados y botones Añadir integrados. Se eliminó la pintura del delegado anterior que aparecía detrás de los controles nuevos.
- `billing_items_design.py`: presentación de categorías, importes, cantidades, encabezados y resumen. Los controles de cantidad reutilizan el editor y las señales originales.
- `billing_workspace_design.py`: integración y ajuste de tamaños de las dos tarjetas.
- `tests/test_billing_items_presentation.py`: regresiones visuales y de interacción.
- `tests/test_workspace_design.py`: verificación del ancho flexible del nombre del ítem.
- Este informe.

Los cambios anteriores de otras tareas se conservaron. No se modificó `CALCULOS_QT.py` durante este ajuste. La comparación recursiva del código compilado de ese módulo entre el ejecutable anterior y el nuevo fue PASS: `output/billing-items-final-verification.json`.

## PRUEBAS

Secuencia de regresión: reproducción específica, pruebas del módulo, módulos relacionados y suite completa aplicable a los componentes afectados. No se afirma una nueva ejecución de toda la suite global del repositorio.

- RED: cinco comprobaciones nuevas fallaron antes de la corrección; evidencia `output/billing-items-red.xml` y `.log`.
- Unitarias y Qt: renderizador único, encabezados, tema claro/oscuro, categorías conocidas/desconocidas/vacías, importes sin modificar el modelo, favoritos, ordenación, paginación, actualización del contador y resumen.
- Interacción: reutilización del editor, botones menos/más, teclado, conservación de señales e iconos y ausencia de duplicación de controles.
- Límites y errores: cantidades 1 y 300, valor próximo al máximo, cero filas, nombres largos, controles faltantes y tamaños estrechos.
- Integración visual: ventana real de aplicación, catálogo contributivo y PostgreSQL local con datos ficticios; capturas y comprobación de geometría.
- Regresión de negocio: dinero, validación, corrección de ARS, recibos editados, autorización, consistencia de listados y estados de extranjeros/sin seguro.

Comando de unitarias y cobertura:

```powershell
python -X utf8 -m coverage run --branch --source=billing_items_design,catalog_workspace_design,billing_workspace_design -m pytest tests/test_billing_items_presentation.py tests/test_workspace_design.py tests/test_decimal_cart_gui.py tests/test_cart_quantity_theme_controls.py tests/test_catalog_favorites_responsive.py -q --junitxml=output/billing-items-unit.xml
python -X utf8 -m coverage json -o output/billing-items-coverage.json
python -X utf8 -m coverage report
```

Suite relacionada:

```powershell
python -X utf8 -m pytest tests/test_billing_items_presentation.py tests/test_workspace_design.py tests/test_decimal_cart_gui.py tests/test_cart_quantity_theme_controls.py tests/test_catalog_favorites_responsive.py tests/test_display_layout.py tests/test_billing_money.py tests/test_billing_field_policy.py tests/test_billing_field_policy_qt.py tests/test_billing_validation_ui.py tests/test_billing_edit_failure_ui.py tests/test_receipt_ars_correction.py tests/test_receipt_ars_correction_qt.py tests/test_self_pay_billing.py tests/test_self_pay_billing_ui.py tests/test_self_pay_pending.py tests/test_receipt_edit_integrity.py tests/test_monthly_ars_form_state.py tests/test_receipt_list_consistency.py tests/test_receipt_authorization_dialog.py tests/test_receipt_metadata_policy.py -q --junitxml=output/billing-items-regression.xml
```

## RESULTADOS

| Ejecución | Passed | Failed/errores | Skipped |
| --- | ---: | ---: | ---: |
| Unitarias finales | 56 | 0 | 0 |
| Suite relacionada final | 378 pruebas + 42 subpruebas | 0 | 0 |
| Smoke del ejecutable | 4 | 0 | 0 |

Las 56 pruebas están incluidas en la suite relacionada; no se suman como casos únicos adicionales. El JUnit de la suite relacionada registra 420 casos contando las subpruebas. Duración de pytest: 9,22 segundos. Una advertencia heredada de deprecación de PyPDF2.

Evidencia: `output/billing-items-unit.xml`, `output/billing-items-regression.xml`, sus registros y `output/billing-items-build-verification.json`.

## COBERTURA

Medición real con coverage.py, con ramas habilitadas y sin exclusiones:

| Módulo de presentación | Líneas | Ramas |
| --- | ---: | ---: |
| billing_items_design.py | 205/205 — 100 % | 38/38 — 100 % |
| catalog_workspace_design.py | 201/201 — 100 % | 30/30 — 100 % |
| billing_workspace_design.py | 151/151 — 100 % | 32/32 — 100 % |
| Total medido | 557/557 — 100 % | 100/100 — 100 % |

La cobertura supera los gates del código modificado. No se modificaron reglas financieras críticas; las interacciones nuevas de presentación están dentro de la medición. Evidencia: `output/billing-items-coverage.json`.

## CALIDAD

- Formatter: PASS, Ruff; cinco archivos ya formateados.
- Lint: PASS, Ruff; sin errores nuevos en los cinco archivos.
- Type checker: PASS, mypy sobre los tres módulos de aplicación; comprobación del test nuevo también PASS.
- Static analysis: PASS, Ruff, mypy y Bandit en el alcance modificado.
- Complejidad: PASS, Radon; máximo 9 en el módulo nuevo, 8 en catálogo y 8 en integración de Facturación.
- Duplicación: PASS, detector de código duplicado de Pylint, sin hallazgos con umbral de seis líneas. No se estima un porcentaje global del repositorio.
- Espacios y conflictos: `git diff --check` PASS.
- Seguridad: Bandit sin hallazgos en los tres módulos. No se introdujeron consultas SQL, credenciales, comandos ni cambios de permisos.

Comprobaciones finales de formato y lint:

```powershell
python -X utf8 -m ruff check billing_items_design.py catalog_workspace_design.py billing_workspace_design.py tests/test_billing_items_presentation.py tests/test_workspace_design.py
python -X utf8 -m ruff format --check billing_items_design.py catalog_workspace_design.py billing_workspace_design.py tests/test_billing_items_presentation.py tests/test_workspace_design.py
git diff --check
```

Evidencia de las herramientas reales: `output/billing-items-static.json`, `billing-items-complexity.json`, `billing-items-security.json`, `billing-items-duplication.log` y registros de formato/lint/tipos.

## BUILD

```powershell
python -X utf8 -m PyInstaller --noconfirm --distpath output/billing-items-build --workpath output/billing-items-build-cache build_app.spec
```

PASS; 253,841 segundos. Ejecutable: `output/billing-items-build/SIGEH/CALCULOS_QT.exe`. Registro: `output/billing-items-build.log`.

Se comprobó que diez módulos empaquetados y los archivos HTML/CSS del PDF coinciden con las fuentes actuales. Smoke del build, cuatro PASS con salida 0: lanzador, PDF, visor de reportes y generación de reportes. Evidencia: `output/billing-items-build-verification.json` y `output/billing-items-build-smoke/`.

## QA

- PASS: capturas nativas de Qt en oscuro a 1366×768, 1680×950 y 1920×1080, y claro a 1366×768; inspección visual de cada tamaño y tema.
- PASS: sin pintura superpuesta del catálogo y sin desplazamiento horizontal del recibo en los tres tamaños. Nombres largos conservan tooltip; los textos pueden abreviarse en ventanas estrechas.
- PASS: categorías con color, cantidades legibles, importes y resumen actualizados; botones y editor conservan su comportamiento.
- PASS: comprobaciones visuales de las pantallas relacionadas de historial y listado.
- PASS: apertura visible del ejecutable nuevo el 2026-10-05 a las 14:18, proceso 20316, ventana `Bienvenido - Hospital Provincial`, respondiendo. Bootstrap confirmó `BOOT_STAGE_OK stage=UI_READY` a las 14:18:19.
- PASS: entorno local de prueba, PostgreSQL `127.0.0.1:55432/sigeh_self_pay_visual`, datos ficticios y directorios bajo `output/self-pay-interactive/`.

Cuenta de prueba: `vista_local`, contraseña `VistaLocal2026`. La ventana queda en el inicio de sesión para que el usuario entre y revise Facturación.

Capturas: `output/billing-items-visual-qa/items-1366.png`, `items-1680.png`, `items-1920.png`, `billing-light-1366.png`. Evidencia de inicio: `output/self-pay-interactive/items-preview-launch.json` y `output/billing-items-final-verification.json`.

Cuatro pasadas finales realizadas:

1. Funcionalidad: alcance visual de las dos tarjetas, referencias, renderizador único y controles integrados — PASS.
2. Regresiones: reglas financieras y señales conservadas, suite relacionada y módulo principal compilado sin cambios — PASS.
3. Clean code: responsabilidades separadas, estilos acotados, complejidad medida y sin duplicación detectada — PASS.
4. QA: unitarias, integración Qt, cobertura, análisis, build, smoke y revisión de capturas — PASS.

## QUALITY GATES

| Gate | Estado | Alcance |
| --- | --- | --- |
| Funcionalidad | PASS | Presentación de catálogo y recibo |
| Unit tests | PASS | 56 pruebas finales |
| Regression tests | PASS | 378 + 42 subpruebas |
| Integration tests | PASS | Qt real, build, PDF/reportes y demo local |
| Coverage | PASS | 100 % líneas y ramas en los tres módulos |
| Boundary tests | PASS | Cantidades, vacíos, nombres largos y ancho |
| Formatter | PASS | Ruff, cinco archivos |
| Lint | PASS | Ruff, cero errores nuevos |
| Static analysis | PASS | Ruff, mypy y Bandit |
| Type checker | PASS | Tres módulos de aplicación y test nuevo |
| Complexity | PASS | Máximo 9 |
| Duplication | PASS | Sin hallazgos del detector real |
| Build | PASS | PyInstaller y comprobación del contenido |
| Smoke test | PASS | Cuatro auto pruebas y apertura visible |
| Security checks | PASS | Alcance modificado y entorno local |
| QA final | PASS | Cuatro pasadas y capturas revisadas |
| Migraciones/rollback de datos nuevos | N/A | No se cambió persistencia |
| Validación manual del usuario para producción | NO VERIFICADO | Pendiente de revisar esta prueba |
| Nuevo paquete público de actualización | NO VERIFICADO | Esta compilación local conserva versión 1.2.8 |

## PROBLEMAS PENDIENTES

La prueba local está lista. La aprobación visual del usuario y un paquete de publicación con nueva identidad de versión siguen pendientes antes del despliegue público. No se publicó a producción en esta tarea.

**ESTADO FINAL: APROBADO PARA ENTREGA** — alcance exclusivo de la prueba local solicitada. La aprobación para producción sigue **NO VERIFICADO**.
