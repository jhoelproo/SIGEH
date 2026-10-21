# QA — Permisos, ARS y sincronización entre Admisión y Facturación

Fecha: 15/10/2026. Alcance: código y compilación local de validación. Producción permanece sin modificar.

ESTADO FINAL: APROBADO PARA ENTREGA

## IMPLEMENTACIÓN

- Excepciones individuales «Predeterminado / Permitir / Denegar» sobre los permisos del rol, con registro de cambios, control de acceso y comprobación del usuario vigente en las operaciones protegidas. El rol y los permisos existentes se conservan como predeterminados.
- Administración integrada en Usuarios. Las actualizaciones de permisos utilizan la consulta de sesión existente; los formularios abiertos recuperan o pierden acceso y el acceso sin conexión conserva la última configuración sincronizada.
- Cambio controlado de ARS reutilizando el catálogo y la validación de tarifas. Los cambios administrativos del paciente no cambian automáticamente importes del recibo. La edición de ARS recalcula la tarifa completa o revierte; limpia la autorización anterior solo cuando se aplica. Los recibos facturados mantienen las restricciones de reapertura existentes.
- Autorizaciones limitadas a dígitos ASCII, incluido pegado, con ceros iniciales; validación en formularios, guardado central y cola local. Se mantienen los requisitos anteriores de autorización completa y revisión; no se reescribe indiscriminadamente el historial.
- Correcciones administrativas utilizan los datos actuales de la atención y solo propagan campos modificados. Una cobertura previamente válida se conserva cuando sus datos no cambian; los estados incompletos desactualizados se recalculan con los datos vigentes.
- El paciente puede editarse aunque tenga recibos. Se notifica la existencia de recibos y se habilita «Editar recibo» con selección identificable. Nombre, NSS y cédula se sincronizan por vínculos existentes; se genera una versión actualizada del documento conservando las anteriores.
- Las transacciones conservan IDs, atención original, conceptos, estados financieros e importes. Las listas pendientes se actualizan; las copias enviadas permanecen congeladas. Se registran los cambios relevantes y se invalidan/actualizan las vistas mediante los mecanismos existentes.

### Archivos de aplicación

- [ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py)
- [CALCULOS_QT.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/CALCULOS_QT.py)
- [admission_demographics.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_demographics.py)
- [admission_v15_adapter.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_v15_adapter.py)
- [billing_admission_edit.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_admission_edit.py)
- [local_receipts.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/local_receipts.py)
- [patient_directory.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/patient_directory.py)
- [receipt_ars_correction.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_ars_correction.py)
- [receipt_documents.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_documents.py)
- [receipt_list_consistency.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_list_consistency.py)
- [offline_auth.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/offline_auth.py)
- [admission_receipt_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_receipt_dialog.py)
- [admission_receipt_sync.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_receipt_sync.py)
- [authorization_validation.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/authorization_validation.py)
- [user_permissions.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/user_permissions.py)
- [user_permissions_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/user_permissions_dialog.py)

Migración aditiva: [supabase/migrations/20261015161253_user_permission_overrides.sql](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/supabase/migrations/20261015161253_user_permission_overrides.sql). Añade la columna JSONB de excepciones con valor vacío predeterminado; no elimina ni transforma datos históricos. El arranque incorpora la misma ampliación idempotente.

### Pruebas creadas

- [tests/test_oct15_demographic_readiness.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_demographic_readiness.py)
- [tests/test_oct15_gui_and_authorization.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_gui_and_authorization.py)
- [tests/test_oct15_live_controls.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_live_controls.py)
- [tests/test_oct15_offline_permissions.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_offline_permissions.py)
- [tests/test_oct15_permissions_store.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_permissions_store.py)
- [tests/test_oct15_permission_and_authorization.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_permission_and_authorization.py)
- [tests/test_oct15_sync_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_oct15_sync_postgres.py)

### Pruebas existentes actualizadas

- [tests/test_admission_receipt_link.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_receipt_link.py)
- [tests/test_admission_validation_extensions.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_admission_validation_extensions.py)
- [tests/test_integral_emergency_to_monthly_list.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_integral_emergency_to_monthly_list.py)
- [tests/test_monthly_batch_removal.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_batch_removal.py)
- [tests/test_monthly_metadata_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_metadata_postgres.py)
- [tests/test_monthly_patient_name_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_monthly_patient_name_postgres.py)
- [tests/test_patient_edit_dialog_identity.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_patient_edit_dialog_identity.py)
- [tests/test_receipt_ars_correction_qt.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_ars_correction_qt.py)
- [tests/test_receipt_continuity.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_continuity.py)
- [tests/test_receipt_list_consistency.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_list_consistency.py)
- [tests/test_receipt_list_consistency_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_list_consistency_postgres.py)
- [tests/test_receipt_metadata_policy.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_metadata_policy.py)
- [tests/test_receipt_optional_uuid_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_optional_uuid_postgres.py)
- [tests/test_receipt_trash_and_deduplication.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_trash_and_deduplication.py)
- [tests/test_receipt_trash_purge_service.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_receipt_trash_purge_service.py)
- [tests/test_recent_demographics_postgres.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/tests/test_recent_demographics_postgres.py)

