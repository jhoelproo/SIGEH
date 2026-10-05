# Color de botones, historial y Listados ARS

## IMPLEMENTACIÓN

`workspace_accents.py` concentra la paleta y presentación de tarjetas, botones y estados. `workspace_design.py`, `billing_workspace_design.py`, `monthly_workspace_design.py` y `receipt_history_design.py` la aplican. Dos líneas de `CALCULOS_QT.py` conservan estos estilos al cambiar de tema. `tests/test_workspace_accents.py` verifica interacción y presentación.

Verde para acciones de creación y estados listos; azul para navegación y facturados; ámbar para pendientes; violeta para historial y reportes; rojo para acciones de retirada. Gradientes suaves, foco, hover y estados deshabilitados en temas claro/oscuro. Se conservan textos, selección, señales, permisos, datos y cálculos originales. No se cambian las reglas de negocio por el color.

## PRUEBAS Y RESULTADOS

105 pruebas de los módulos visuales: PASS, cero fallidas u omitidas (`output/workspace-accents-unit.xml`). Regresión relacionada: 478 pruebas y 42 subpruebas aprobadas, cero fallos u omisiones (`output/workspace-accents-regression.xml`). Los conjuntos se solapan y no se suman. Cinco comprobaciones reprodujeron el problema antes del ajuste (`output/workspace-accents-red.xml`). Se probaron modelos vacíos, valores largos, estados negativos, ancho reducido, temas y callbacks existentes.

## COBERTURA

Coverage.py con ramas: 725/725 líneas y 112/112 ramas, 100 %/100 %, sobre los cinco módulos de color/disposición. Las dos líneas nuevas del archivo principal están ejecutadas. Evidencia: `output/workspace-accents-coverage.json` y `workspace-accents-hook-coverage.json`. La lógica crítica de recibos mantiene la cobertura documentada en `QA_WORKSPACE_REDESIGN_2026-10-05.md`.

## CALIDAD

Ruff lint y format: PASS. Radon: máximo 8 en los módulos afectados, máximo 5 en el módulo nuevo. Pylint duplicate-code: sin hallazgos nuevos. Bandit: cero hallazgos. Mypy: cero errores nuevos; doce diagnósticos heredados de alias/enums Qt en `app_icons.py`, comprobados también en ese módulo sin modificar. `git diff --check`: PASS. Evidencia: logs `output/workspace-accents-*` y `workspace-accents-quality.json`. La comparación del código compilado conserva todos los otros métodos del archivo principal respecto al ejecutable previo a este ajuste de color.

## BUILD

`python -X utf8 -m PyInstaller --noconfirm --distpath output/workspace-accents-build --workpath output/workspace-accents-build-cache build_app.spec`: PASS. Correspondencia de once módulos y dos recursos PDF con las fuentes: PASS (`output/workspace-accents-build-verification.json`). Este build local todavía identificaba la versión 1.2.8; la publicación requiere el build 1.2.9 y su QA de paquete independiente.

## QA

Cuatro smoke tests del build local: lanzador, PDF, visor PDF y reportes, todos con salida 0. Capturas Qt reales con datos ficticios, temas oscuro/claro y tamaños 1366×768, 1680×950 y 1920×1080 en `output/workspace-accents-visual-qa/`. Revisión visual de colores, legibilidad, disposición y estados. Las tablas conservan scroll horizontal cuando el ancho es reducido. No se comprobó una impresora física ni la instalación en el hospital.

## QUALITY GATES

Funcionalidad, unitarias, regresión relacionada, integración visual/eventos, coverage, límites, formatter, lint, análisis estático, tipos (cero errores nuevos), complejidad, duplicación, build local, smoke y seguridad: PASS en este alcance. Cuatro pasadas: funcionalidad, regresión, clean code y QA: PASS. Suite global y publicación: comprobación independiente en `RELEASE_1.2.9_QA.md`.

## PROBLEMAS PENDIENTES

Diagnósticos y complejidad heredados documentados; comprobación física en hospital: NO VERIFICADO. Sin errores nuevos detectados en este ajuste.

ESTADO FINAL: APROBADO PARA ENTREGA (ajuste visual local).
