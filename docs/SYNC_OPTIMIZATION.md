# Sincronización y presupuesto de transferencia

Rama de evaluación: `codex/sync-transfer-budget`. Base: SIGEH 1.2.2 (`f1f5d83`). No desplegada.

## Auditoría

El [inventario de consultas](SYNC_QUERY_INVENTORY.md) registra las funciones que acceden a las tres tablas, incluidas las escrituras. Los disparadores operativos son:

| Disparador | Camino | Datos recuperados |
|---|---|---|
| Inicio de sesión / apertura de Admisión | `_HybridCoordinator.start`, reanexión, `synchronize` | Estado operativo; primer ciclo incremental; réplica del turno activo cuando la caché es nueva |
| Temporizador | `_schedule_poll`, `synchronize`, `synchronize_once` | Estado/lease, cabecera `event_window`, eventos posteriores al cursor; outbox local pendiente |
| Directorio en segundo plano | `_pull_patient_directory_if_due`, `pull_incremental` | Cabeceras incrementales y fichas de identidades ya presentes en la caché |
| Cambio de turno | Transición central, refresco de identidad, `reconcile_current_turn` | Páginas del nuevo turno; cierre usa las consultas de facturación existentes |
| Historial y búsqueda | `_legacy_projection_readthrough`, `load_admission_history_batch`, `get_operational_candidates` | Páginas solicitadas, con filtros centrales y permisos existentes |
| Abrir/editar/anular una atención | `get_attention_by_global_id`, `build_document`, procesamiento central | Una identidad y su información necesaria; se mantienen las comprobaciones de identidad |
| Reportes solicitados / cierres | Dataset del turno, consultas estadísticas, captura de cierre | Datos del período solicitado. No se limita el contenido de un reporte para ahorrar tráfico |
| Reparaciones/importación/mantenimiento | Backfill, importador, análisis de capacidad, migraciones | Caminos administrativos; no se convierten en descargas periódicas |

Las consultas de `db_init` y migraciones se revisaron como preparación del esquema, no como polling. Se conservan las consultas de existencia, revisión y bloqueos de escrituras. No se modificaron permisos, RLS, migraciones centrales, tablas ni registros de producción.

## Cambios

- Los cursores SQLite existentes se conservan. Los lotes de atenciones guardan cambios, confirmación de eventos y cursor en la misma transacción. Los lotes de pacientes también mantienen su cursor si el guardado falla.
- Una réplica nueva de atenciones hidrata primero todas las páginas del turno activo y solo entonces confirma el checkpoint observado antes de la lectura. Las atenciones históricas siguen en PostgreSQL y se recuperan bajo demanda. Este checkpoint describe una **caché operativa**, no una copia local completa del hospital.
- La reconciliación ya no se detiene silenciosamente en 500 atenciones. Una reconciliación forzada fallida invalida la confirmación anterior y no permite adelantar el cursor usando una confirmación vieja.
- El directorio en ejecución normal usa `cache_only=True`: con un cursor nuevo o vencido actualiza las fichas locales por identidad, en grupos de 100. En ciclos normales consulta hasta 500 cabeceras y descarga solamente las fichas locales afectadas. Un paciente desconocido se busca centralmente al necesitarlo. El bootstrap completo explícito sigue disponible para herramientas de mantenimiento.
- En un historial sin pendientes locales se pide únicamente `LIMIT página OFFSET posición`, no `LIMIT página+posición OFFSET 0`. Si hay pendientes sin sincronizar se conserva por ahora la fusión anterior para no ocultarlos ni desplazar incorrectamente resultados. Este caso sigue teniendo un costo mayor y debe medirse en el piloto.
- La hidratación de historial conserva el contenido necesario para que los identificadores locales sigan siendo seguros. Se elimina el segundo payload cuando la proyección ya contiene el primero. No se elimina a ciegas `p.*` de consultas que reconstruyen fichas. Las consultas de cabeceras del directorio y el flujo de eventos usan columnas explícitas.
- El temporizador no encola un ciclo extra cuando encuentra otro en curso. Las solicitudes explícitas siguen pendientes para su ejecución. `start` es idempotente. El polling inactivo aumenta de 10 a 20 y hasta 30 segundos; actividad y cambios de estado lo devuelven a 10 segundos. El heartbeat y las reglas de lease siguen vigentes.

