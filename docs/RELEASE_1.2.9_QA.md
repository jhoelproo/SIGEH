# SIGEH 1.2.9 — validación de entrega

## IMPLEMENTACIÓN

Archivos de aplicación: `CALCULOS_QT.py`, `workspace_design.py`, `workspace_selection.py`, `workspace_accents.py`, `billing_workspace_design.py`, `catalog_workspace_design.py`, `billing_items_design.py`, `monthly_workspace_design.py`, `receipt_history_design.py`, `receipt_list_consistency.py`, `receipt_authorization_dialog.py`, `pdf_engine/renderer.py`, `pdf_engine/template.html`, `pdf_engine/styles.css`, `sigeh_product.py` y `version_config.json`.

Se incluyen el rediseño y controles de Facturación, Historial y Listados ARS; botones y estados con la paleta aprobada; catálogo sin pintura superpuesta; cantidades menos/más; NSS visible y destacado; autorización rápida; sincronización de identificación/autorización/especialidad entre recibo y expedientes editables; recuperación de sesión sin aviso repetitivo. Las reglas financieras, los permisos, la selección y los callbacks se conservan en el ajuste visual. Los expedientes emitidos mantienen su copia histórica.

Los requerimientos anteriores y sus pruebas de caracterización están detallados en `QA_WORKSPACE_REDESIGN_2026-10-05.md`, `QA_NSS_POSITION_COLOR_2026-10-05.md`, `QA_BILLING_ITEMS_VISUAL_2026-10-05.md` y `QA_WORKSPACE_ACCENTS_2026-10-05.md`.

## PRUEBAS

Pruebas nuevas: `tests/test_workspace_design.py`, `test_workspace_accents.py`, `test_billing_items_presentation.py`, `test_receipt_list_consistency.py`, `test_receipt_list_consistency_postgres.py`, `test_receipt_authorization_dialog.py`, `test_receipt_metadata_policy.py`, `test_login_silent_recovery.py`, `test_receipt_nss_design.py` y `test_receipt_nss_layout.py`. `test_sigeh_update.py` exige versión 1.2.9 y compara con 1.2.10.

Unitarias, eventos Qt, integración PostgreSQL local, documentos PDF reales, archivos, conflictos, rollback y actualización de instalación. Límites: campos vacíos, ceros iniciales, autorizaciones de 3/4/40/41 dígitos, caracteres inválidos, identificación extensa, cero filas, primera/última página, cantidades límite, permisos y estados deshabilitados.

RED de versión: una prueba falló con 1.2.8; GREEN: 22 pruebas del actualizador aprobadas. Los RED funcionales y visuales anteriores se conservan en los informes específicos.

## RESULTADOS

Regresión global: PASS; 2.252 casos recolectados y comprobados sin casos faltantes o adicionales. Resultado efectivo: 2.251 aprobados, cero fallos/errores y una omisión optativa de capacidad real, más 74 subpruebas aprobadas: 2.325 comprobaciones aprobadas de 2.326. La ejecución se divide en doce grupos con procesos aislados y un fingerprint común. Los grupos completados antes de una interrupción se conservan; solo se retoman los pendientes. El gestor de importación conserva una referencia viva a QApplication durante su módulo.

Seis casos de papelera se omitieron inicialmente porque el servidor local de demostración quedó detenido durante la interrupción. Se restableció únicamente ese servidor local y se reejecutaron los seis casos con bases temporales: todos PASS. El resumen efectivo sustituye esos seis resultados omitidos por sus ejecuciones reales, sin borrar los JUnit originales. Evidencia: `output/release-129-full-summary.json`, `release-129-full-suite-completeness.json`, doce JUnit `release-129-group-*.xml` y tres JUnit `release-129-trash-rerun-*.xml`.

Medición de cobertura: 224 pruebas aprobadas. Integración adicional: 81 aprobadas. Se solapan con la suite global y no se suman. Un intento inicial de medición cerró Qt durante el teardown; no se contó como aprobado. La repetición con QApplication viva pasó sin modificar el código de aplicación.

## COBERTURA

Medida con Coverage.py y ramas, sobre diez módulos nuevos completos y las sentencias modificadas semánticamente del código heredado, sin contar cambios de formato:

| Alcance | Líneas | Ramas |
| --- | ---: | ---: |
| Diez módulos nuevos | 100 % | 100 % |
| Todo el alcance semántico modificado | 100 % | 99,24 % |
| Archivo principal, sentencias modificadas | 133/133, 100 % | 36/38, 94,74 % |
| Cuatro funciones críticas de corrección/documento | 49/49, 100 % | 16/16, 100 % |
| Constante de versión modificada | Ejecutada | N/A, sin condición |

