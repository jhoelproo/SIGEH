# SIGEH 1.2.10 — validación de entrega

## IMPLEMENTACIÓN

Versión preparada el 6 de octubre de 2026. Código funcional: commit `2da94cb`, descrito en [QA_LIST_CORRECTIONS_2026-10-05.md](QA_LIST_CORRECTIONS_2026-10-05.md). Se incluyen búsqueda por recibo en candidatos ARS, nombre sincronizado entre recibo/listado y Admisión vinculada, especialidades persistentes con sugerencias y recuperación del foco del buscador.

El incremento de versión modifica `sigeh_product.py`, `version_config.json` y las expectativas de `tests/test_sigeh_update.py`; se agregan estas notas y `RELEASE_1.2.10.md`. El QA detectó un log generado por el self-test en la carpeta de distribución: `release_packaging.py` ahora rechaza también `lanzador_log.txt`, con tres regresiones en `tests/test_release_packaging_fresh_install.py`. Esta herramienta no forma parte de los ejecutables. No cambia la lógica funcional validada. Se conservan precios, importes, autorizaciones, permisos, identificadores, estados y copias históricas de expedientes emitidos.

## PRUEBAS

La expectativa 1.2.10 falló con la constante anterior 1.2.9: RED real en `output/release-1210-version-red.xml`. Las tres variantes del rechazo del log fallaron antes de la corrección: `output/release-1210-log-guard-red.xml`. Después se ejecutaron actualización, instalación portable, conservación de conexión, empaquetado, controles de actualización y producto: 105 pruebas aprobadas, cero fallos, errores u omisiones; `output/release-1210-tests.xml` y `.log`.

Regresión funcional completa previa al incremento: 2.357 pruebas y 74 subpruebas aprobadas, cero fallos/errores y una prueba optativa de capacidad omitida. No se suman ambos resultados porque se solapan. El código funcional permanece idéntico al commit comprobado; cambian metadatos, dos expectativas de versión y la política de rechazo de logs de la herramienta de empaquetado, cuya suite se repite completa. Evidencia de procedencia: `output/release-1210-validation-summary.json` y `output/list-corrections-results.json`.

Integración funcional validada con PostgreSQL desechable local: sincronización, identidad explícita, bloqueo, conflicto, transacciones y rollback. Límites probados: búsquedas vacías/parciales/exactas, números con ceros iniciales, campos incompletos, especialidades con/sin acento y personalizadas, nombres inválidos, vínculos inexistentes/cambiados y foco con filtros/modal. Detalle de resultados en el informe funcional citado.

## COBERTURA

Coverage.py real con ramas: cambio funcional 314/314 líneas (100 %) y 101/102 ramas (99,02 %). Los cuatro módulos nuevos alcanzan 100 % de líneas y ramas, incluida la lógica crítica de corrección de paciente. No se atribuyen estos porcentajes al monolito completo.

La constante de versión y la declaración de nombres prohibidos del empaquetador se ejecutaron en la medición adicional de publicación; no agregan ramas. Las tres regresiones comprueban rechazo en raíz, subcarpeta y con mayúsculas. Evidencia: `output/list-corrections-coverage-summary.json`, `output/release-1210-version-coverage.json` y `output/release-1210-validation-summary.json`.

## CALIDAD

Ruff lint/formatter y mypy del alcance de versión: PASS. `git diff --check`: PASS. El código funcional conserva las comprobaciones de Ruff, mypy (también `--check-untyped-defs`), Bandit, Radon y Pylint: cero errores nuevos, cero hallazgos de Bandit y cero bloques duplicados detectados con mínimo de seis líneas en los seis módulos revisados. No se afirma un porcentaje de duplicación global.

Complejidad máxima de módulos auxiliares: 10. Excepción heredada documentada: la orquestación transaccional `save_receipt_with_items` conserva complejidad 190; los auxiliares nuevos de identidad/corrección no superan 8. No se debilitaron configuraciones ni se agregó una exclusión para ocultar diagnósticos.

Radon confirma cero incremento de complejidad en el cambio de versión/empaquetado; máximos heredados 12 en `sigeh_product.py` y 15 en `release_packaging.py`, sin funciones nuevas. Pylint no detecta bloques duplicados de seis líneas entre ambos módulos. Bandit: cero hallazgos nuevos respecto de `2da94cb`; conserva B608 en un DELETE cuyo nombre de tabla procede exclusivamente de una tupla fija de dos tablas, revisada sin entrada controlada por el usuario. No se suprime ese diagnóstico. Evidencia: `output/release-1210-tooling-metrics.json` y reports de seguridad actual/base.

## BUILD

Comandos de publicación:

```powershell
python -X utf8 -m PyInstaller --noconfirm --log-level WARN --distpath output/release-1210-app --workpath build/release-1210-app build_app.spec
python -X utf8 -m PyInstaller --noconfirm --log-level WARN --distpath output/release-1210-updater --workpath build/release-1210-updater build_updater.spec
python -X utf8 release_packaging.py --dist output/release-1210-app/SIGEH --updater output/release-1210-updater/SIGEH_Updater.exe --output output/release-1.2.10 --version 1.2.10
```

