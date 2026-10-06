# SIGEH 1.2.11

Las búsquedas de pacientes pendientes de Facturación consultaban nuevamente el contenido completo de la cola aunque la información no hubiera cambiado. Esta versión comprueba una huella calculada por PostgreSQL y conserva una sola respuesta en memoria mientras esa huella coincida. Cuando cambia un paciente, la autorización, la especialidad, el orden o la elegibilidad, vuelve a descargar el resultado actualizado.

La medición con la consulta real, 100 pacientes sintéticos y 30 refrescos sin cambios redujo el contenido recibido de 2.764.170 a 93.999 bytes: **96,6% en ese escenario**. Se midió el contenido de las respuestas; no es una medición del tráfico de red facturado ni una promesa de reducción mensual. La primera carga realiza una comprobación adicional.

También se evita crear tres índices de eventos que duplicaban las claves primarias o la restricción de unicidad. La migración de mantenimiento comprueba su equivalencia antes de retirarlos y ajusta autovacuum/analyze en tres tablas con actualizaciones frecuentes. Los registros, restricciones y datos históricos se conservan.

Actualizar las estaciones que compartan la base permite aprovechar la nueva caché y evitar que una versión antigua vuelva a crear los índices redundantes. La actualización empaquetada desde 1.2.10 y un segundo ciclo del actualizador se probaron con configuración protegida e historial sintéticos.

El consumo de transferencia ya acumulado en Supabase sigue contando hasta el reinicio del ciclo. Esta versión ayuda a reducir transferencias futuras; no modifica el plan ni restablece las cuotas del proveedor. [Documentación de Supabase](https://supabase.com/docs/guides/platform/billing-faq#fair-use-policy).

La evidencia de validación del paquete y del código se documenta en [RELEASE_1.2.11_QA.md](RELEASE_1.2.11_QA.md).
