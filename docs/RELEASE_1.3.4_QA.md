# SIGEH 1.3.4 — Validación

Fecha: 2026-10-27. Evidencia resumida y hashes: [RELEASE_1.3.4_EVIDENCE.json](RELEASE_1.3.4_EVIDENCE.json).

## IMPLEMENTACIÓN
- `CALCULOS_QT.py`, `sigeh_visual_theme.py`, `app_icons.py`: un solo cambio del tema en la ventana, colores explícitos del desplegable y actualización agrupada de iconos.
- `ADMISION_PYSIDE6_V15/qt_compat.py`, `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: agrupan la aplicación de estilos de Admisión; no cambian sus formularios ni impresión.
- `diagnosis_recommendations.py`, `diagnosis_recommendation_admin.py`, `diagnosis_recommendation_store.py`, `diagnosis_recommendation_schema.py`: correcciones y retiros persistentes, equivalencias de nombres editados, configuración por identificador ARS y validación del catálogo.
- `recommendation_config_events.py`, `diagnosis_recommendation_ui.py`: señal PostgreSQL después del commit; recuperación tras reconectar y sincronización por cambios conservadas. Una conexión de sesión por aplicación escucha exclusivamente cambios de configuración. La señal contiene la versión, sin datos clínicos.
- `user_permissions.py`, `local_receipts.py` y controles de Facturación: permiso individual de preliminares sin autorización. Las excepciones prevalecen sobre el rol; controles en interfaz, trabajador local y persistencia central. Los valores predeterminados anteriores y la excepción de cobros directos se conservan. Permitir este permiso no completa el recibo.
- `build_app.spec`, `sigeh_product.py`, `version_config.json`: módulo incluido y versión 1.3.4.
- Pruebas nuevas en `tests/test_theme_and_diagnosis_oct27.py`, `tests/test_theme_batch_controls.py`, `tests/test_diagnosis_admin_persistence_oct27.py`, `tests/test_diagnosis_ars_controls.py`, `tests/test_recommendation_config_events.py`, `tests/test_preliminary_authorization_permission.py`. Pruebas relacionadas actualizadas para esperar la población asíncrona de Qt y aislar contadores/servidores desechables.

Se preservan recibos, identificadores, conceptos, importes, usuarios, logo y configuración. Los retiros y las asociaciones por ARS se guardan separadamente para que un cliente anterior no los borre al guardar el formato antiguo. Las asociaciones anteriores se conservan; los estudios nuevos requieren seleccionar una ARS.

## PRUEBAS
- Reproducción: 4 fallos originales en tema/retirada de diagnósticos y 2 en denegación del permiso antes de las correcciones (`output/oct27-red.xml`, `output/preliminary-permission-red.xml`).
- Suite de módulos e integración: **638 PASS**, 0 FAIL, 0 skipped (`output/oct27-verified-tests.xml`).
- Regresión de impresión, PDF, SQLite, arquitectura y actualización: **194 PASS**, 0 FAIL, 0 skipped (`output/oct27-regression-tests.xml`).
- Total final: **832 PASS**, 0 FAIL, 0 skipped. Las ejecuciones interrumpidas por problemas del entorno no se cuentan como aprobadas.
- PostgreSQL: dos clientes, cambios tras commit, rollback sin notificación, configuración de un cliente anterior, edición/retirada y reapertura, selección por ARS y rechazo de catálogos ajenos/inactivos, conservación del recibo original y estado preliminar.
- Bordes: ARS 0/-1/bool/ausente/fuera de rango, datos inválidos, permisos explícitos, cobros directos, ceros iniciales, reconexión, backoff y cancelación; lotes e iconos anidados, excepciones y límite de caché.

## COBERTURA
Herramienta: Coverage.py con ramas. Código nuevo/modificado: **337/338 líneas (99,70%)**, **107/108 ramas (99,07%)**. Todos los archivos modificados cumplen 90%/85%; la lógica crítica cumple 95%/90%. No se atribuye esta cobertura al código heredado completo. Evidencia: `output/oct27-coverage-summary.json`, `output/.coverage-oct27-verified`.

## CALIDAD
- Formatter: PASS en código nuevo y rangos modificados.
- Ruff: PASS diferencial, 0 errores nuevos.
- Mypy: PASS diferencial, 0 errores nuevos. Se conservan errores previos del código heredado; no se declara limpio todo el repositorio.
- Bandit: PASS diferencial, 0 hallazgos nuevos.
- Radon: máximo **10** en funciones nuevas; no aumenta la complejidad heredada modificada.
- Pylint duplicate-code: **0 bloques nuevos**.
- `git diff --check`: PASS.
- Evidencia: `output/oct27-quality-quality.json` y registros con prefijo `output/oct27-quality-`.

## BUILD
Ambos comandos PASS con Python 3.14:
```
python -m PyInstaller --noconfirm --distpath output/recommendations-app --workpath output/diagnosis-v131-collect output/diagnosis-v131-collect.spec
python -m PyInstaller --noconfirm --distpath output/release-134-updater --workpath output/release-134-updater-build build_updater.spec
```
El primer comando recompila las fuentes sobre el grafo de dependencias verificado e incluye explícitamente el módulo nuevo. El spec principal también lo declara. Registros: `output/release-134-build.log`, `output/release-134-updater-build.log`.

## QA
- Transiciones medidas con Coverage: 2,027 / 2,099 / 1,979 / **2,294 s**. Revisión visual independiente: 1,874 / 1,939 s. Límite de 3 s: PASS en este equipo.
- Se inspeccionaron imágenes reales de Qt de ambos desplegables y del editor ARS (`output/oct27-diagnosis-popup-dark.png`, `output/oct27-diagnosis-popup-light.png`, `output/oct27-ars-recommendations.png`). Texto y selección legibles.
- Paquete: 1823 archivos, CRC/tamaños/SHA y **20 módulos compilados** comparados con las fuentes. Motor Sumatra idéntico al incluido en 1.3.3.
- Smoke: 6 PASS (lanzador, actualizador, V15, PDF, visor e informes).
- Actualización del código empaquetado de 1.3.3 a 1.3.4 en perfil sintético: 8 archivos conservan sus hashes (incluidos logo y ajustes); dos bases SQLite mantienen sus dos filas. Health check del ejecutable real PASS; arranque interactivo interceptado para evitar acceso a producción.
- Seguridad: parámetros SQL, validación de permisos contra el usuario central, esquema privado con RLS y sin permisos PUBLIC; notificaciones sin datos clínicos. QA de base de datos exclusivamente en PostgreSQL local desechable.
- SHA256 del ZIP: `45a18c3075203db1877f67804fbd5e8d128f3b57752777881428b8af532bbc52`.
- Evidencia: `output/release-134-package-validation.json`, `output/oct27-ui-qa.json`.

## QUALITY GATES
| Gate aplicable | Estado |
|---|---|
| Funcionalidad | PASS |
| Unitarias, integración y regresión | PASS |
| Bordes y cobertura | PASS |
| Formatter, lint, tipos y análisis estático diferenciales | PASS |
| Complejidad y duplicación nuevas | PASS |
| Build y smoke | PASS |
| Revisión de seguridad | PASS |
| Cuatro pasadas finales: función, regresión, código y QA | PASS |

## PROBLEMAS PENDIENTES
La impresión física en la Canon de la reparación 1.3.3 sigue **NO VERIFICADA** en este equipo, que solo dispone de impresoras virtuales. No se enviaron trabajos a impresoras. La propagación se probó entre dos clientes locales; confirmar la operación en los equipos del hospital después de actualizar. Las mediciones de tema corresponden al equipo de QA.

Los problemas del entorno de pruebas (espacio y registros de subprocesos) se aislaron antes de las ejecuciones finales. No se modificó una base de producción ni se cuentan resultados de esas ejecuciones interrumpidas.

Referencias técnicas: [NOTIFY y transacciones](https://www.postgresql.org/docs/current/sql-notify.html), [conexiones de sesión de Supabase](https://supabase.com/docs/guides/database/connecting-to-postgres).

**ESTADO FINAL: APROBADO PARA ENTREGA** — cambios de software verificados; comprobaciones operativas pendientes indicadas arriba.
