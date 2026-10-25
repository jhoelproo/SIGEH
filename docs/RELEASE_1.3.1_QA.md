# SIGEH 1.3.1 — QA de publicación

## IMPLEMENTACIÓN

Las correcciones de texto, selección y búsqueda de diagnósticos están descritas, con sus archivos y pruebas, en `QA_NORMALIZACION_DIAGNOSTICOS_20261024.md`.

Esta entrega actualiza `sigeh_product.py`, `version_config.json` y `tests/test_sigeh_update.py` a 1.3.1. Las notas están en `RELEASE_1.3.1.md`. Se conservan recibos, conceptos, importes, permisos y decisiones manuales de Facturación.

## PRUEBAS Y RESULTADOS

- Validación funcional previa del cambio: 393 PASS, 0 FAIL, 0 SKIP en las suites relacionadas documentadas. Se comprobaron las huellas de las seis fuentes: permanecen idénticas a las usadas para la cobertura.
- Regresión adicional de publicación: 91 PASS, 0 FAIL, 0 SKIP. Incluye versión, canal, actualizador, rollback, conservación de configuración, controles Qt, instalación, compatibilidad de contraseñas y migración de materiales. Los recuentos de ambas ejecuciones se informan por separado; contienen pruebas compartidas.
- La prueba de versión falló primero esperando 1.3.1 frente al código 1.3.0; pasó después de actualizar las dos fuentes de versión. También verifica que 1.3.0 sea anterior y 1.3.2 posterior.
- ZIP final: 1,823 archivos comprobados por tamaño, SHA-256 y CRC. No contiene credenciales, bases operacionales ni archivos temporales.
- Se conservaron todas las rutas del ZIP 1.3.0, incluido el SQL de migración anterior. La comparación inicial detectó esa omisión de recursos del grafo de build almacenado; se incorporó antes de producir y comprobar el paquete final.
- Código empaquetado de 29 entradas comparado con las fuentes finales: PASS.
- Seis smoke checks sobre el ZIP final: lanzador, actualizador, paquete V15, PDF, visor e informes: PASS.
- Actualización real de un paquete 1.3.0 al ZIP 1.3.1 en un perfil desechable: PASS. Se verificaron ocho archivos preservados por hash, configuración de hospital, horarios, logo, usuarios, conexión protegida e historial sintético; ambas bases conservaron sus dos registros. Health check real del paquete: PASS. Solo se sustituyó el reinicio del lanzador por un callback para evitar abrir una sesión real.

El primer intento del arnés de comprobación usó un atributo inexistente del lector de archivos de PyInstaller. Se corrigió el arnés y se repitió la validación; no cambió la aplicación por ese error.

La suite completa del repositorio no se repitió en esta publicación; se ejecutaron las suites aplicables y regresiones indicadas.

## COBERTURA

Coverage.py sobre las funciones de diagnóstico nuevas o modificadas: 99.05% líneas (626/632), 97.13% ramas (169/174). Módulo nuevo y persistencia modificada: 100% en ambas métricas. El cambio adicional de publicación es una constante de versión, sin nuevas ramas de negocio.

## CALIDAD Y SEGURIDAD

- Ruff check y format: PASS en 14 fuentes/pruebas del alcance.
- Mypy: PASS en siete fuentes.
- Bandit: PASS en las seis fuentes de diagnósticos. En `sigeh_product.py` se compararon los resultados antes/después: cero hallazgos nuevos. El aviso B608 existente usa exclusivamente dos nombres literales de tabla, sin identificadores externos.
- Complejidad máxima de las funciones del cambio: 9. Duplicación nueva detectada: 0 bloques, según la medición documentada y las fuentes verificadas sin cambios.
- `git diff --check`: PASS. No se debilitaron reglas ni thresholds.
- QA con perfiles sintéticos y PostgreSQL desechable. No se ejecutó QA destructivo ni SQL manual contra producción.

## BUILD

PASS:

`python -X utf8 -m PyInstaller --noconfirm --distpath output/recommendations-app --workpath output/diagnosis-v131-collect output/diagnosis-v131-collect.spec`

`python -X utf8 -m PyInstaller --noconfirm --distpath output/release-131-updater --workpath output/release-131-updater-build build_updater.spec`

El primer comando recompila código y ejecutables usando el grafo verificado de dependencias 1.3.0; el recurso SQL anterior se incorporó al paquete y a la receta de colección. Se comprobó el contenido compilado y los recursos del ZIP final.

## PUBLICACIÓN

SHA-256 del ZIP final: `3671b533ffc3f548ae5a598f709a5a3b3a732017aaf165964f8518a509d6b683`.

Publicada como versión estable y latest: [SIGEH 1.3.1](https://github.com/jhoelproo/SIGEH/releases/tag/v1.3.1). Fecha de GitHub: `2026-10-25T00:24:32Z` (24/10/2026, 20:24 en America/La_Paz).

Rama: `codex/release-1.3.1`. La etiqueta `v1.3.1` apunta a `df1d45e54414821422a63549888c2a5a0f467e13`.

Comprobación remota: PASS. Los cuatro assets están subidos y sus tamaños/SHA-256 de GitHub coinciden con los locales. Los manifiestos y checksum se descargaron y compararon byte por byte. El mismo cliente de actualizaciones de la aplicación consultó el canal estable, obtuvo 1.3.1 y validó el manifiesto y checksum; la versión se reconoce como posterior a 1.3.0. No se descargó nuevamente el ZIP completo: se contrastó su digest SHA-256 calculado por GitHub con el paquete local ya comprobado.

## QUALITY GATES

| Gate | Resultado |
|---|---|
| Funcionalidad, unitarias, integración, límites y regresiones aplicables | PASS |
| Cobertura del cambio y lógica crítica | PASS |
| Formatter, lint, tipos y análisis estático del alcance | PASS |
| Complejidad, duplicación y seguridad del cambio | PASS |
| Build y correspondencia de código/recursos | PASS |
| Smoke del ZIP y actualización con preservación de datos | PASS |
| Publicación estable y checksum remoto | PASS |
| Estaciones físicas e impresora hospitalaria | N/A — requiere comprobación operativa en el hospital |

## PROBLEMAS PENDIENTES

La versión está disponible para actualizar. La instalación efectiva en cada estación física no se realiza desde este entorno. Se mantienen las limitaciones de vocabulario y los diagnósticos generales de lint heredados fuera del alcance que se documentaron en la entrega local.

## EVIDENCIA

`output/release-131-update-tests.xml`, `release-131-update-tests.log`, `release-131-build.log`, `release-131-updater-build.log`, `release-131-packaging.log`, `release-131-package-validation.json`, `release-131-validation.log`, `release-131-security-before.json`, `release-131-security-after.json`, `release-131-final-fingerprint.json`, `release-131-draft-verification.json`, `release-131-publication-verification.json` y los cuatro artefactos de `output/release-1.3.1/`.

ESTADO FINAL: APROBADO PARA ENTREGA
