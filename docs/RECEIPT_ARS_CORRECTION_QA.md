# Corrección administrativa de ARS — QA local

## IMPLEMENTACIÓN

Archivos de producción de este ajuste: `CALCULOS_QT.py`, `billing_field_policy.py`, `receipt_edit_integrity.py`, `receipt_ars_correction.py`.

- ADMIN puede seleccionar otra ARS al editar un recibo asegurado pendiente o sin clasificar, con o sin vínculo.
- La cobertura permanece inmutable. Extranjeros y no asegurados conservan Contributivo y su historial de cobros.
- El cambio recalcula sala, medicamentos, materiales y servicios conservando las cantidades. Una tarifa ausente, inactiva, incompleta o modificada entre selección y guardado bloquea el cambio.
- El formulario revierte la selección sin modificar importes cuando falla la carga o falta un ítem; impide guardar mientras carga la tarifa.
- Un recibo vinculado actualiza la ARS de su paciente maestro y únicamente esa atención, junto con el recibo, sus ítems, versión documental y eventos de sincronización, en una sola transacción.
- Se conservan autor, número, fecha original de generación, fecha de servicio, identidad de atención y turno. Se usan los campos demográficos actuales de la proyección para evitar recuperar valores antiguos de un evento.
- Una consulta conserva su tipo y utiliza el precio de consulta de la nueva ARS; no se convierte en emergencia al editarse.
- Se mantienen los controles de elegibilidad, reservas, duplicidad, atención anulada y recibos de solo lectura. Una identidad central ausente o una ARS del paciente corregida en otra estación exige revisión en Admisión.

Pruebas nuevas: `tests/test_receipt_ars_correction.py`, `tests/test_receipt_ars_correction_postgres.py`, `tests/test_receipt_ars_correction_qt.py`.
Pruebas actualizadas para la nueva regla: `tests/test_receipt_edit_integrity.py`, `tests/test_billing_field_policy_qt.py`, `tests/test_receipt_edit_postgres.py`, `tests/test_admission_billing_consistency.py`.

## PRUEBAS

- RED: 5 fallos y 6 aprobadas antes de habilitar la regla administrativa (`output/ars-correction-red.xml`).
- Unitarias: roles, cobertura inmutable, estados vacío/solo lectura, cantidades 0/-1, precios negativos/no finitos, ítem ausente e identidades de otros pacientes o estaciones.
- Integración: PostgreSQL desechable en loopback. Guardado/reapertura, recálculo de sala e ítems, eventos, paciente inexistente/eliminado, cambios concurrentes, catálogo inactivo y rollback completo ante fallo documental.
- GUI: controles Qt reales; selección administrativa, callback de tarifa, cantidades/importes, reversión, respuesta obsoleta, error de red y bloqueo del guardado durante la carga.
- Regresión: suites de Admisión, edición, turnos, auditoría, documentos, historial y cobros directos.

## RESULTADOS

PASS: 2.009 casos regulares y 60 subpruebas: **2.068 aprobadas, 0 fallos, 0 errores, 1 omitida**. La única omitida es la comprobación opcional de capacidad real, que requiere habilitar un escenario externo explícito y no corresponde a este ajuste.

La colección final coincide con la ejecución, sin casos faltantes. La prueba de consulta agregada durante QA se ejecutó separadamente y se incluye una sola vez en el total. Evidencia: `output/ars-correction-full-summary.json`, `output/ars-correction-full-suite-completeness.json`, `output/ars-correction-group-*.xml`, `output/ars-correction-consultation.xml`.

Validación específica adicional del código final: **146 aprobadas, 0 fallos, 0 omitidas** (`output/ars-correction-delivery-tests.xml`). Los fallos iniciales de desarrollo no se presentan como validaciones aprobadas.

## COBERTURA

Medida con coverage.py, incluyendo ramas, sobre la versión final del código. Informes: `output/ars-correction-coverage-delivery.json` y `output/ars-correction-patch-coverage.json`.

| Alcance | Líneas | Ramas |
|---|---:|---:|
| Módulos de negocio y cambios de integración de ARS | 184/189: **97,35 %** | 63/72: **87,50 %** |
| Negocio crítico nuevo (`receipt_ars_correction.py`) | 84/85: **98,82 %** | 33/34: **97,06 %** |
| Política de campos | 30/31: 96,77 % | 9/10: 90,00 % |
| Invariantes de edición | 21/21: 100 % | 8/8: 100 % |

