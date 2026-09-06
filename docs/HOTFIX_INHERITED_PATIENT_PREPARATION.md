# Preparación: recibos heredados y edición de pacientes

> Informe histórico de la primera reproducción. La continuación y el estado vigente están en `HOTFIX_RECEIPT_EDIT_AND_PERMISSIONS_QA.md`; los resultados de abajo describen el código anterior a esa continuación.

Estado: EN PREPARACIÓN / NO APROBADO PARA ENTREGA. Sin publicación, cambio de versión ni migraciones productivas.

## Implementación de edición de pacientes

Archivo modificado: `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`.

La ruta reproducida del mensaje «No se pudo cargar los datos actuales del paciente» es:

1. `buscar_paciente_para_edicion()` devuelve una ficha maestra sin atención. Su `id` es el ID del paciente y `paciente_id` identifica inequívocamente la ficha.
2. El diálogo convertía ese `id` a `A:<id>`, que significa atención, no paciente.
3. `guardar_edicion()` volvía a consultar por esa identidad incorrecta: devolvía `None` si no había atención con ese número. Si coincidía con una atención ajena, podía recuperar otra ficha.

Los callbacks `_llenar_formulario_paciente`, `cargar_paciente._buscar` y `seleccionar_resultado_paciente` conservan ahora `P:<paciente_id>`. Las filas de selección llevan esa identidad, independientemente del ID de atención visible. La carga inicial relee la ficha maestra vigente; no usa los datos históricos de la atención como datos actuales para editar. Si la ficha desapareció, no carga el snapshot histórico como sustituto.

No se cambiaron permisos, PRIMARY, turnos, representantes ni las funciones de persistencia. Las pruebas comprueban actualización de la ficha solicitada, preservación de su identidad y de otra ficha, ausencia de nuevas fichas, y atenciones históricas intactas. Los callbacks probados son los reales, extraídos sin reescribirlos mediante AST; no sustituyen QA manual de la ventana completa.

## Incidente de recibos heredados: pendiente, no oculto

`tests/test_inherited_receipt_save.py` reproduce el defecto usando PostgreSQL temporal y datos sintéticos:

1. Atención activa, EMERGENCIA, preparada para facturación y herencia explícita PENDIENTE.
2. El primer guardado funciona y consume la herencia, dejándola COMPLETADA.
3. `get_projected_billable_attention()` solamente admite turno actual o herencia PENDIENTE; al reeditar el recibo propio devuelve `None`.

La prueba está deliberadamente en FAIL, no omitida ni marcada como éxito esperado. `CALCULOS_QT.py` aún no se modificó en esta preparación. Por tanto el hotfix integral NO está corregido.

Además, `_lock_and_validate_admission_processing()` retorna anticipadamente para el recibo propio antes de validar nuevamente la proyección. Corregir sólo el filtro de la GUI dejaría ese defecto de seguridad sin resolver.

Trabajo requerido para cerrar este incidente:

- Una evaluación canónica con contexto explícito del recibo que se edita.
- Reconocer una herencia consumida exclusivamente por ese recibo, sin admitir un segundo recibo.
- Revalidar estado activo, tombstone, EMERGENCIA, readiness, ARS, cobertura, descarte y claims incluso en edición propia.
- Mantener bloqueo y revalidación dentro de la misma transacción del guardado, usando la identidad global y sus referencias legítimas.
- Diferenciar errores centrales de exclusiones reales y conservar motivos seguros en UI/log.
- Probar edición propia, recibo ajeno, claim propio/ajeno, anulación posterior, urgencia, rollback y guardados concurrentes.

## Validaciones y evidencia

- Pruebas específicas y relacionadas de pacientes: 49 PASS. Evidencia: `output/inherited-patient/patient-tests.xml`.
- Reproducción PostgreSQL del defecto heredado: FAIL en la aserción de elegibilidad tras el primer guardado. No es un fallo de conexión ni del fixture.
- Ruff de ambos archivos de pruebas: PASS.
- Formatter de ambos archivos de pruebas: PASS.
- Compilación Python de archivo modificado y pruebas: PASS. Esto NO equivale a build de ejecutables.
- Complejidad de callbacks modificados (Radon): carga de formulario 5, búsqueda 7, selección 4. Contenedor heredado 13; no se refactorizó el diálogo completo.
- Duplicación del módulo (jscpd): 0,50 % global; 6 clones / 87 líneas. La métrica global no implica que toda duplicación sea nueva.
- Regresión completa: 1.100 PASS, 1 FAIL (recibo heredado reproducido), 1 SKIPPED y 60 subtests PASS; 210,56 segundos. Evidencia: `output/inherited-patient/preparation-tests.xml`. Comando: `python output/inherited-patient/run_suite.py`; el runner inicia y detiene PostgreSQL desechable en loopback.
- Cobertura real del código de aplicación modificado: 10/10 líneas ejecutables y 4/4 ramas (100 % / 100 %). Evidencia: `output/inherited-patient/coverage.json` y `quality.json`. No es la cobertura global del monolito.
- Lint comparado con HEAD: 85 diagnósticos heredados antes y después, 0 nuevos. No se declara el monolito libre de advertencias.

## Quality gates de esta preparación

| Gate | Estado | Alcance |
| --- | --- | --- |
| Funcionalidad integral | FAIL | El incidente de recibo heredado sigue reproduciéndose. |
| Unitarias / integración / regresión de pacientes | PASS | 49 pruebas dirigidas, SQLite temporal y callbacks reales; no prueba física. |
| Suite completa | FAIL | Único fallo: regresión nueva del defecto heredado. |
| Cobertura del cambio de edición | PASS | Líneas 100 %, ramas 100 %. |
| Lint sin errores nuevos | PASS | Comparación con HEAD y pruebas nuevas verificadas. |
| Formatter de pruebas nuevas | PASS | Ruff format --check. |
| Complejidad de callbacks modificados | PASS | Máxima 7; contenedor heredado 13, fuera de refactorización general. |
| Tipos / formatter integral del monolito | NO VERIFICADO | No se ejecutó un chequeo integral de tipos ni reformateo masivo. |
| Compilación Python | PASS | py_compile del módulo modificado y ambas pruebas nuevas. |
| Build de ejecutables / smoke del ZIP | NO VERIFICADO | No se construye ni aprueba paquete final con el defecto heredado pendiente. |
| QA manual del diálogo completo | NO VERIFICADO | Callbacks y persistencia probados; validación en instalación afectada pendiente. |
| Seguridad focalizada | PASS | Los casos de colisión de IDs no editan otra ficha; no se ampliaron permisos. No equivale a pentest. |
| QA integral / entrega | FAIL | Trabajo parcial, no publicable. |

## QA y limitaciones

Producción: ninguna escritura realizada. No se crearon pacientes ni recibos hospitalarios de prueba. No se cambió versión, tag, GitHub ni paquete entregable.

Pendientes: corrección integral de recibos heredados, prueba manual de ventana completa en la instalación afectada, actualización coherente de campos visibles del borrador activo, build y smoke del paquete final. No se declara verificado el chequeo de tipos de la aplicación heredada.

ESTADO FINAL: NO APROBADO PARA ENTREGA.
