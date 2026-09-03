# SIGEH v1.1.2 — cierre documental de turno

## Reportes de Admisión

- El relevo confirmado conserva antes del `COMMIT` la identidad inmutable del
  turno saliente: fuente operacional, `turn_id`, generación, revisión,
  representante e intervalo real.
- El PDF estadístico y el listado Excel operacional se generan desde una sola
  lectura canónica de PostgreSQL para esa identidad saliente.
- Las preferencias existentes permiten activar o desactivar de forma
  independiente el PDF y el Excel, así como su apertura e impresión.
- Ambos archivos usan nombres deterministas ligados al `transition_id`; un
  reintento solo completa archivos pendientes y nunca repite el relevo.
- Un fallo de apertura, impresora o espejo SQLite conserva los documentos ya
  generados y no modifica el nuevo turno.

## Reporte de Facturación

- El evento de cierre de Facturación se registra como efecto post-commit
  independiente del guardado del espejo local de Admisión.
- El PDF de cierre de Facturación conserva su instantánea auditable y queda
  marcado como generado aunque Windows no pueda abrirlo o imprimirlo en ese
  momento.
- La generación continúa siendo exclusiva de la estación PRIMARY online y
  deduplicada por la identidad del cierre.

## Continuidad operacional

- La actualización no ejecuta SEED, MERGE, reset de historial, cambio de
  PRIMARY, cambio adicional de representante ni recreación de bases de datos.
- Se conserva íntegro el contrato idempotente de relevo de v1.1.1: replay
  semántico, detección de colisión, recuperación post-commit y seguridad ante
  solicitudes concurrentes.
- No se agregan migraciones de esquema en esta versión.

## Alcance de validación

- Incluye pruebas automatizadas de snapshot único, identidad saliente,
  preferencias, idempotencia, concurrencia, errores de generación/apertura y
  reporte de Facturación.
- Las pruebas físicas simultáneas en dos computadoras no se presentan como
  ejecutadas; requieren validación posterior en PRIMARY y SECONDARY reales.
