# Auditoría de capacidad Supabase — 30/08/2026

Captura read-only: 2026-08-30 19:25:25 UTC.

- PostgreSQL físico: **366,414,995 bytes (349 MB)**.
- Límite de referencia del plan observado: 500 MB.
- Egreso mostrado en la captura del panel: **41.8 GB / 5 GB**.
- Conclusión: 41.8 GB era tráfico saliente acumulado, no tamaño de base.
- Filas eliminadas: **0**.
- Tamaño antes/después: **349 MB / 349 MB**; no se justificó una limpieza.

## Inventario completo de relaciones públicas

Los conteos provienen de `pg_stat_user_tables` y son estimaciones del catálogo;
los bytes provienen de `pg_total_relation_size`.

| Objeto | Filas est. | Bytes totales | % DB | Clase / decisión |
|---|---:|---:|---:|---|
| admission_sync_events | 43,552 | 94,502,912 | 25.791 | B; mínimo 180 días, solo tras backup y checkpoint |
| admission_attention_projection | 28,010 | 78,725,120 | 21.485 | A permanente; incluye tombstones |
| admission_patient_directory_events | 24,964 | 33,603,584 | 9.171 | B; mínimo 365 días, backup y checkpoint |
| admission_import_staging | 18,557 | 27,025,408 | 7.376 | C, pero actualmente protegido: ANALYZED/incompleto |
| legacy_entity_uuid_map | 43,584 | 23,191,552 | 6.329 | A; identidad histórica |
| pdf_storage | 115 en inventario | 22,929,408 | 6.258 | A hasta archivo externo y hash verificados |
| admission_patient_directory | 24,877 | 20,086,784 | 5.482 | A permanente |
| recibos | 5,247 | 18,128,896 | 4.948 | A permanente |
| recibo_document_versions | 4,728 | 8,871,936 | 2.421 | A auditoría documental |
| action_history | 11,420 | 4,907,008 | 1.339 | A auditoría |
| recibo_document_migration | 5,127 | 4,202,496 | 1.147 | A trazabilidad documental |
| recibo_items | 27,486 | 3,416,064 | 0.932 | A permanente |
| recibo_facturacion_history | 4,281 | 2,260,992 | 0.617 | A permanente |
| report_document_versions | 378 | 1,712,128 | 0.467 | A documental |
| billing_batch_receipts | 1,663 | 1,515,520 | 0.414 | A facturación |
| admission_operational_audit | 2,221 | 1,097,728 | 0.300 | A auditoría crítica |
| report_document_migration | 357 | 655,360 | 0.179 | A trazabilidad |
| report_history | 319 | 581,632 | 0.159 | A reportes |
| billing_shift_closure_details | 861 | 573,440 | 0.157 | A cierre de turno |
| document_external_files | 723 | 516,096 | 0.141 | A catálogo de archivo |
| universal_items | 1,036 | 352,256 | 0.096 | A catálogo |
| admission_operational_sessions | 2 | 286,720 | 0.078 | A identidad operacional |
| daily_reports | 142 | 278,528 | 0.076 | A reportes |
| active_sessions | 467 | 270,336 | 0.074 | B; sin purga automatizada en este cambio |
| billing_batch_events | 1,722 | 253,952 | 0.069 | A trazabilidad de facturación |
| admission_operational_devices | 6 | 172,032 | 0.047 | A estado operacional |
| admission_shift_inheritances | 263 | 163,840 | 0.045 | A regla de facturación |
| ars_items | 373 | 139,264 | 0.038 | A catálogo |
| admission_import_batches | 3 | 122,880 | 0.034 | A trazabilidad de importación |
| billing_shift_closures | 24 | 122,880 | 0.034 | A cierre de turno |
| admission_legacy_seed_conflicts | 210 | 114,688 | 0.031 | A evidencia de migración |
| admission_patient_seed_conflicts | 15 | 106,496 | 0.029 | A evidencia de migración |
| admission_billing_claims | 3 | 98,304 | 0.027 | A coordinación de facturación |
| billing_batches | 12 | 98,304 | 0.027 | A facturación |
| ars | 17 | 81,920 | 0.022 | A catálogo |
| database_capacity_history | 5 | 81,920 | 0.022 | B; conservar tendencia, sin purga ahora |
| admission_native_attentions | 1 | 65,536 | 0.018 | A compatibilidad |
| admission_native_shifts | 1 | 65,536 | 0.018 | A compatibilidad |
| session_control | 1 | 65,536 | 0.018 | A control operacional |
| admission_native_patients | 1 | 57,344 | 0.016 | A compatibilidad |
| admission_native_audit | 2 | 49,152 | 0.013 | A auditoría |
| admission_native_documents | 0 | 49,152 | 0.013 | A esquema documental |
| admission_operational_turn_intervals | 13 | 49,152 | 0.013 | A turnos |
| admission_patient_seed_registry | 1 | 49,152 | 0.013 | A control de baseline |
| sigeh_product_state | 1 | 49,152 | 0.013 | A continuidad de producción |
| user_catalog_favorites | 44 | 49,152 | 0.013 | A preferencia de usuario |
| users | 12 | 49,152 | 0.013 | A seguridad/usuarios |
| admission_operational_identity | 1 | 40,960 | 0.011 | A identidad central |
| admission_import_schema_migrations | 1 | 32,768 | 0.009 | A control de esquema |
| admission_native_settings | 2 | 32,768 | 0.009 | A configuración |
| admission_replication_event_floors | 2 | 32,768 | 0.009 | A checkpoint de recovery |
| billing_ars_profiles | 12 | 32,768 | 0.009 | A configuración de facturación |
| billing_pricing_settings | 1 | 32,768 | 0.009 | A configuración de precios |
| document_maintenance_config | 3 | 32,768 | 0.009 | A configuración de mantenimiento |
| schema_migrations | 5 | 32,768 | 0.009 | A control de esquema |
| sigeh_maintenance_events | 1 | 32,768 | 0.009 | A auditoría de mantenimiento |
| user_preferences | 6 | 32,768 | 0.009 | A configuración de usuario |
| admission_central_seeds | 0 | 24,576 | 0.007 | A control histórico; no reejecutar |
| admission_dataset_state | 1 | 24,576 | 0.007 | A estado del dataset |
| admission_quick_list_dismissals | 0 | 24,576 | 0.007 | A regla de facturación |
| admission_ars_crosswalk | 0 | 16,384 | 0.004 | A catálogo |

## Evidencia de no eliminación

- `admission_import_staging`: el único lote con filas está `ANALYZED`, modo
  `MERGE`, con 18,557 resultados incompletos. No es purgable.
- `pdf_storage`: una lectura posterior contó 116 filas, todas `UNKNOWN`, y cero
  copias externas verificadas. No es purgable.
- `admission_attention_projection`: 27,763 tombstones. Permanecen protegidos para
  conservar DELETE WINS frente a estaciones offline.
- Dead tuples bajos respecto al tamaño de las tablas; no se justifican
  `VACUUM FULL` ni `REINDEX` bloqueantes.

## Egreso

La causa del egreso ya quedó corregida en v1.0.9: un cursor local detenido en
49 descargaba repetidamente páginas desde un stream cuyo máximo era 123,087.
El ciclo ahora consulta una cabecera liviana, hace un solo pull, mantiene cursor
monotónico, aplica backoff y activa `EGRESS_LOOP_GUARD_TRIGGERED` si no progresa.
El consumo acumulado del ciclo vigente de Supabase no se puede retrotraer; el
objetivo menor a 5 GB debe comprobarse en el siguiente ciclo mediante medición
de 1 h, 6 h y 24 h en ambas estaciones.
