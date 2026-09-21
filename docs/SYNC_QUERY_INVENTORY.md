# Inventario estático de consultas de sincronización

Las líneas se refieren a la rama de optimización. Incluye lecturas y escrituras; no ejecuta consultas.

| Archivo / función | Línea | Tablas |
|---|---:|---|
| admission_hybrid.py / _allocate_next_central_turn_id | 4351 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / repair_ambiguous_current_turn_identity | 4386 | admission_attention_projection |
| admission_hybrid.py / get_attention_by_global_id | 7449 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / current_turn_attention_events | 7483 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / backfill_projection_events | 7619 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / backfill_projection_payloads | 7755 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / _materialize_attention | 7831 | admission_attention_projection |
| admission_hybrid.py / _push_event_pre_hybrid_v2 | 8118 | admission_sync_events |
| admission_hybrid.py / push_event | 8185 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / _bulk_materialize_missing_projections | 8464 | admission_attention_projection |
| admission_hybrid.py / push_events | 8541 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / events_after | 8717 | admission_sync_events |
| admission_hybrid.py / event_window | 8732 | admission_sync_events |
| admission_hybrid.py / projection_has_attention | 8753 | admission_attention_projection |
| admission_hybrid.py / projection_snapshot_events | 8767 | admission_attention_projection |
| admission_hybrid.py / rematerialize_attention_events | 8786 | admission_sync_events, admission_attention_projection |
| admission_hybrid.py / missing_projection_entity_ids | 8908 | admission_attention_projection |
| patient_directory.py / _insert_event | 227 | admission_patient_directory_events |
| patient_directory.py / events_after | 617 | admission_patient_directory_events |
| patient_directory.py / event_window | 628 | admission_patient_directory_events |
| patient_directory.py / event_headers_after | 648 | admission_patient_directory_events |
| admission_v15_adapter.py / _legacy_projection_readthrough | 2659 | admission_sync_events, admission_attention_projection |
| admission_v15_adapter.py / _load_central_turn_rows | 3036 | admission_attention_projection |
| admission_v15_adapter.py / _confirm_central_zero | 3171 | admission_attention_projection |
| admission_v15_adapter.py / _statistical_report_records_query | 3251 | admission_attention_projection |
| CALCULOS_QT.py / _apply_billing_shift_closure_snapshot_migration | 2678 | admission_attention_projection |
| CALCULOS_QT.py / _apply_admission_cancellation_receipt_trash_migration | 2819 | admission_attention_projection |
| CALCULOS_QT.py / db_init | 2961 | admission_attention_projection |
| CALCULOS_QT.py / evaluate_attention_billing_eligibility | 5105 | admission_attention_projection |
| CALCULOS_QT.py / sync_admission_projection | 5306 | admission_attention_projection |
| CALCULOS_QT.py / ensure_admission_history_projection | 5459 | admission_attention_projection |
| CALCULOS_QT.py / diagnose_billing_admission_queue | 6321 | admission_attention_projection |
| CALCULOS_QT.py / claim_projected_billable_attention | 7803 | admission_attention_projection |
| CALCULOS_QT.py / load_current_shift_billing_summary | 7936 | admission_attention_projection |
| CALCULOS_QT.py / _legacy_build_shift_closure_report_data | 8135 | admission_attention_projection |
| CALCULOS_QT.py / capture_shift_closure_snapshot | 8533 | admission_attention_projection |
| CALCULOS_QT.py / _lock_and_validate_admission_processing | 10692 | admission_attention_projection |
| CALCULOS_QT.py / restore_recibo | 11789 | admission_attention_projection |
| CALCULOS_QT.py / _admission_projection_repair_callback | 30945 | admission_attention_projection |
| CALCULOS_QT.py / diagnose_missing_search | 5848 | admission_attention_projection |
| CALCULOS_QT.py / get_operational_candidates | 5874 | admission_attention_projection |
| CALCULOS_QT.py / load_admission_history_batch | 6035 | admission_attention_projection |
| CALCULOS_QT.py / build_document | 6778 | admission_sync_events, admission_attention_projection |
| admission_database_import.py / _cloud_rows | 510 | admission_attention_projection |
| admission_database_import.py / apply | 1120 | admission_sync_events, admission_attention_projection |
| database_capacity.py / _attention_projection_ready | 616 | admission_sync_events, admission_attention_projection |
| database_capacity.py / _patient_projection_ready | 645 | admission_patient_directory_events |
| database_capacity.py / _persist_sample | 663 | admission_sync_events, admission_patient_directory_events |
| database_capacity.py / analyze | 699 | admission_sync_events, admission_patient_directory_events |
| admission_turn_history.py / search_turns | 25 | admission_attention_projection |
| admission_turn_history.py / selected_turn_records | 81 | admission_attention_projection |
| admission_authorship.py / repair_admission_authorship | 4 | admission_sync_events, admission_attention_projection |
| admission_urgency_repair.py / repair_urgency_projection | 4 | admission_attention_projection |
| billing_closure_recovery.py / closed_turn_attentions | 98 | admission_attention_projection |
| billing_historical_cancellation.py / inherited_attention_is_active_sql | 14 | admission_attention_projection |
| billing_historical_cancellation.py / _locked_attention | 50 | admission_attention_projection |
| patient_seed_tool.py / _write_patient_batch | 248 | admission_patient_directory_events |