Se adaptaron datos sintéticos a autorizaciones numéricas, usuarios persistidos para permisos vigentes y puntos de inyección de fallos del servicio documental compartido. Se conservaron las comprobaciones de integridad, duplicados y rollback.

## PRUEBAS

- Unitarias: prioridad permitir/denegar, valores predeterminados, permisos inválidos y concurrentes, rechazo de roles suministrados por el llamador, autorización vacía, 0 como texto, longitud 1/40/41, caracteres ASCII/no ASCII, teclado y pegado.
- Integración PostgreSQL local: persistencia de permisos, concesión de ARS sin cambiar el rol, sincronización de NSS/nombre, recibos pendientes y facturados, actualización de documentos, listas y auditoría, lectura desde otra conexión y rollback ante fallo documental.
- GUI Qt: administración de usuarios y permisos, botones y señales, pérdida/restauración de acceso, conservación de bloqueos previos, módulos nuevos y reutilizados, aviso de recibos existentes y selección del recibo original.
- Regresión: suite completa de 228 módulos en 51 grupos aislados; incluyó duplicados, papelera, edición de recibos, facturación sin seguro, sincronización, interfaz adaptable, documentos y reportes.
- RED/GREEN: se registraron fallos iniciales de permisos/autorizaciones y pérdida de elegibilidad; sus pruebas pasan tras las correcciones. [output/oct15/readiness-red.xml](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15/readiness-red.xml) y [output/oct15/red.xml](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15/red.xml). También se mantuvo la regresión del botón que no se reactivaba tras conceder permisos.

## RESULTADOS

- Pruebas recogidas: **2925**.
- PASS: **2924**, más **74 subcasos**.
- FAIL: **0**. Errores: **0**.
- Omitida: **1**. Corresponde a la prueba opcional de capacidad real, que exige RUN_REAL_CAPACITY_INTEGRATION=1; no aplica a estos cambios y no se habilitó contra producción.

Evidencia: [output/oct15-test-summary.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-test-summary.json), [output/oct15-verified-groups.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-verified-groups.json) y los 51 informes JUnit asociados. La consolidación comprueba la huella de los 16 archivos de aplicación, las pruebas de cada grupo y que todos los módulos recogidos estén incluidos una sola vez. Las ejecuciones con fallos previas se conservan separadas; no se contabilizan como aprobadas ni se suman repeticiones al total.

## COBERTURA

Medida real con coverage.py y ramas habilitadas. Alcance: líneas semánticamente nuevas/modificadas respecto a HEAD y contenido completo de los módulos nuevos; no es la cobertura total del código heredado.

- Código cambiado: **99.19 % de líneas (368/371)** y **99.04 % de ramas (103/104)**.
- Módulos nuevos: **100 % de líneas (136/136)** y **100 % de ramas (34/34)**.
- Lógica crítica cambiada: **99,19 % / 99,04 %**; supera los objetivos de 95 % / 90 %.
- Gates por archivo cambiado: **PASS**, mínimo 90 % de líneas y 85 % de ramas donde existen.

| Archivo | Líneas cambiadas | Ramas cambiadas |
| --- | ---: | ---: |
| [admission_receipt_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_receipt_dialog.py) | 100 % | 100 % |
| [admission_receipt_sync.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_receipt_sync.py) | 100 % | 100 % |
| [authorization_validation.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/authorization_validation.py) | 100 % | 100 % |
| [user_permissions.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/user_permissions.py) | 100 % | 100 % |
| [user_permissions_dialog.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/user_permissions_dialog.py) | 100 % | 100 % |
| [ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py) | 100 % | N/A — sin ramas |
| [CALCULOS_QT.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/CALCULOS_QT.py) | 98.17 % | 98.15 % |
| [admission_demographics.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_demographics.py) | 100 % | 100 % |
| [admission_v15_adapter.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/admission_v15_adapter.py) | 100 % | 100 % |
| [billing_admission_edit.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/billing_admission_edit.py) | 100 % | 100 % |
| [local_receipts.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/local_receipts.py) | 100 % | N/A — sin ramas |
| [patient_directory.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/patient_directory.py) | 100 % | N/A — sin ramas |
| [receipt_ars_correction.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_ars_correction.py) | 100 % | 100 % |
| [receipt_documents.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_documents.py) | 100 % | N/A — sin ramas |
| [receipt_list_consistency.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/receipt_list_consistency.py) | 100 % | N/A — sin ramas |
| [offline_auth.py](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/offline_auth.py) | 100 % | 100 % |

Evidencia: [output/oct15-coverage-summary.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-coverage-summary.json) y [output/oct15-coverage.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-coverage.json). Permanecen sin ejecutar tres líneas de guardas en CALCULOS_QT y una rama de construcción de interfaz; están declaradas en el informe, sin exclusiones añadidas.

## CALIDAD

