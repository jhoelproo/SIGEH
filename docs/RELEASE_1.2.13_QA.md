# Publicación de SIGEH 1.2.13 — 20/10/2026

ESTADO FINAL: APROBADO PARA ENTREGA del paquete publicado en el canal estable.

Publicación verificada: 2026-10-21T00:22:49Z (20:22:49, America/La_Paz).
[Versión estable](https://github.com/jhoelproo/SIGEH/releases/tag/v1.2.13). Fuente/tag: `90cd76fc26b38aed453f77682a7da35ba17b46be`.

## IMPLEMENTACIÓN

- Publicación de las mejoras terminadas y validadas: permisos individuales, correcciones administrativas y de ARS, sincronización de NSS/nombres, autorizaciones numéricas y confirmación de envío tras editar el Excel exportado.
- Archivos funcionales y comportamiento preservado: [QA de permisos y ARS](QA_PERMISOS_ARS_20261015.md) y [QA de confirmación del Excel](QA_CONFIRMACION_EXCEL_20261015.md). El commit de versión conserva los 48 archivos acumulados, incluidas sus pruebas; no incorpora cambios funcionales nuevos a ese trabajo validado.
- Metadatos actualizados: `sigeh_product.py`, `version_config.json` y las dos expectativas de versión de `tests/test_sigeh_update.py`. Notas: `docs/RELEASE_1.2.13.md`.
- Se utiliza el repositorio y actualizador existentes `jhoelproo/SIGEH`. No se sobrescribió la rama principal remota: el tag apunta al commit de la rama `codex/release-1.2.13` que contiene exactamente las fuentes del paquete.
- Ampliación aditiva central `user_permission_overrides`: columna JSONB NOT NULL, predeterminado `{}`; registrada como `20261021001417`. Sin borrado de cuentas ni actualización de contraseñas; 12 usuarios antes y después, RLS permanece activado. No se hicieron pruebas de escritura de datos clínicos contra producción.

## PRUEBAS

- Se verificó la huella original de código funcional `5766dc8ba5451e2b3b1477c9d102468f1220f83d203b0c10d39327368dbf956b` y las huellas de los archivos de pruebas de cada uno de los 49 grupos de regresión antes de modificar los metadatos de versión.
- Suite funcional previa: 2.950 PASS, cero fallos/errores, una prueba opcional omitida y 74 subpruebas PASS, en 230 módulos. Es evidencia previa validada contra las mismas fuentes funcionales; no se presenta como una nueva ejecución completa durante la publicación.
- Ejecución adicional de 86 pruebas de canal, versiones, actualizador, lanzador, configuración y empaquetado: PASS. La primera ejecución encontró una contaminación del entorno de QA por DATABASE_URL; al retirar esa variable del proceso de pruebas, los 86 casos pasaron sin modificar código o debilitar aserciones.
- Paquete extraído: seis smoke PASS (lanzador, ayuda del actualizador, integridad V15, PDF, visor e informes).
- Dos ciclos de actualización PASS, usando el código extraído del actualizador instalado: 1.2.12 → 1.2.13 y reaplicación 1.2.13 → 1.2.13. Se ejecuta el health check empaquetado real. Se sustituye únicamente el callback final de apertura del login para evitar contacto con producción.
- Se conservaron byte a byte una configuración de conexión protegida, SQLite con dos pacientes ficticios y archivos sintéticos de configuración/nombre/horario/usuarios/logo en la carpeta de datos preservada. Esto valida esas rutas y ese ciclo; no constituye una prueba de todas las configuraciones posibles de cada estación ni de una versión futura.
- CRC y hashes de los 1822 archivos del ZIP: PASS. Ejecutables comparados con las fuentes actuales; metadatos internos y externos coincidentes. El paquete público no contiene credenciales ni bases operacionales.
- Cuatro assets remotos con tamaño y SHA-256 coincidentes; tag/commit, manifest/checksum y detección desde 1.2.12 PASS. La misma versión no solicita reinstalación.

## RESULTADOS

| Comprobación | PASS | FAIL / ERROR | SKIP |
| --- | ---: | ---: | ---: |
| Suite funcional previa, fuentes comprobadas | 2.950 + 74 subpruebas | 0 | 1 |
| Pruebas adicionales de publicación | 86 | 0 | 0 |
| Smoke del paquete extraído | 6 | 0 | 0 |
| Ciclos del actualizador | 2 | 0 | 0 |
| Assets remotos verificados | 4 | 0 | 0 |

Los conjuntos se solapan; no se suman como casos únicos. Omisión: ensayo optativo de capacidad contra PostgreSQL real, ajeno a esta publicación.

## COBERTURA

Medición funcional previa mediante coverage.py: permisos/ARS 368/371 líneas (99,19 %) y 103/104 ramas (99,04 %); cinco módulos nuevos 100 %/100 %. Confirmación de Excel: 12/12 líneas y 4/4 ramas, 100 %/100 %. Son alcances del cambio, no cobertura de toda la aplicación heredada. Cobertura nueva de publicación: N/A — solo constantes y expectativas de versión; no se cambió lógica funcional.

## CALIDAD

- Formatter, lint, tipos y análisis estático diferencial: PASS, cero errores nuevos para los metadatos de versión. `git diff --check`: PASS.
- Complejidad y duplicación de publicación: N/A, no hay nueva lógica ni funciones. Se conserva la evidencia funcional previa: complejidad nueva máxima 9 en permisos/ARS y 7 en confirmación; cero bloques duplicados nuevos. Las excepciones de complejidad y avisos heredados siguen documentados en el QA funcional.
- Seguridad del paquete: PASS (sin credenciales/bases operacionales); análisis de Supabase sin avisos nuevos tras la ampliación. Permanecen 65 INFO de RLS sin políticas, 3 WARN de search_path y 1 WARN de extensión en public. No se cambiaron grants ni políticas. [RLS](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy), [search_path](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable), [extensión](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public).

## BUILD

PASS de ambos comandos:

```text
python -X utf8 -m PyInstaller --noconfirm --distpath output/release-1213-app --workpath output/release-1213-build build_app.spec
python -X utf8 -m PyInstaller --noconfirm --distpath output/release-1213-updater --workpath output/release-1213-updater-build build_updater.spec
```

Empaquetado mediante `release_packaging.py`, canal público sin backend bundle. SHA-256 del ZIP publicado: `e638e3cfa5a02cbc21a221f9eb7764797b80680a69616f449aed133a8683122a`. No se alteró el paquete después de sus comprobaciones.

## QA

Cuatro pasadas: funcionalidad PASS; regresión/actualización PASS; clean code/fuentes PASS; QA del paquete, esquema central y canal estable PASS. La GUI funcional fue validada en las mismas fuentes antes de publicar; se ejecutaron los smoke empaquetados adicionales arriba descritos.

## QUALITY GATES

| Gate aplicable | Estado |
| --- | --- |
| Funcionalidad, unitarias, integración, regresión y límites del cambio | PASS — evidencia funcional previa conservada y metadatos verificados |
| Pruebas adicionales de publicación | PASS |
| Cobertura funcional y calidad del cambio | PASS — mismo alcance validado |
| Formatter/lint/tipos/estático de metadatos | PASS — sin errores nuevos |
| Build, concordancia de fuentes y ejecutables | PASS |
| Integridad y ausencia de credenciales/bases en ZIP | PASS |
| Smoke y conservación en actualización | PASS |
| Ampliación central no destructiva y RLS | PASS |
| Tag, cuatro assets, hashes y canal latest | PASS |
| Cuatro pasadas de QA final | PASS |
| Nueva lógica/cobertura/complejidad de publicación | N/A — constantes de versión |
| Capacidad real optativa | N/A — no requerida |

## PROBLEMAS PENDIENTES

- La publicación está completada. Instalación efectiva en cada computadora del hospital: NO VERIFICADO desde este entorno; deben aceptar/aplicar la actualización ofrecida por SIGEH.
- Las mejoras adicionales del actualizador y del tratamiento de contraseñas solicitadas el 19 de octubre siguen pendientes; no se incluyeron cambios inconclusos en producción.
- Los cambios externos del Excel no se importan automáticamente a los datos internos; la confirmación lo indica.
- Se conservan las limitaciones y avisos heredados documentados. No se afirma que toda la aplicación o la base esté libre de riesgos.

Evidencias: `output/release-1213-functional-provenance.json`, `release-1213-targeted.xml`, `release-1213-quality.json`, `release-1213-package-validation.json`, `release-1213-schema-evidence.json`, `release-1213-draft-verification.json` y `release-1213-publication-verification.json`.
