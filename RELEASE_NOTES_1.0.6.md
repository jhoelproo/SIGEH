# SIGEH 1.0.6 — Reportes Estadísticos operacionales

SIGEH 1.0.6 incorpora el nuevo sistema de Reportes Estadísticos de Admisión.
La actualización consume el historial central existente: no importa pacientes,
no crea otro baseline, no cambia el turno y no modifica la autoridad
PRIMARY/SECONDARY.

## Cambios principales

- Dataset canónico único para tarjetas, vista previa, PDF y Excel.
- Lectura online desde `admission_attention_projection` y uso de la réplica
  SQLite únicamente cuando la estación ya está en modo offline.
- Filtros por turno operacional actual, anterior o todos los turnos.
- Períodos diario, semanal, mensual, anual y rango, siempre delimitados a las
  8:00 AM del primer día y las 8:00 AM posteriores al último día.
- Filtros por especialidad y cobertura.
- Modos ARS **TODAS**, **INCLUIR** y **EXCLUIR**, con selección múltiple y
  búsqueda sin distinción de mayúsculas ni acentos.
- Exclusión defensiva de atenciones anuladas o eliminadas.
- Tarjetas de total, asegurados, sin seguro, Medicina General, Pediatría y
  Ginecología.
- Vista previa con los conteos del mismo dataset que alimenta las exportaciones.
- PDF con resumen, filtros, conteos por ARS y por especialidad.
- Excel con exactamente dos hojas:
  - **LISTADO DE PACIENTES**, construido por el mismo generador del listado
    operacional oficial.
  - **RESUMEN ESTADÍSTICO**, calculado desde las mismas filas de la primera
    hoja.
- Validación interna que bloquea la exportación si el total del resumen no
  coincide con la cantidad de pacientes del listado.

## Continuidad de producción

Esta versión no incluye migraciones de datos ni cambios destructivos de
esquema. La versión de software cambia a `1.0.6`, pero permanecen intactos:

- el `production_epoch`;
- el historial central ya cargado;
- el turno operacional vigente;
- las bases SQLite de cada estación;
- las colas de sincronización pendientes;
- el rol PRIMARY/SECONDARY;
- el listado Excel operacional existente.

## Actualización segura en el hospital

1. Verifique que ninguna estación esté registrando un paciente en ese instante.
2. Cierre SIGEH en la estación que va a actualizar. No cierre el turno desde la
   aplicación.
3. Conserve una copia de seguridad del PostgreSQL y de la carpeta de datos
   persistente de la estación. No mueva esos datos dentro de la descarga.
4. Descargue `SIGEH-1.0.6-windows-x64.zip` y compruebe su archivo `.sha256`.
5. Extraiga la distribución completa en una carpeta nueva; no mezcle archivos
   ejecutables de versiones distintas.
6. Ejecute `SIGEH.exe`. El actualizador conserva `_internal/data`, la
   configuración privada y cualquier sincronización pendiente.
7. Confirme en el inicio que la versión visible es **SIGEH v1.0.6**.
8. Confirme que el mismo turno y representante sigan activos y que Historial
   muestre los pacientes existentes.
9. Abra **Reporte estadístico**, genere el turno actual con ARS, especialidad y
   cobertura en **TODAS**, y compare el total con Historial y el listado
   operacional.
10. Exporte PDF y Excel. Compruebe que el Excel tenga las dos hojas indicadas y
    que el total de **RESUMEN ESTADÍSTICO** sea igual al número de filas de
    **LISTADO DE PACIENTES**.
11. Actualice las estaciones restantes una por una. No ejecute `SEED`, `MERGE`,
    cambio de turno ni reconstrucción del historial durante la actualización.

Ante cualquier diferencia de conteo, conserve la instalación anterior, los
logs y los respaldos; no reimporte pacientes para intentar corregirla.