Evidencia: `output/release-129-coverage.json`, `release-129-coverage-summary.json`, `release-129-critical-coverage.json`; mediciones unitarias, de integración y de pantallas reales combinadas. No se atribuye ese porcentaje al archivo principal completo.

## CALIDAD

Ruff lint/formatter: PASS; mypy: cero errores nuevos; Bandit: cero hallazgos; Pylint duplicate-code, mínimo seis líneas: sin bloques nuevos detectados. Radon: máximo 9 por función en los módulos nuevos. `git diff --check`: PASS. Evidencia: `output/release-129-static-static-summary.json`, logs estáticos y `release-129-complexity.json`.

Se preservan las excepciones de formato y complejidad heredada explicadas en `QA_WORKSPACE_REDESIGN_2026-10-05.md`: seis funciones grandes sin formato masivo y orquestación existente que supera los objetivos de complejidad. No se introducen funciones nuevas con complejidad superior a 10. Los diagnósticos heredados no se ocultan ni se debilitan configuraciones. No se afirma un porcentaje global de duplicación del código heredado.

## BUILD

```powershell
python -X utf8 -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/release-129-app --workpath build/release-129-app build_app.spec
python -X utf8 -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/release-129-updater --workpath build/release-129-updater build_updater.spec
python -X utf8 release_packaging.py --dist output/release-129-app/SIGEH --updater output/release-129-updater/SIGEH_Updater.exe --output output/release-1.2.9 --version 1.2.9
```

Los tres comandos terminaron con salida 0: PASS. Se comprobó correspondencia de los módulos compilados y de plantilla/CSS/versiones con las fuentes finales. Las advertencias heredadas de imports opcionales `mypyc` no impidieron el build ni los smoke tests.

## QA

- Seis smoke tests del ZIP extraído: lanzador, ayuda del actualizador, paquete de Admisión v15, PDF, visor y reportes; todos con salida 0. Cuatro smoke adicionales del build previo al ZIP también aprobados.
- ZIP íntegro y 1.822 archivos verificados por tamaño y SHA-256. Sin configuración privada, credenciales, bases operativas o logs. SHA-256 del ZIP: `5b7b062912da94d31573d4d1854861a191b10a22c4dc493262450ca5f69867f3`.
- Actualización 1.2.8→1.2.9 usando código del actualizador extraído del ejecutable instalado; segundo ciclo usando el actualizador empaquetado de 1.2.9 y reaplicando el mismo paquete. Conexión cifrada e historial sintético de dos pacientes idénticos byte por byte. Health checks del ejecutable reales; callback final de reapertura sustituido para evitar acceso a producción. Este ensayo no equivale a construir una versión futura.
- Pantallas nativas Windows con datos ficticios, temas claro/oscuro y anchos 1366/1680/1920. Capturas revisadas en `output/release-129-visual-qa-windows/`. El primer renderizador offscreen mostró fuentes ausentes; se repitió con el renderizador nativo y ventanas ocultas, sin cambiar la aplicación. Se conservan scroll y tooltips en espacios reducidos.
- Seguridad: permisos y consultas parametrizadas, conflicto entre estaciones, rollback, listas de valores permitidos para ordenar, escape de texto y conservación de copias emitidas. QA sin operaciones destructivas contra producción.

Evidencia del paquete: `output/release-129-package-validation.json`; fuentes finales: `release-129-final-fingerprint.json`. Los artefactos de `output/` son evidencia local y no forman parte del paquete público.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad, unitarias, integración, límites | PASS |
| Regresión global | PASS |
| Coverage, formatter, lint, tipos, análisis estático | PASS |
| Complejidad y duplicación, con alcance/excepciones anteriores | PASS |
| Build, smoke, paquete, actualización, seguridad y QA visual | PASS |
| Canal público y assets remotos | NO VERIFICADO, publicación pendiente |
| Prueba optativa de capacidad contra una base real | N/A, ajena al cambio y no habilitada contra producción |
| Instalación en hospital e impresión física | NO VERIFICADO, sin acceso a esas estaciones/impresora |

Cuatro pasadas: funcionalidad PASS; regresión global PASS; clean code PASS; QA local PASS. La aprobación final de entrega requiere verificar la publicación. Las fuentes siguen idénticas al fingerprint después de todas las comprobaciones.

## PROBLEMAS PENDIENTES

Pendiente publicar/verificar el canal estable. Deuda técnica y advertencias heredadas documentadas; instalación efectiva en hospital e impresión física no comprobadas. La publicación no confirma que las estaciones ya estén actualizadas.

ESTADO FINAL: NO APROBADO PARA ENTREGA (verificación de publicación pendiente; código y paquete aprobados).