## Medición y presupuesto

`transfer_budget.py` registra solicitudes, respuestas, filas y tamaños estimados de los resultados leídos mediante `PostgresWrapper.execute`. Guarda contadores en `%LOCALAPPDATA%/SIGEH/telemetry/transfer.sqlite`. No guarda SQL, parámetros, contenido clínico, documentos ni credenciales. La identidad de estación es un UUID aleatorio local.

**Es una estimación de payload de aplicación, no una medición de egress facturado.** No incluye TLS/protocolo, resultados que el cliente no lee, llamadas directas al driver fuera del wrapper, otros programas, respaldos, ni servicios ajenos al cliente. Debe contrastarse con Shared Pooler Egress en Supabase. No garantiza que el proyecto permanezca debajo de 5 GB.

Configuración local opcional `%LOCALAPPDATA%/SIGEH/telemetry/budget.json`:

```json
{"quota_bytes": 5000000000, "stations": 4, "start_day": 1}
```

`stations: 4` es un **ejemplo**, no un dato confirmado del hospital. Se reparte la cuota entre las estaciones configuradas. Sin configuración se mide contra 5 GB para una estación; no debe interpretarse como presupuesto global correctamente repartido. El día de corte admite 1 a 31 y se ajusta al último día de meses cortos. Falta confirmar la cantidad real de estaciones y el inicio del ciclo.

Las alertas locales aparecen al 50%, 70% y 85%, en el registro y en la barra de estado de Admisión. Solo el refresco de la caché del directorio se espacia (30/60/120/300 segundos). Las búsquedas explícitas, escrituras, facturación y sincronización de atenciones no se suspenden por presupuesto. Una falla del archivo de métricas tampoco bloquea operaciones.

Exportación sin nube:

```powershell
python transfer_usage.py --output estacion-a.json
python transfer_usage.py --combine estacion-a.json estacion-b.json --output conjunto.json
```

El ejecutable de evaluación permite exportar sin Python con `CALCULOS_QT.exe --export-transfer-usage estacion-a.json`; este comando no inicia sesión ni consulta la nube.

La combinación usa la exportación más reciente por estación y rechaza ciclos distintos. No necesita cargar documentos ni telemetría a Supabase. Una estación que no entrega su exportación no queda contabilizada; los agregados no sustituyen el panel del proveedor.

## Comparación de laboratorio

Se ejecutó el método real de historial de la base `f1f5d83` y el método modificado contra la misma conexión simulada: 1.000 registros sintéticos, 20 páginas de 50, cuatro estaciones, sin pendientes locales. Ambos devolvieron las mismas 4.000 filas visibles.

| Variante | Filas transferidas | Payload serializado recibido |
|---|---:|---:|
| 1.2.2 | 42.000 | 51.289.600 bytes |
| Optimizada | 4.000 | 4.886.240 bytes |

La reducción observada es aproximadamente 90,5% **para ese escenario de paginación**, no para todo el consumo mensual. Evidencia local: `output/sync-pagination-comparison.json`.

## Puerta de despliegue

No publicar hasta comparar una jornada normal con las estaciones reales. Recoger las exportaciones al inicio y final de la jornada y el consumo del proveedor; anotar número de atenciones, búsquedas, turnos, reportes, respaldos y cortes de conexión para que las cargas sean comparables.

Verificar en el piloto: búsquedas históricas y paginación; edición/anulación con identidad correcta; facturación actual/heredada/histórica; cambio de turno; cierre e impresión; desconexión, reinicio y reenvío del outbox sin duplicados; ausencia de avance del cursor ante fallos; alertas y operaciones críticas por encima del presupuesto.

Conservar el paquete 1.2.2 para volver al software anterior si el piloto falla. No borrar las bases locales ni retroceder cursores manualmente. Las cachés parciales no se convierten en copias completas al volver a un cliente anterior; validar el comportamiento offline antes de considerar completada una reversión.

Referencia: [Supabase: Manage Egress usage](https://supabase.com/docs/guides/platform/manage-your-usage/egress). Shared Pooler Egress corresponde a datos que salen por Supavisor; la cuota es compartida entre servicios y el consumo pasado no se reduce con estos cambios.