| Comprobación | Estado | Evidencia / límite |
| --- | --- | --- |
| Formatter | PASS | Ruff en módulos nuevos y pruebas nuevas; formato por rangos modificados en archivos heredados con equivalencia AST |
| Lint | PASS | Ruff: cero errores nuevos; módulos/pruebas nuevos sin errores |
| Type checker | PASS | mypy diferencial, cero errores nuevos; módulos nuevos con check-untyped-defs |
| Análisis estático | PASS | Ruff, mypy y Bandit comparados con HEAD |
| Complejidad | PASS | Radon: máximo nuevo 9; ninguna función heredada aumenta complejidad por encima del umbral |
| Duplicación | PASS | Pylint duplicate-code: cero bloques nuevos detectados; no se estima un porcentaje |
| Seguridad | PASS | Cero hallazgos Bandit nuevos; permisos centrales, SQL parametrizado, rollback y protección de recibos comprobados |
| Limpieza | PASS | git diff --check sin errores; sin código de depuración añadido |

Excepción heredada documentada: save_receipt_with_items conserva una complejidad alta, reducida de 189 a 187; otras funciones heredadas mayores que 10 permanecen iguales o disminuyen. Se extrajo la lógica nueva en funciones pequeñas sin realizar una refactorización masiva de facturación. No se declara saneado todo el repositorio: Bandit conserva 307 hallazgos heredados, sin nuevos hallazgos en este cambio.

Evidencia: [output/oct15-quality.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-quality.json), [output/oct15-format-ranges.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-format-ranges.json) y los registros oct15-lint/types/security/duplication asociados.

## BUILD

Comando ejecutado desde la raíz del repositorio:

    python -X utf8 -m PyInstaller --noconfirm --distpath output/oct15-app --workpath output/oct15-build build_app.spec

**PASS — salida 0.** Compilación local: [output/oct15-app/SIGEH/SIGEH.exe](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-app/SIGEH/SIGEH.exe). Se mantiene la versión pública 1.2.12: este resultado es una compilación de validación, no una nueva publicación.

Se comparó el código incluido en los ejecutables con los 16 archivos actuales, el lanzador y la configuración de base de datos. También coinciden las plantillas PDF y los metadatos de versión. Evidencia: [output/oct15-build.log](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-build.log) y [output/oct15-package-verification.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-package-verification.json).

## QA

- Smoke: **PASS**, cuatro ejecuciones con salida 0: lanzador, PDF, visor y reportes. [output/oct15-smoke.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15-smoke.json).
- Interfaz: **PASS**, controles Qt reales con datos sintéticos, renderizado offscreen y estilo de producción. [output/oct15/gui-qa.json](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15/gui-qa.json), [output/oct15/users-admin.png](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15/users-admin.png), [output/oct15/permissions-dialog.png](C:/Users/ampar/OneDrive/Desktop/PROYECTOS/hosp/sigeh-receipt-edit-fix/output/oct15/permissions-dialog.png).
- Regresión e integración: **PASS**, suite consolidada y clientes/conexiones independientes sobre PostgreSQL local. No se efectuó QA destructivo ni escritura de pruebas en producción.
- Pasada 1, funcionalidad: requisitos contrastados con los flujos y pruebas de permisos, ARS, autorización, elegibilidad, vínculo y sincronización.
- Pasada 2, regresiones: suite completa aplicable y cuatro smoke del ejecutable.
- Pasada 3, clean code: nombres, funciones, límites, duplicación, complejidad y alcance revisados; deuda heredada indicada arriba.
- Pasada 4, QA: pruebas, cobertura, formato, lint, tipos, análisis estático, build y smoke comprobados con sus registros.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad | PASS |
| Unit tests | PASS |
| Regression tests | PASS |
| Integration tests aplicables | PASS |
| Coverage | PASS |
| Boundary tests | PASS |
| Formatter | PASS |
| Lint | PASS |
| Static analysis | PASS |
| Type checker aplicable | PASS |
| Complexity | PASS — excepción heredada documentada, sin aumento |
| Duplication | PASS — sin bloques nuevos detectados |
| Build | PASS |
| Smoke test | PASS |
| Security checks aplicables | PASS |
| QA final | PASS |
| Prueba opcional de capacidad real | N/A — opt-in, ajena al cambio |

## PROBLEMAS PENDIENTES

- No se publicó una nueva versión ni se aplicó la migración a Supabase de producción en esta entrega. Antes del despliegue debe incorporarse la migración y distribuirse la aplicación actualizada a las estaciones; clientes anteriores conservan sus reglas de permisos anteriores.
- NO VERIFICADO: instalación simultánea en dos computadoras físicas. La consistencia se validó con conexiones y réplicas independientes de prueba, sin afirmar una prueba física realizada.
- El acceso sin conexión utiliza los permisos de la última sincronización; una revocación remota se reconoce al recuperar comunicación.
- La deuda histórica de lint/tipos/seguridad y complejidad no relacionada no se modificó. No se detectaron errores nuevos en el alcance validado.

ESTADO FINAL: APROBADO PARA ENTREGA
