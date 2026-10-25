# SIGEH 1.3.3 — Reparación urgente de impresión

- Admisión vuelve a enviar los PDF a SumatraPDF, el motor utilizado antes del cambio que afectó a las impresiones.
- Las hojas y reimpresiones de Admisión solicitan papel Carta y doble cara por el borde largo, en un solo trabajo. El formulario de Emergenciología conserva sus tres páginas: 1 y 2 en la primera hoja, y 3 en el frente de la segunda.
- Un fallo de envío conserva su detalle en pendientes, aunque Excel termine correctamente. Reintentar mantiene la atención y su número originales.
- Se evita reenviar automáticamente un trabajo fallido por otro motor. Los informes conservan su configuración de caras de la impresora.

No se modifican la composición de los formularios, los recibos, las tarifas ni los permisos. La actualización conserva configuración, logo, horarios, usuarios e historial.

Verificación: `RELEASE_1.3.3_QA.md`. El envío físico en la Canon debe comprobarse en el hospital; este equipo solo tiene impresoras virtuales.
