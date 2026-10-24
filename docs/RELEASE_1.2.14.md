# SIGEH 1.2.14

- Honorarios configurables por ARS y especialidad: Emergenciología/General, Pediatría y Ginecología. La exclusión impide añadir y guardar cargos nuevos; conserva los importes históricos.
- Materiales vinculados a medicamentos desde el catálogo administrativo. Se añaden proporcionalmente y se ajustan al cambiar cantidades o retirar el medicamento, conservando los materiales manuales y los recibos históricos.
- Reglas conservadas localmente y actualización de caché por versión, sin descargas repetidas del catálogo.
- Botón para buscar actualizaciones y comprobación al abrir. Se conservan configuración, logo, horarios, usuarios y conexión, incluido el primer salto desde 1.2.13 mediante recuperación durante el control de instalación.
- Acceso local con verificadores PBKDF2 de 600.000 iteraciones y actualización de cachés antiguos. La base central conserva el formato PBKDF2 compatible de 120.000 iteraciones mientras conviven estaciones anteriores; no se convierten contraseñas a texto plano ni se fuerzan restablecimientos.
- Mejoras pendientes de impresión en tamaño carta, disposición de formularios y permisos para corregir la edad de facturación.

La migración es aditiva: incorpora configuración de honorarios y relaciones de materiales, sin modificar precios ni registros clínicos. La tabla nueva tiene RLS y carece de permisos para los roles públicos de la API.

Los informes locales del 22 y 23 de octubre documentan compilaciones anteriores. Para la versión publicada prevalece `RELEASE_1.2.14_QA.md`, en especial su validación de compatibilidad de acceso y actualización desde la versión instalada.