Se mide el código nuevo y modificado de este ajuste, no se afirma esta cobertura sobre todo el monolito heredado. El cálculo incluye 52 líneas ejecutables de integración en `CALCULOS_QT.py`, identificadas por las asignaciones/guardas nuevas de carga, edición y persistencia. No se modificaron umbrales ni configuraciones de cobertura.

## CALIDAD

Ruff (formato/lint), mypy y Bandit del módulo nuevo: PASS. Ruff diferencial del archivo heredado: 0 errores nuevos (327 preexistentes). Mypy diferencial del archivo heredado: 0 errores nuevos. No se cambiaron configuraciones ni añadieron supresiones.

Radon del módulo nuevo: máximo 10. La coordinación heredada de `MainWindow` y `save_receipt_with_items` excede 10 antes de este cambio; se preservó su estructura para limitar la modificación a permisos, carga de tarifa y transacción. Excepción técnica documentada: `output/ars-correction-legacy-complexity.json`. Pylint duplicate-code, mínimo 6 líneas: sin duplicación detectada en el módulo nuevo. Bandit: 0 hallazgos en los módulos de este ajuste; SQL parametrizado.

## BUILD

Comando final: `python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/ars-correction-app --workpath build/ars-correction-app build_app.spec`.

PASS, exit 0 (`output/ars-correction-build-clean.log`). Se reconstruyó con limpieza al detectar una función antigua en la caché del primer paquete. La comparación final confirma que el ejecutable contiene el código actual de `CALCULOS_QT.py` y los tres módulos de política/corrección (`output/ars-correction-package-verification.json`). No se modificó la versión ni se publicó en producción.

## QA

Cuatro pasadas: funcionalidad, regresiones, clean code y QA. Seguridad: SQL parametrizado; ADMIN se resuelve desde el usuario persistido; controles de identidad, elegibilidad y solo lectura conservados. No hubo escrituras ni QA destructivo sobre datos del hospital.

PASS: ejecutable real con `--self-test-pdf`, `--self-test-report-viewer` y `--self-test-reports`, todos exit 0. Evidencia: `output/ars-correction-package-smoke.json`.

PASS: apertura de `MainWindow`, carga de un recibo ficticio de la base local, ARS habilitada para ADMIN, cobertura bloqueada y cierre del proceso, exit 0. Revisión visual con el motor gráfico de Windows, sin mostrar una ventana al usuario; la primera captura offscreen tenía fuentes no legibles y se repitió con el motor nativo. Evidencia: `output/ars-correction-mainwindow-smoke.json` y `.png`. Se reutilizó el recibo sintético en la segunda ejecución tras comprobar que el control de duplicidad impedía crear otro igual.

## QUALITY GATES

| Quality Gate | Estado |
|---|---|
| Funcionalidad | PASS |
| Unit tests | PASS |
| Regression tests y colección completa | PASS |
| Integración SQL y transacciones/rollback | PASS |
| Coverage de código nuevo/modificado | PASS |
| Boundary tests | PASS |
| Formatter de módulos/pruebas nuevos; convenciones heredadas preservadas | PASS |
| Lint, 0 errores nuevos | PASS |
| Static analysis | PASS |
| Type checker, 0 errores nuevos | PASS |
| Complexity nueva <=10; excepción heredada documentada | PASS |
| Duplication nueva, comprobación de bloques | PASS |
| Build y coincidencia del código empaquetado | PASS |
| Smoke tests de ejecutable y formulario real | PASS |
| Seguridad y controles de permisos | PASS |
| QA final, cuatro pasadas | PASS |
| Migración nueva de esquema para ARS | N/A — no cambia el esquema |
| Despliegue en producción | N/A — este ajuste se entrega en local |

## PROBLEMAS PENDIENTES

- No está desplegado. El paquete se construye para validación local.
- Un ítem sin equivalencia por nombre/categoría en el catálogo nuevo necesita corregirse en el carrito; no se elimina ni se le asigna una tarifa inventada.
- Persisten advertencias y complejidad heredadas documentadas. Se evalúa diferencial de 0 errores nuevos; no se declara que todo el monolito carece de diagnósticos.

**ESTADO FINAL: APROBADO PARA ENTREGA — ajuste local.**