Los tres comandos terminaron con salida 0: PASS. Veintiocho módulos funcionales, el código principal, las dependencias del lanzador y el actualizador coinciden con sus fuentes compiladas; la versión compilada coincide en los tres ejecutables. Plantilla/CSS idénticos byte por byte y metadatos JSON de raíz/interno iguales a SIGEH 1.2.10. Evidencia: `output/release-1210-package-verification.json`, `release-1210-extended-code-verification.json` y `release-1210-resources-verification.json`.

Las advertencias heredadas sobre imports opcionales `mypyc`/`mx.DateTime` no impidieron el build ni los smoke tests. La protección de logs pertenece únicamente a la herramienta de construcción y no exige recompilar ejecutables: se comprobó que ese módulo no está incluido en sus archivos PYZ.

## QA

QA funcional previo: 21 pruebas de interfaz nativa Windows aprobadas. Editor y búsqueda revisados en temas claro/oscuro e historial a 1366/1680/1920 píxeles. Los controles mantienen señales, estados y persistencia. No cambian en el incremento de versión.

- ZIP íntegro; 1.822 archivos comprobados por tamaño y SHA-256. Sin credenciales, bases operativas, cachés Python ni logs. SHA-256: `35120c176e5f7505deb9756c78aabb6d6468fcbc7ea27ec2562b928989d8cec6`.
- Cuatro smoke del build y seis del ZIP extraído: lanzador, ayuda del actualizador, paquete Admisión v15, PDF, visor y reportes; todos con salida 0.
- Actualización 1.2.9→1.2.10 usando código del actualizador extraído del ejecutable instalado. Segundo ciclo usando el actualizador empaquetado de 1.2.10 y reaplicando el mismo paquete. Conexión cifrada e historial sintético de dos pacientes conservados byte por byte en ambos ciclos, con health checks reales del ejecutable. Solo se sustituye el callback final de reapertura para evitar login/acceso a producción. No equivale a compilar una versión futura.

Evidencia: `output/release-1210-package-validation.json` y `output/release-1210-smoke.json`. Las pruebas usan datos sintéticos y configuración aislada; no se ejecutan operaciones destructivas contra producción. Un primer intento corrigió el import del runner local; otro detectó el log que fue retirado y cuya exclusión quedó probada. Se conservan sus logs de diagnóstico; la evidencia final corresponde a la repetición limpia.

### Publicación verificada

Publicada como estable y Latest el 6 de octubre de 2026 a las 04:51:16 UTC (00:51:16, Bolivia): https://github.com/jhoelproo/SIGEH/releases/tag/v1.2.10. Tag y commit fuente verificados: `a5830c67c6e967b8e5c7e9904b93f33031fd345f`. Los cuatro assets remotos coinciden por tamaño y SHA-256 con los archivos aprobados localmente, comprobados primero como borrador y de nuevo después de publicar.

El canal público usado por el lanzador devuelve 1.2.10; manifest y checksum remotos coinciden. La actualización desde 1.2.9 se reconoce y la misma versión 1.2.10 no provoca reinstalación. Evidencia: `output/release-1210-draft-verification.json` y `output/release-1210-publication-verification.json`.

Se congelaron y comprobaron 378 inputs rastreados de build antes de subir y publicar: `output/release-1210-final-fingerprint.json`. Este informe incorpora la comprobación remota posterior al commit fuente, sin modificar los ejecutables ni el ZIP.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad, unitarias, integración y límites | PASS |
| Regresión funcional completa y suite de publicación | PASS |
| Cobertura | PASS: 100 % líneas / 99,02 % ramas del cambio |
| Formatter, lint, tipos y análisis estático | PASS en el alcance y cero errores nuevos |
| Complejidad | PASS con excepción heredada documentada |
| Duplicación | PASS en módulos revisados, cero bloques detectados |
| Seguridad funcional y QA de interfaz | PASS |
| Build, smoke, paquete y actualización | PASS |
| Canal público, tag y assets remotos | PASS |
| QA final | PASS |
| Benchmark externo de capacidad | N/A: optativo y ajeno a este cambio |
| Instalación efectiva en hospital e impresión física | NO VERIFICADO: sin acceso a esas estaciones/impresora |

## PROBLEMAS PENDIENTES

No quedan pasos de publicación ni defectos nuevos detectados en este alcance. La deuda heredada del monolito se conserva y se identifica expresamente. La instalación efectiva en las estaciones del hospital y la impresión física siguen sin verificarse; no son comprobaciones ejecutables desde este entorno ni requisitos de publicación del paquete. Una publicación no confirma que esas estaciones ya estén actualizadas.

Cuatro pasadas finales: funcionalidad PASS, verificando cada requisito y sus vínculos; regresiones PASS, con suite funcional previa inalterada y suite completa de publicación; clean code PASS, con análisis de nombres, duplicación, responsabilidades, errores y excepciones heredadas documentadas; QA PASS, con cobertura real, herramientas, build, interfaz, paquete, actualización y canal público.

ESTADO FINAL: APROBADO PARA ENTREGA.
