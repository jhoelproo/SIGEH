# Preparación de edición de recibos y permisos — 2026-09-06

No publicado. Sin cambio de versión, tag, turno, PRIMARY, representante ni datos productivos. Sin migraciones nuevas.

## Causa y corrección

El primer guardado de un recibo heredado consume su herencia: pasa de PENDIENTE a COMPLETADA. La antigua consulta de `get_projected_billable_attention()` sólo admitía herencias PENDIENTES, por lo que rechazaba la reedición del mismo recibo y la GUI mostraba incorrectamente «Atención excluida». La prueba PostgreSQL reprodujo este fallo antes de corregirlo.

Además, `_lock_and_validate_admission_processing()` retornaba para el recibo propio antes de revalidar la proyección. No bastaba con ampliar un filtro: habría permitido editar recibos asociados a atenciones posteriormente anuladas. La carga del editor tampoco conservaba el UUID global guardado, obligando a resolver mediante referencias locales.

Ahora la consulta usa una sola evaluación canónica con `receipt_id`. Una herencia consumida sólo se reconoce para el recibo centralmente vinculado que la consumió. La existencia de otro recibo se prioriza sobre el propio para detectar duplicados. Se mantienen las verificaciones de estado, tombstone, tipo, readiness, ARS, cobertura, descarte y claim ajeno. No se crea una atención ni un UUID nuevo.

Al guardar, la proyección se bloquea y se revalida en la misma conexión/transacción del recibo; después del bloqueo se ejecuta una consulta nueva para observar un guardado concurrente confirmado. La edición conserva origen, procesamiento y estado de herencia persistidos. El editor conserva `admission_global_attention_id`.

## Propiedad de los datos

- La sala procede de la tarifa ARS almacenada, no de Admisión. Sólo ADMIN puede modificarla; se comprueba en controles y persistencia. Para editar un recibo, un no-Admin debe conservar su importe existente, incluso si la tarifa cambió después: no se reescriben importes históricos.
- Auxiliar sin paciente validado: nombre, diagnóstico, fecha, ARS/cobertura y sala quedan deshabilitados. Persistencia también rechaza cambios de nombre, diagnóstico, fecha, ARS y sala al editar un recibo sin vínculo validado.
- Con atención validada: nombre, fecha y ARS/cobertura no se editan desde Facturación. La autorización sigue disponible en recibos editables. Sala continúa reservada a ADMIN.
- La fecha enviada al guardado debe coincidir con Admisión. El snapshot se compara contra fecha, nombre, ARS, NSS, cédula y tipo de atención centrales, cuando esos campos existen en el contrato recibido. Las diferencias generan `AdmissionDataChanged`, con etiquetas de campos, no valores personales.
- Un error de consulta no se transforma en exclusión ni en resultado vacío. Se conserva el borrador y no se libera la reserva por ese error. La UI no guarda silenciosamente sobre una diferencia detectada.
- Se conserva la semántica y los permisos de bypass, la normalización UUID opcional, la auditoría, rollback y controles de duplicación.

La corrección previa del editor de pacientes se conserva: las fichas maestras usan `P:<paciente_id>` y no se confunden con `A:<attention_id>`.

## Archivos

Aplicación:

- `CALCULOS_QT.py`: elegibilidad, bloqueo y guardado, carga del recibo, preflight de GUI y permisos de sala.
- `billing_admission_edit.py`: contexto de recibo propio y detección de diferencias.
- `billing_field_policy.py`: reglas de campos y validación monetaria.
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: identidad inequívoca de edición de paciente.

Pruebas nuevas:

- `tests/test_patient_edit_dialog_identity.py`
- `tests/test_inherited_receipt_save.py`
- `tests/test_billing_field_policy.py`
- `tests/test_billing_field_policy_qt.py`
- `tests/test_receipt_edit_guards_postgres.py`
- `tests/test_billing_edit_failure_ui.py`

Regresiones adaptadas al contrato canónico y fixtures completos:

- `tests/test_admission_billing_consistency.py`
- `tests/test_admission_cancellation_receipt_trash_v104.py`
- `tests/test_admission_receipt_link.py`
- `tests/test_admission_validation_extensions.py`
- `tests/test_current_shift_and_privileged_billing.py`
- `tests/test_receipt_optional_uuid_postgres.py`

## Evidencia y entorno

Directorio de QA aislado: `D:/SIGEH_QA_0753897894054e8e9b5dc973cd5cc469`.

Los servidores PostgreSQL de pruebas son desechables, en loopback, con fixtures sintéticos. No se realizó QA destructivo contra producción. El intento inicial de compilación en C: falló por falta de espacio; las verificaciones posteriores se trasladaron a D: sin borrar SQLite ni archivos hospitalarios.

