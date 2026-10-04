# Listados ARS: recibos manuales por fecha de servicio

## IMPLEMENTACIÓN

- `CALCULOS_QT.py`: `_query_available_receipts_for_batch` permite recibos asegurados PENDIENTE, SIN_CLASIFICAR y FACTURADO que no estén incluidos en otro expediente. Excluye cobros directos SELF_PAY y coberturas distintas de ASEGURADO.
- Se conserva el filtro inclusivo sobre `recibos.fecha`, la fecha de servicio seleccionada. No se filtra por fecha de creación ni por el mes del expediente. No requiere vínculo a Admisión.
- Se conservan el límite de resultados, los permisos de edición, los bloqueos y la revalidación al agregar, los importes, el estado de facturación y las fechas del recibo.
- Pruebas modificadas: `test_integral_emergency_to_monthly_list.py`, `test_monthly_ars_candidate_selection.py`, `test_monthly_ars_form_state.py`, `test_admission_validation_extensions.py`.
- Continúan incluidos los cambios anteriores de administrador/ARS y extranjeros/cobros directos, documentados en `RECEIPT_ARS_CORRECTION_QA.md` y `SELF_PAY_PENDING_QA.md`.

## DIAGNÓSTICO REAL

Consulta central de solo lectura: MONUMENTAL, 01/08/2026–28/08/2026, 11 recibos activos sin expediente: 6 PENDIENTE y 5 FACTURADO. El filtro anterior excluía los cinco FACTURADO. La función corregida, ejecutada con una conexión central READ ONLY, devuelve **11 recibos, RD$ 16.663,36**, con fechas de servicio dentro del rango. No se alteraron datos del hospital ni se crearon recibos para completar la cifra.

Evidencia local agregada sin nombres/identificadores personales: `output/monthly-manual-audit.json`.

## PRUEBAS

- RED real: prueba de once recibos manuales devuelve seis y falla por los cinco omitidos; segunda prueba de exclusiones pasa (`monthly-manual-red.xml`). Los primeros intentos tuvieron errores de datos ficticios duplicados; se corrigió el fixture antes de reproducir la regresión de negocio.
- PostgreSQL local desechable: once manuales guardados en octubre con fecha de servicio de agosto, cinco FACTURADO; inclusión de todos en un expediente de octubre; estado y fecha preservados; rechazo de segundo expediente.
- Límites inclusivos 1 y 28 de agosto; 31 de julio y 29 de agosto excluidos aunque su fecha de creación esté en agosto.
- Exclusiones: otra ARS, borrado, NO_FACTURADO, EXTRANJERO y NO_ASEGURADO/SELF_PAY.
- Qt: clic en Buscar pacientes transmite fechas elegidas, muestra once resultados y habilita Agregar todo (11), con mes del expediente distinto del rango.
- Unitarias: ARS no seleccionable no consulta SQL; resultados de ARS ajena descartados defensivamente.

## RESULTADOS

- Suite relacionada inicial: 34 PASS, 0 FAIL, 0 SKIP (`monthly-manual-tests.xml`).
- GUI y validación final tras formato: 37 PASS y 3 subpruebas PASS, 0 FAIL, 0 SKIP (`monthly-manual-ui-final.xml`).
- Regresión completa: 2.014 casos regulares y 60 subpruebas; **2.073 PASS, 0 FAIL, 0 errores, 1 SKIP**. La omitida es la prueba opcional de capacidad externa. Los 2.014 casos recopilados fueron ejecutados, sin faltantes ni extras (`monthly-manual-full-summary.json`, `monthly-manual-full-suite-completeness.json`). Una prueba de latencia SQLite falló inicialmente (408 ms frente a 200 ms); su módulo completo pasó al repetirlo (16 PASS) y su grupo completo pasó (204 PASS), sin cambiar código ni umbrales. Se conserva el resultado inicial en `monthly-manual-group-9-initial.xml`.

## COBERTURA

coverage.py con ramas: función de consulta modificada **16/16 líneas, 6/6 ramas: 100 %/100 %**. No representa la cobertura de todo el monolito. Las condiciones SQL se comprueban con PostgreSQL real local. Evidencia `monthly-manual-coverage.json`, `monthly-manual-quality.json`.

## CALIDAD

- Ruff: pruebas afectadas PASS; formato aplicado solo a funciones nuevas/modificadas, preservando el resto del archivo.
- Lint y mypy diferencial de producción: 0 diagnósticos nuevos; diagnósticos heredados conservados. Evidencia `monthly-manual-lint.log`, `monthly-manual-types.log`.
- Compileall: PASS. Radon: complejidad de la consulta **4**; no incrementada.
- Pylint duplicate-code, mínimo seis líneas, sobre aplicación y módulos nuevos de ARS/cobros directos: PASS, sin duplicación detectada (`monthly-manual-duplication.log`).
- `git diff --check`: PASS. No se agregan supresiones ni se debilitan configuraciones.

## BUILD

`python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/monthly-manual-app --workpath build/monthly-manual-app build_app.spec`: **PASS**, exit 0 (`monthly-manual-build.log`).

Comparación de código empaquetado con fuentes actuales de aplicación, corrección de ARS, integridad y política: todas coinciden (`monthly-manual-package-verification.json`).

## QA

Ejecutable real: generación de PDF, apertura de visor y generación de reportes: **PASS**, las tres invocaciones exit 0 (`monthly-manual-package-smoke.json`). Controles Qt reales y persistencia PostgreSQL local: PASS. Seguridad: SQL parametrizado, permisos preservados, consulta central READ ONLY; ninguna escritura ni prueba destructiva en producción. No se modifica esquema, credenciales, RLS o permisos de base.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad | PASS |
| Unitarias | PASS |
| Integración SQL y persistencia | PASS |
| Límites y exclusiones | PASS |
| Cobertura del código modificado | PASS |
| Formatter de funciones nuevas/modificadas | PASS |
| Lint: cero errores nuevos | PASS |
| Tipos: cero errores nuevos | PASS |
| Análisis estático y compilación Python | PASS |
| Complejidad | PASS — 4 |
| Duplicación | PASS — sin bloques detectados |
| Build y coincidencia del código empaquetado | PASS |
| Smoke de ejecutable y controles Qt | PASS |
| Seguridad y permisos | PASS |
| Regresión completa | PASS |
| QA final: cuatro pasadas | PASS |
| Migración nueva de esquema | N/A — no cambia el esquema |
| Publicación | NO VERIFICADO — paquete local, versión sin cambiar |

## PROBLEMAS PENDIENTES

- Cuatro pasadas completadas: funcionalidad, regresiones, clean code y QA.
- No está publicado; la instalación actual conserva el filtro anterior hasta actualizarse.

**ESTADO FINAL: APROBADO PARA ENTREGA — ajuste local.**

