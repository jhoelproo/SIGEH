# SIGEH 1.3.0 — QA de publicación

## Implementación

- Recomendaciones de medicamentos, laboratorios e imágenes por diagnóstico y especialidad; configuración de administrador; las sugerencias no se añaden automáticamente.
- Selector de cantidad en el catálogo, en lugar de ordenar por nombre/precio.
- Corrección de importación Word y migración aditiva del esquema privado de recomendaciones.
- La actualización conserva los datos locales; no modifica contabilidad ni recibos existentes.
- El documento Word real del hospital se probó localmente y se excluyó del repositorio. Las regresiones usan un fixture sintético.

## Pruebas y resultados

- Recomendaciones, catálogo, actualizador e importación Word: **98 PASS** en la ejecución focalizada inicial; **27 PASS** para Word después de reemplazar el documento real por datos sintéticos; integración PostgreSQL de importación: **2 PASS**; catálogo después del formateo: **5 PASS**.
- Suite de regresión completa de las recomendaciones: **3,464 PASS, 0 FAIL, 1 SKIP opcional**; el informe de detalle identifica dos reintentos aislados por condiciones transitorias de Windows/PostgreSQL.
- Verificación de instalación real 1.2.14 → 1.3.0: **PASS**. Se conservaron y compararon ocho archivos sintéticos de configuración, logo, usuarios, horarios, conexión e historial; health check del paquete actualizado: PASS.
- ZIP exacto: **1,823 archivos** comprobados por tamaño/SHA-256; CRC correcto. Código compilado comparado con fuentes en 26 módulos: **PASS**.
- Smoke del ZIP exacto: lanzador, ayuda del actualizador, paquete de Admisión V15, PDF, visor e informes: **6 PASS**.
- Pytest: 98 pruebas focalizadas PASS; Word actualizado: 29 PASS; catálogo reformateado: 5 PASS. Los últimos dos grupos se repitieron sobre el estado final.

## Cobertura y calidad

- Cobertura diferencial del código nuevo/modificado: **99.57% líneas, 98.02% ramas**, registrada en `QA_RECOMENDACIONES_DIAGNOSTICO_20261024.md`.
- Ruff check y formatter en los módulos de alcance y pruebas: **PASS**. Revisión diferencial previa: sin errores nuevos.
- Ruff de todo el repositorio: **FAIL**, 917 diagnósticos en módulos dispersos; no se corrigieron en esta versión por estar fuera del alcance. El análisis diferencial del cambio y el chequeo directo de los archivos en alcance pasan.
- Complejidad y duplicación diferencial: **PASS** según el informe de recomendaciones. Mypy, Bandit y análisis diferencial: **PASS** según ese informe.
- `git diff --check`: **PASS**.

## Build y QA

- `python -m PyInstaller --noconfirm --distpath output/release-130-app --workpath output/release-130-build build_app.spec`: **PASS**.
- `python -m PyInstaller --noconfirm --distpath output/release-130-updater --workpath output/release-130-updater-build build_updater.spec`: **PASS**.
- Paquete 1.3.0, manifiestos, checksum, seis smoke checks y actualización desde la versión estable anterior: **PASS**.
- Integración con base PostgreSQL desechable: PASS. El esquema se aplica de forma aditiva al conectar. No se ejecutó SQL manual ni QA destructivo contra producción.
- Verificación en estaciones físicas e impresora hospitalaria: **NO VERIFICADO**.

## Publicación

SHA-256 del ZIP: `cb47129c2c3ea9e8b096edb3eb25d84ace323fc79b251d2a31c706c5534a4955`.

## Quality gates

| Gate | Resultado |
|---|---|
| Funcionalidad, regresión, integración y límites | PASS |
| Cobertura diferencial | PASS |
| Formatter, lint y tipos del alcance | PASS |
| Ruff global | FAIL — diagnósticos generales fuera del alcance |
| Complejidad, duplicación y seguridad focalizada | PASS |
| Build, smoke y actualización real de paquete | PASS |
| QA en estaciones físicas/impresora | NO VERIFICADO |

## Problemas pendientes

- Verificación con impresora y estaciones físicas del hospital.
- Resolver por separado los 917 diagnósticos de Ruff del repositorio; no se incluyen en este cambio.
