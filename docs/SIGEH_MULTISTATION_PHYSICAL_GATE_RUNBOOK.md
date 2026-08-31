# SIGEH — gates físicos multestación

Este procedimiento requiere **PC HOSPITAL**, **PC SECONDARY** y PostgreSQL
central. No contiene un atajo automatizado que pueda sustituir las dos PCs.

## Preparación

1. Confirmar backup recuperable y ventana controlada de prueba.
2. Iniciar sesión en ambas PCs sobre la misma sesión operacional.
3. Anotar el total visible inicial `N > 0` y no realizar relevo hasta el gate D.
4. Crear una carpeta de evidencia fuera de la instalación y copiar allí este
   archivo y `qa/collect_multistation_evidence.py`.
5. No escribir nombres, diagnósticos, NSS, cédulas ni autorizaciones en la
   evidencia. El recolector solo guarda identidad operacional y UUID globales.

## A. Historial central

En cada PC, con SIGEH cerrado solo durante la lectura SQLite, ejecutar:

```powershell
python qa/collect_multistation_evidence.py `
  --local-db "C:\RUTA\SIGEH\_internal\data\pacientes.db" `
  --bundle-root "C:\RUTA\SIGEH\_internal" `
  --station HOSPITAL `
  --output "C:\SIGEH_EVIDENCIA\hospital-pre.json"
```

Repetir con `--station SECONDARY`. PASS exige simultáneamente:

- `identity_matches_active=true` en ambos archivos;
- mismo `operational_source_id`, `turn_id`, `generation`;
- `dataset_matches_central=true` en ambos;
- igualdad exacta de `global_attention_ids`, no solo de `count`;
- `active_primary_count=1`.

## B. Atención controlada

1. Crear una única atención desde Hospital y copiar su
   `global_attention_id` desde el log `ATTENTION_SYNC_ENQUEUED`.
2. Esperar el ACK normal, sin forzar seed, merge ni reconstrucción.
3. Ejecutar el recolector en ambas PCs agregando:

```text
--trace-global-attention-id UUID_CONTROLADO
```

4. Conservar `hospital-post.json` y `secondary-post.json`.

PASS exige el mismo UUID en atención local, outbox, evento central, proyección
y ambos historiales. `projection.is_deleted=false`; el outbox debe terminar
`SENT/ACKED` según el vocabulario instalado, sin un segundo UUID.

## C. Estabilidad del conteo

Durante al menos 30 minutos, registrar cada 60 segundos:

- timestamp, device ID, source, turn, generation y revision;
- total visible anterior/nuevo;
- `TURN_SUMMARY_DATASET_RESULT`, fuente y motivo;
- conteo central/local/pendiente del JSON de evidencia.

Entre capturas repetir refresh, heartbeat, abrir/cerrar Historial, buscar,
Mostrar todo, Excel, Reportes y regreso a Emergencias. Cualquier `N→0` con la
misma identidad es FAIL inmediato. Simular por separado timeout central y
réplica no disponible; la GUI debe conservar el último snapshot válido.

## D. Relevo normal

Capturar evidencia antes. Intentar primero `A→A`: debe ser NO-OP absoluto.
Después confirmar `A→B`. Inmediatamente después del COMMIT, antes del siguiente
heartbeat, capturar pantalla y JSON. PASS exige cambio conjunto de turn,
representante, generación, intervalo, rango y GUI. No se acepta estado mixto.

## E. Transferencia PRIMARY

Con ambas estaciones saludables, capturar JSON antes y después. Un Admin
transfiere PRIMARY A→B. PASS exige:

- PRIMARY activo central exactamente 1 antes y después;
- A adopta SECONDARY y B adopta PRIMARY;
- source, turn, generación, representante y conteo permanecen idénticos;
- permisos PRIMARY-only se mueven en ambas GUIs.

## Evidencia obligatoria

- `hospital-pre.json`, `secondary-pre.json`;
- `hospital-post.json`, `secondary-post.json`;
- captura antes/después de relevo y transferencia PRIMARY;
- logs filtrados por `TURN_SUMMARY_`, `TURN_HANDOFF_`,
  `PRIMARY_TRANSFER_`, `ATTENTION_SYNC_`;
- resultado firmado por operador de cada PC, hora local y UTC;
- ninguna información clínica o identificadora del paciente.

Si una de las dos PCs no está disponible o saludable, el estado correcto es
`BLOQUEADO POR ENTORNO FÍSICO`, nunca PASS.
