# SIGEH 1.3.4 — Temas y diagnósticos compartidos

- El cambio claro/oscuro evita aplicar dos veces el tema global y agrupa la actualización de estilos e iconos. El desplegable de diagnóstico usa colores explícitos del tema.
- El administrador puede corregir y retirar diagnósticos sin que reaparezcan desde el historial. Una corrección conserva la equivalencia del nombre anterior; los recibos históricos permanecen intactos.
- La configuración confirmada genera una señal PostgreSQL al terminar la transacción. Las estaciones actualizan su copia desde el servidor; se conserva la recuperación por cambios y la copia local.
- Las asociaciones de recomendaciones permiten seleccionar el catálogo ARS. Laboratorios e imágenes se validan contra la aseguradora; se conservan las asociaciones anteriores.
- Nuevo permiso individual «Guardar preliminares sin autorización», con los valores de rol anteriores conservados. Permitir o denegar prevalece sobre el rol, también en guardado local y central. No convierte un preliminar en recibo completo.
- Conserva la reparación de impresión de 1.3.3. No cambia importes, conceptos, formularios ni numeración.

Configuración, logo, nombre, horarios, usuarios e historial se conservan al actualizar. Verificación: RELEASE_1.3.4_QA.md. La salida física Canon de 1.3.3 sigue pendiente de confirmación en el hospital.
