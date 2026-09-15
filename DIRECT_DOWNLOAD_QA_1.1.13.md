# QA SIGEH 1.1.13 — descarga directa

## Implementación

- `database_config.py`: reconoce `database_url.bundle` en `_internal` junto a la aplicación y recupera una configuración válida desde instalaciones SIGEH/HOSPITAL conocidas en Escritorio local o OneDrive.
- `portable_launcher.py`: usa la recuperación únicamente cuando la configuración normal no existe, la entrega al proceso principal y registra `CONFIG_RECOVERY` sin exponer la URL.
- `sigeh_product.py` y `version_config.json`: versión 1.1.13.
- La 1.1.13 conserva las correcciones de recibos de la 1.1.12. Extranjeros y la corrección individual de fecha permanecen fuera del alcance.

## Pruebas

- Reproducción previa: ZIP 1.1.12 limpio sin configuración produjo `CONFIGURATION_MISSING`: PASS como reproducción del defecto.
- Pruebas específicas finales de configuración, lanzador, actualizador y empaquetado: 38 PASS, 0 FAIL.
- Prueba adicional de OneDrive posterior: incluida en las 17 pruebas de configuración/lanzador, 17 PASS.
- Suite completa: 1.542 PASS, 0 FAIL, 1 SKIPPED y 60 subtests PASS. La única omisión es la prueba optativa de capacidad contra una base PostgreSQL real, ajena al cambio. El clúster PostgreSQL de QA fue local, temporal y limitado a loopback.
- Casos cubiertos: configuración en `_internal`, instalación previa en Escritorio, instalación HOSPITAL en OneDrive, directorio ajeno ignorado, ausencia total segura y recuperación desde el ejecutable empaquetado.

## Cobertura

- Diff modificado medido: `database_config.py` 31/31 sentencias y 18/18 ramas; `portable_launcher.py` 11/11 sentencias y 6/6 ramas. Líneas y ramas: 100%.
- Evidencia: `output/release-1113-coverage.json` y `output/release-1113-diff-coverage.json`.

## Calidad

- Ruff lint: PASS.
- Ruff format: PASS.
- Mypy: PASS, 0 errores en los dos módulos modificados.
- Complejidad máxima: 10.
- Duplicación total de los dos módulos: 1,12%, por dos funciones pequeñas preexistentes que resuelven la carpeta del ejecutable; duplicación nueva: 0%.
- `compileall` y `git diff --check`: PASS.
- Seguridad: la búsqueda está acotada a nombres de instalación conocidos; no recorre documentos personales, no registra secretos y el ZIP público continúa rechazando credenciales o bases operativas.

## Build y QA

- Aplicación: PyInstaller PASS.
- Actualizador: PyInstaller PASS.
- ZIP extraído: CRC y hashes de 1.822 archivos PASS.
- Smoke real del ZIP: recuperación sin variable de entorno desde una instalación previa sintética, `SIGEH.exe --self-test`, PDF y reportes: PASS.
- SHA-256: `393ad317a1d46b7a5bdd59106d8912c31763eb972c9583c9eb6066b2cf49dcbb`.
- Impresión física: N/A; el cambio afecta el inicio, no la impresión.

## Quality gates

| Gate | Estado |
|---|---|
| Funcionalidad y regresión | PASS |
| Unitarias, integración y límites | PASS |
| Cobertura modificada | PASS |
| Formatter, lint, tipos y análisis estático | PASS |
| Complejidad | PASS |
| Build y smoke del ZIP | PASS |
| Seguridad del paquete público | PASS |
| QA final | PASS |

## Problemas pendientes

Una computadora sin ninguna instalación SIGEH previa ni configuración administrada seguirá bloqueada de forma segura. No es el escenario de actualización reportado y evita publicar credenciales del hospital en un repositorio público.

ESTADO FINAL: APROBADO PARA ENTREGA
