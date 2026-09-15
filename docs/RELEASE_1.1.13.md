# SIGEH 1.1.13 — inicio confiable desde descarga directa

- El lanzador reconoce la configuración central conservada en `SIGEH/_internal`, ubicación usada por la instalación del hospital.
- Si una descarga se extrae en una carpeta nueva, el lanzador recupera automáticamente la configuración desde una instalación SIGEH anterior ubicada en el Escritorio local o de OneDrive.
- La búsqueda está limitada a carpetas conocidas de SIGEH; no recorre archivos personales ni acepta configuraciones de otras aplicaciones.
- Si no existe ninguna instalación configurada, se conserva el bloqueo seguro y el registro `CONFIGURATION_MISSING`; no se publican credenciales en el repositorio público.
- Incluye íntegramente las correcciones de edición de recibos de la versión 1.1.12.

La función de extranjeros y la corrección individual de fecha continúan fuera de esta entrega.
