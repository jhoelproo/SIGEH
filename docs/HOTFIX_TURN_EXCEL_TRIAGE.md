# Comparación de adjuntos nuevos — 2026-09-06

Preparación sin publicación ni cambios productivos. Se leyeron ambos contratos adjuntos y las cuatro capturas. Las capturas confirman mensajes visibles, pero no demuestran por sí solas el estado PostgreSQL ni qué proceso escribió el Excel.

## No repetir lo aplicado

| Requisito | Evidencia en código | Estado |
| --- | --- | --- |
| Editar recibo heredado sin bloquearse contra sí mismo | `billing_admission_edit.apply_owned_receipt_context`, evaluador y guardado en `CALCULOS_QT.py`; pruebas PostgreSQL | Implementado en preparación; QA integral pendiente según informe asociado |
| Anulada/Urgencia/tombstone/claim ajeno/otro recibo continúan bloqueados | Evaluación canónica dentro del bloqueo de guardado; pruebas de exclusión y concurrencia | No relajar ni rehacer |
| Editar paciente por identidad maestra | Callbacks de `App._abrir_edicion_paciente`, `P:<paciente_id>`; pruebas SQLite | No repetir corrección |
| UUID opcional ausente = NULL | Normalización y regresiones de recibos existentes | Preservar |
| Generación integrada no expira únicamente por horario | `admission_sheet_state.ConfirmedTurnConfig`; `_generation_turn_config()` y `turno_config_es_vigente()` | Protección existente; todavía debe trazarse si la instalación afectada la está utilizando |
| Excel temporal/reemplazo | `_generate_versioned_excel`, `_update_canonical_excel`, actualización de latest | Parcial: reutilizar, no asumir cobertura de todas las rutas |

## Hallazgos nuevos que requieren trabajo

1. **Diálogo de turno todavía interpreta el horario para elegir la operación.** En `App._dialogo_turno._aplicar_cambio`, `administrative_override = not turno_config_es_vigente(candidato, momento=...)`. `candidato` es un diccionario normal, no un `ConfirmedTurnConfig`. Después `relevo_formal_actual` exige `not administrative_override`; si es override y el usuario no es Admin, aparece exactamente el mensaje de la captura. Esto explica el rechazo del diálogo, pero no demuestra aún qué ruta bloqueó previamente Generar hoja.
2. **Corrección administrativa debe auditarse antes de cambiarla.** `AdmissionOperationalSessionService.admin_set_admission_turn()` delega a `transition_primary_turn()`, con `allocate_central_turn_id` recibido. Falta seguir el llamador y el resultado persistido del incidente para determinar antes/después reales. No se ha probado que esa corrección haya cambiado un turno productivo.
3. **Persiste escritura directa del archivo Excel final.** `guardar_excel_seguro()` llama `wb.save(ruta_excel)`; no usa temporal validado. Otras rutas sí guardan temporales, pero `_generate_versioned_excel` reemplaza sin reabrir/verificar el XLSX. `_update_canonical_excel` usa un nombre temporal fijo `.pending.xlsx`. Falta probar exclusión mutua entre actualización, apertura manual y cierre; estos hechos son riesgos concretos, no prueba de la causa del archivo dañado observado.
4. **Recuperación de corrupción es invasiva para el artefacto.** `abrir_excel_workbook_seguro()` emite «Excel dañado» y llama a `recrear_excel_basico_por_corrupcion()`, que intenta mover el archivo y, si falla, eliminarlo. No se ejecutó esa ruta. Se requiere conservar evidencia y último archivo válido sin este fallback destructivo.
5. **Caso de búsqueda visible en Historial pero no en Facturación:** aún no se trazó su UUID central, fuente/turno, readiness, recibo y claim. No se puede afirmar una discrepancia de turno a partir de las pantallas. Se mantendrán todos los filtros legítimos y no se moverán pacientes para hacerlos aparecer.
6. **Edición maestra durante borrador de recibo:** la identidad se corrigió; falta verificar actualización coherente de datos visibles sin perder ítems/autorización/versión y convergencia física entre estaciones. No confundir bloquear campos de Facturación con prohibir editar la ficha maestra autorizada.

## Siguiente orden de trabajo

1. Pruebas de caracterización de fin nominal 19:59/20:00/20:01, medianoche y reinicio para la ruta integrada real, sin relevo ni mutación central.
2. Trazar configuración/corrección → comando central, diferenciando metadata de relevo. Conservar contrato de idempotencia de transición.
3. Reproducir escrituras concurrentes/fallo de Excel en directorio temporal; reutilizar un único guardado validado y coordinado, manteniendo formato oficial y datos fuera de transacciones SQLite.
4. Diagnóstico de identidad/eligibilidad del caso trazador, con fixture equivalente o lectura autorizada sin imprimir PHI.
5. Cerrar ramas pendientes de edición maestra/borrador y pruebas de regresión.

No se modificó todavía la lógica del turno ni Excel a partir de estas capturas. No hay causa raíz productiva confirmada del Excel ni de la búsqueda ausente. No hay autorización de publicación en estos contratos.

ESTADO FINAL: NO APROBADO PARA ENTREGA.