La ejecución monolítica de la suite abortó en `tests/test_admission_import_task_manager.py::test_import_progress_is_monotonic_and_apply_reuses_the_same_task`. El proceso informó `Fatal Python error: Aborted` mientras existía `AdmissionDatabaseImportWorker`. El mismo módulo aislado volvió a abortar. No se atribuye una causa definitiva sin diagnóstico; no se modificó el importador para esconderlo. Evidencia: `results/tests.log` y `results/isolated-import.log`.

Se ejecuta separadamente el resto de la suite, sin considerar esa separación como aprobación de la suite integral.

Resultado del resto de la suite: 1.168 PASS, 0 FAIL, 1 SKIPPED, 60 subtests PASS, 445,44 s. Evidencia: `results/split-suite/preparation-tests.xml` y `results/split-suite.log`. El importador fue excluido de esta ejecución porque aborta el proceso; sus dos fallos permanecen registrados, no omitidos de la conclusión integral. Pruebas adicionales de ramas de rechazo/UI: 11 PASS (`results/supplement-final-tests.xml`). Los conteos de ambas ejecuciones se solapan y no deben sumarse como pruebas únicas.

Cobertura medida con coverage.py y combinada conservando archivos originales: líneas modificadas de `CALCULOS_QT.py` 78/80 (97,50 %), ramas 33/34 (97,06 %); edición V15 10/10 líneas y 4/4 ramas (100 %); ambos módulos nuevos 100 % líneas/ramas. Evidencia: `results/split-suite/coverage.json` y `prepared-quality.json`. Son métricas del diff contra HEAD y de módulos nuevos, no cobertura global de la aplicación.

## Build y smoke

Comando ejecutado con TEMP/TMP y caché en D:

```text
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath D:/SIGEH_QA_0753897894054e8e9b5dc973cd5cc469/dist --workpath D:/SIGEH_QA_0753897894054e8e9b5dc973cd5cc469/build build_app.spec
```

PASS, salida 0. Se construyeron `SIGEH.exe` y `CALCULOS_QT.exe`. Se registraron avisos de imports opcionales; no se consideran silenciosamente resueltos. Esta preparación no construye un nuevo updater ni un ZIP de distribución.

Sobre el ejecutable resultante, con perfil temporal, directorio de trabajo ajeno al repositorio y URL de base inválida deliberada en loopback:

- `--check-v15-package`: PASS, salida 0. Widget de Admisión, Historial y configuración construidos. Evidencia `smoke/v15.json`.
- `--self-test-pdf`: PASS, salida 0; recibo sintético generado.
- `--self-test-reports`: PASS, salida 0; 3 PDF y 2 Excel sintéticos generados.

Esto demuestra carga/generación técnica, no QA visual manual exhaustivo ni funcionamiento en dos PCs hospitalarias.

SHA-256 del `CALCULOS_QT.exe` de QA:

```text
CDF35D22AA0119D165F31B890246F1F64DBFA2419E17A9E79ADC864B103AADD6
```

## Calidad y riesgos

- Ruff: módulos nuevos y pruebas nuevas PASS; comparación del código de aplicación con HEAD: 0 diagnósticos nuevos. Monolitos conservan 327 y 85 diagnósticos heredados.
- Formatter: módulos nuevos y pruebas nuevas PASS. No se reformateó masivamente el monolito.
- Mypy `--check-untyped-defs billing_admission_edit.py billing_field_policy.py`: PASS. Tipos del monolito integral: NO VERIFICADO.
- `py_compile` de los cuatro archivos de aplicación: PASS.
- Duplicación jscpd: 0,5144 % en los cuatro archivos; ningún tramo duplicado detectado intersecta las líneas modificadas. No equivale a demostrar ausencia de toda duplicación semántica.
- Radon: módulos nuevos, máximo 9. Wrapper de proyección 7→5; bloqueo de guardado 18→15. Evaluador 21→22 y guardado heredado 143→157. El gate estricto de complejidad integral no pasa. Se necesita separar responsabilidades del guardado sin alterar sus transacciones, en un cambio controlado y nuevamente verificado.
- No hay prueba física de los recibos afectados en la instalación hospitalaria. No se publicó ni se instaló esta compilación de QA.
- La detección de diferencias exige revisión/revalidación; no se implementó una actualización silenciosa del borrador ni se amplió el alcance a un rediseño de edición.

## Estado

ESTADO FINAL: NO APROBADO PARA ENTREGA.

Bloqueos explícitos: aborto reproducido del importador en QA, complejidad del guardado por encima del gate y validación manual pendiente. Las pruebas técnicas del hotfix no sustituyen esos gates.
