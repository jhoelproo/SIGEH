# SIGEH v1.0.10 — instalación controlada y rollback

`v1.0.10` es una prerelease de prueba. Este procedimiento no autoriza un
despliegue masivo ni crea una nueva historia operacional.

## Antes de instalar

1. Cerrar SIGEH en la PC de prueba y conservar el instalador anterior.
2. Respaldar `_internal/data`, `recibos`, `reportes`, `respaldos`, configuración
   local y cualquier `database_url.protected` o `database_url.bundle` existente.
3. Registrar `operational_source_id`, `turn_id`, representante, generación,
   PRIMARY y conteo antes de actualizar.
4. Verificar el SHA-256 publicado del ZIP.
5. No ejecutar SEED, MERGE, cierre/relevo, limpieza ni reconstrucción de datos.

El ZIP público no contiene credenciales ni bases operacionales. El updater
preserva los archivos privados desde la instalación existente.

## Instalación controlada

1. Extraer el ZIP en una carpeta temporal limpia.
2. Ejecutar `SIGEH_Updater.exe` apuntando a la instalación de prueba existente.
3. Confirmar que la versión visible es `1.0.10`.
4. Confirmar que se adoptaron exactamente el mismo turno, fuente operacional,
   representante y conteo.
5. Probar Historial, Facturación, PDF, Excel y Reportes.
6. No probar transferencia PRIMARY ni relevo en producción sin dos estaciones
   saludables y una ventana autorizada.

## Migración aditiva

La migración `20260830_billing_bypass_authorization_review.sql` agrega columnas
idempotentes en `recibos`. No borra ni reasigna pacientes, atenciones, recibos,
UUID, turnos, tombstones, outbox ni SQLite. La migración ya aplicada no debe
revertirse mediante `DROP COLUMN` durante un rollback de software.

## Rollback

Si falla un health check, el updater restaura automáticamente los ejecutables y
archivos sustituidos. Para rollback manual:

1. Cerrar SIGEH.
2. Restaurar el paquete anterior sobre una copia de la instalación.
3. Conservar la SQLite y configuración más recientes; no sustituir datos por
   una copia antigua salvo corrupción demostrada.
4. Reabrir y verificar identidad operacional, conteo, Historial, recibos y
   pendientes de sincronización.
5. Registrar el fallo y no continuar con otras estaciones.

## Gates físicos pendientes

La igualdad Hospital/PostgreSQL/Secondary, la atención controlada, el relevo y
la transferencia PRIMARY requieren dos PCs reales. Hasta ejecutarlos, el estado
correcto es `NO PROBADO / BLOQUEADO POR ENTORNO FÍSICO`.
