# SIGEH 1.0.5 — historial central definitivo de Admisión

Esta entrega consolida PostgreSQL como fuente autoritativa del historial de
Admisión. El SQLite de cada estación queda limitado a réplica local y trabajo
offline; no se comparte entre equipos y no reemplaza el historial central.

## Cambios principales

- Historial, resumen del turno, Excel y reportes consultan la misma proyección
  central cuando hay conexión.
- Los registros locales solo se agregan provisionalmente cuando están
  `PENDING` o `RETRY`; al sincronizarse, prevalece la identidad global central.
- El baseline inicial `SEED` exige exactamente una sesión operacional central
  activa y asigna a todo el histórico el mismo `operational_source_id` y
  `turn_id` vigente.
- Antes de analizar una fuente SQLite se crea una copia verificada mediante la
  API de backup de SQLite, `PRAGMA quick_check` y SHA-256. La copia se vuelve a
  verificar inmediatamente antes de aplicar.
- El baseline inicial es único e idempotente. Una fuente o huella diferente se
  rechaza; `SEED` nunca sobrescribe un registro central diferente.
- `MERGE` queda reservado para recuperación manual. Conserva tombstones y
  revisiones centrales más nuevas.
- Las rectificaciones exigen motivo, actor, fecha, valores anterior/nuevo y
  campos cambiados. Administrador y Auxiliar pueden editar/anular; los roles de
  auditoría permanecen en solo lectura para Admisión.
- Una atención anulada desaparece del historial activo. Su recibo vinculado se
  mueve a la papelera; restaurar el recibo no restaura la atención original.
- Se eliminó el reinicio automático del historial introducido en 1.0.4.

## Despliegue obligatorio del baseline inicial

1. Cierre SIGEH en todas las estaciones.
2. Respalde la base PostgreSQL y copie el `pacientes.db` productivo de la
   estación PRIMARY a un medio seguro.
3. Instale SIGEH 1.0.5 únicamente en la estación PRIMARY y ejecútelo como un
   usuario Administrador.
4. Confirme que exista exactamente un turno central `ACTIVE`, con la estación
   PRIMARY y el representante operacional correctos. No cambie turno ni
   representante durante el análisis/aplicación.
5. Abra **Opciones avanzadas → Actualizar base de Admisión**. Seleccione el
   `pacientes.db` productivo de la PRIMARY, elija `SEED` y pulse **Analizar**.
6. Antes de aplicar, compruebe en la vista previa: ruta y SHA-256 de la copia,
   conteos de pacientes/atenciones, fuente de origen, turno central y cero
   conflictos inesperados.
7. Pulse **Aplicar** una sola vez. Espere el estado `COMPLETED`; no cierre SIGEH
   ni cambie el turno mientras el lote esté `ANALYZING` o `APPLYING`.
8. Valide en PostgreSQL las consultas del siguiente apartado y compare el total
   central activo con el total esperado del SQLite respaldado.
9. En SIGEH verifique que Historial, Resumen, Excel y Reportes muestren el mismo
   conjunto del turno. Pruebe una rectificación y una anulación controladas.
10. Solo después instale 1.0.5 en las estaciones SECONDARY. No ejecute `SEED`
    desde una secundaria. Use `MERGE` únicamente ante una recuperación manual
    documentada.

## Verificación SQL posterior

Ejecute estas consultas con una cuenta de solo lectura o dentro de una sesión
administrativa controlada:

```sql
SELECT central_seed_id, status, imported_records, seed_completed_at,
       legacy_source_instance_id, seed_source_fingerprint,
       operational_source_id, turn_id
FROM admission_central_seeds
WHERE seed_kind = 'INITIAL_BASELINE';

SELECT import_batch_id, mode, status, processed_records, total_records,
       backup_path, backup_sha256, completed_at
FROM admission_import_batches
ORDER BY imported_at DESC
LIMIT 5;

SELECT COUNT(*) AS active_attentions
FROM admission_attention_projection
WHERE COALESCE(is_deleted, FALSE) = FALSE
  AND UPPER(BTRIM(COALESCE(source_status, 'ACTIVA')))
      IN ('ACTIVA', 'PENDIENTE');

SELECT operational_source_id, turn_id, COUNT(*) AS records
FROM admission_attention_projection
WHERE reconciliation_status = 'INITIAL_BASELINE'
GROUP BY operational_source_id, turn_id;

SELECT COUNT(*) AS missing_global_identity
FROM admission_attention_projection
WHERE global_attention_id IS NULL;

SELECT global_attention_id, COUNT(*) AS duplicates
FROM admission_attention_projection
GROUP BY global_attention_id
HAVING COUNT(*) > 1;
```

Resultado esperado: un solo baseline `COMPLETED`, un lote `SEED` completado, una
sola combinación fuente/turno para las filas `INITIAL_BASELINE`, cero identidades
globales faltantes y cero duplicados.

## Instalación

La descarga es ONEDIR. Extraiga la carpeta completa en una ubicación nueva y
ejecute `SIGEH.exe`. No copie bases `.db`, `.sqlite` o `.sqlite3` dentro de la
distribución. El actualizador preserva `_internal/data` y la configuración
privada de cada estación.
