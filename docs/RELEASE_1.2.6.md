SIGEH 1.2.6 reduce las descargas repetidas del turno: cada refresco consulta identificadores y huellas y recupera los datos completos solamente cuando cambian. El resumen de facturación consulta los campos necesarios sin descargar el JSON clínico completo.

El presupuesto local usa tres estaciones por defecto y conserva las alertas y la operación de atención y facturación. El historial hospitalario permanece disponible. Se incluye la vinculación administrativa de recibos sin vínculo a atenciones heredadas pendientes, con auditoría y protección frente a duplicados.

Validación: 1.835 pruebas y 60 subpruebas de regresión; 56 pruebas adicionales de versión, empaquetado y actualización. Cobertura del código modificado comprobado: 100 % de líneas y ramas. Builds de aplicación y actualizador, hashes del ZIP y smoke tests verificados.

La prueba de refrescos sin cambios redujo el payload un 96,3 %; no representa una reducción mensual garantizada. Actualizar los tres equipos y comparar el consumo del panel de Supabase después del despliegue. No se borraron históricos ni se incluyeron credenciales en el ZIP público.
