# SIGEH v1.1.0 — despliegue y rollback

Este procedimiento actualiza software sobre la producción existente. No crea
baseline, no cambia turno, no reinicia `production_epoch`, no borra SQLite y no
ejecuta retención de eventos durante el despliegue.

## 1. Gate previo obligatorio

No iniciar si falta cualquiera de estas evidencias:

1. La versión actual y su instalador anterior están disponibles.
2. PostgreSQL responde y existe un `OperationalState` ACTIVE con `turn_id`,
   `operational_source_id`, representante y una sola PRIMARY.
3. El conteo central, Historial y ambas estaciones coinciden.
4. La outbox de cada estación no tiene pendientes críticos, o sus pendientes
   están inventariados para validarlos después.
5. Existe un dump PostgreSQL recuperable y una copia de los datos/configuración
   local de cada estación.
6. PRIMARY y al menos una SECONDARY aparecen saludables simultáneamente.

Si la condición 6 no se cumple, se puede validar el resto del candidato, pero
no aprobar la release.

## 2. Respaldo PostgreSQL

En una consola que tenga `pg_dump`, configurar la URL sin escribirla en logs:

```powershell
$SigehDatabaseUrl = Read-Host "DATABASE_URL de SIGEH"
$SigehBackupStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$SigehBackupFile = "C:\SIGEH_Backups\sigeh_$SigehBackupStamp.dump"
New-Item -ItemType Directory -Force -Path "C:\SIGEH_Backups" | Out-Null
pg_dump $SigehDatabaseUrl --format=custom --no-owner --no-acl --file $SigehBackupFile
pg_restore --list $SigehBackupFile | Out-Null
Get-FileHash -Algorithm SHA256 -LiteralPath $SigehBackupFile
```

Verificar restauración en una base descartable antes de autorizar cualquier
limpieza. `pg_restore --list` comprueba estructura del archivo, pero no sustituye
una restauración de prueba.

Registrar: timestamp, hash, tamaño, revisión operacional, `turn_id`, conteo,
filas de proyección, recibos, tombstones y máximos de secuencia.

## 3. Respaldo de cada estación Windows

Cerrar SIGEH en esa estación. Ajustar únicamente la ruta de instalación real:

```powershell
$SigehInstallDir = "C:\SIGEH"
$SigehLocalStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$SigehLocalBackup = "C:\SIGEH_Backups\station_$env:COMPUTERNAME`_$SigehLocalStamp"
New-Item -ItemType Directory -Force -Path $SigehLocalBackup | Out-Null
Copy-Item -Recurse -Force -LiteralPath "$SigehInstallDir\_internal\data" -Destination $SigehLocalBackup
Copy-Item -Recurse -Force -LiteralPath "$SigehInstallDir\recibos" -Destination $SigehLocalBackup -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force -LiteralPath "$SigehInstallDir\reportes" -Destination $SigehLocalBackup -ErrorAction SilentlyContinue
Copy-Item -Recurse -Force -LiteralPath "$SigehInstallDir\respaldos" -Destination $SigehLocalBackup -ErrorAction SilentlyContinue
Get-ChildItem -File -Recurse -LiteralPath $SigehLocalBackup | Get-FileHash -Algorithm SHA256
```

Conservar también `database_url.protected`, `database_url.bundle`, configuración,
logs y el paquete anterior. No copiar una SQLite abierta.

## 4. Migración aditiva

Aplicar una sola vez, mediante el mecanismo de migraciones de Supabase, el
contenido exacto de:

`migrations/20260830_billing_bypass_authorization_review.sql`

Después verificar:

```sql
SELECT column_name,data_type,is_nullable,column_default
FROM information_schema.columns
WHERE table_schema='public' AND table_name='recibos'
  AND column_name IN ('review_status','review_reason')
ORDER BY column_name;

SELECT review_status,COUNT(*)
FROM recibos
GROUP BY review_status
ORDER BY review_status;
```

La migración es compatible con v1.0.9: esa versión ignora las columnas nuevas.
No retirar columnas durante un rollback de software.

## 5. Instalación controlada

1. No hacer relevo, transferencia PRIMARY ni cierre de turno durante la ventana.
2. Actualizar primero la PRIMARY actual con el canal oficial y checksum SHA-256.
3. Abrir, iniciar sesión y confirmar que adoptó exactamente el mismo turno,
   representante, fuente operacional y conteo.
4. Actualizar una SECONDARY; repetir la misma verificación.
5. Actualizar las demás estaciones una por una.
6. No borrar `_internal/data`; el actualizador conserva y verifica sus hashes,
   además de `recibos`, `reportes`, `respaldos` y configuración protegida.

## 6. Pruebas hospitalarias antes de aprobar

1. Repetir refresh, heartbeat, Historial, Excel, PDF y Reportes sin relevo; el
   conteo debe permanecer estable.
2. Comparar Historial y conteo en ambas PC con PostgreSQL.
3. Registrar un paciente online y comprobar propagación a la otra estación.
4. Registrar un paciente offline, reconectar y confirmar `CENTRAL_CONFIRMED`.
5. Transferir PRIMARY A → B: ambas sesiones siguen conectadas; turno,
   generación, representante y conteo no cambian.
6. Intentar dos transferencias con la misma revisión: una debe ganar y la otra
   debe exigir refresh.
7. Hacer relevo normal a una persona distinta: representante, `turn_id` y
   generación cambian inmediatamente; mismo usuario debe ser rechazado.
8. Probar bypass con autorización válida, ausente, alfanumérica y demasiado
   corta; comprobar documento, revisión e historial de auditoría.

## 7. Rollback de software

Ante un fallo crítico, cerrar SIGEH y restaurar el paquete v1.0.9 mediante el
respaldo creado por el actualizador. Conservar la SQLite y configuración más
recientes; no reemplazarlas por una copia anterior salvo corrupción demostrada.
El actualizador revierte automáticamente si falla su health check.

Las columnas `review_status` y `review_reason` permanecen: son aditivas y v1.0.9
las ignora. No ejecutar `DROP COLUMN`, no revertir recibos y no restaurar todo
PostgreSQL por un simple rollback de ejecutable.

## 8. Rollback de datos o limpieza

No se realizó limpieza en este candidato. Si en el futuro se autoriza, debe
existir otro checkpoint con backup restaurado, dry run, archivo verificado,
conteos antes/después y referencia del backup pasada al servicio de retención.
