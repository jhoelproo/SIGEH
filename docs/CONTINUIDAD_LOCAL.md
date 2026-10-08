# Continuidad local de Admisión y Facturación

Los recibos nuevos se conservan primero en una cola SQLite local. Cada comando
tiene un UUID y una huella de su contenido; la confirmación central se registra
en la misma transacción que el recibo. Si el servidor guarda el documento pero
la computadora pierde la respuesta, el siguiente envío recupera ese recibo.

## Trabajo sin conexión

- Admisión conserva su autorización y el plazo de escritura local existentes.
- Facturación permite crear copias preliminares locales a los roles que ya
  pueden facturar sin verificación previa, cuando existe un catálogo guardado.
- El historial local permite consultar y abrir las copias propias del usuario.
- Un recibo local muestra «Pendiente de sincronización» y un identificador local;
  el número definitivo se asigna al confirmar el registro central.
- La edición sin conexión de un recibo central continúa bloqueada.

Los catálogos disponibles se guardan al consultarlos con conexión. Una estación
que nunca descargó una tarifa no puede usar esa tarifa durante una caída.
La información de la cola se almacena fuera de la carpeta de instalación, en
`%LOCALAPPDATA%/HospitalProvincial/FacturacionMedica/local_receipts`.
La cola no conserva contraseñas ni tokens de autenticación.

## Recuperación y sincronización

Con la aplicación abierta, el trabajador revisa la cola pendiente. La
sincronización de Admisión mantiene su cursor incremental: las lecturas previas
no vuelven a descargar el historial completo.

Los errores de red espacian los intentos entre 10 y 300 segundos. Una restricción
del proveedor pausa los intentos durante 15 minutos; esta pausa se comparte con
el guardado de nuevos recibos y la verificación final de su atención vinculada.
Mientras dura la pausa, los documentos nuevos permanecen localmente.

Antes de publicar, se vuelven a comprobar el usuario, sus permisos y, cuando
corresponde, la disponibilidad de la atención vinculada. Una discrepancia de
negocio deja el comando en «Requiere revisión». Los datos se conservan y no se
fuerza su publicación. El historial local muestra ese estado; esta versión no
incorpora edición ni reenvío manual de comandos en revisión.

Los recibos locales de cobro directo también son copias preliminares; su PDF
local no constituye confirmación central de facturación ni acredita un pago.

## Respaldos

Mientras la aplicación está activa, se comprueba periódicamente la existencia
de un respaldo diario válido. Las copias se verifican antes de eliminar las
anteriores y un respaldo corrupto se reemplaza. Los fallos se registran y se
reintentan sin bloquear la atención.

El respaldo de la cola conserva los comandos, sus identificadores, sus estados y
el catálogo local. Sus PDF se pueden regenerar desde los datos del comando.
Admisión conserva sus respaldos independientes, incluidos los eventos pendientes
de envío. La restauración debe realizarse con la aplicación cerrada y preservando
una copia del estado previo.

Los respaldos locales dependen de ese equipo; para cubrir una pérdida del disco
se necesita además una copia en otro dispositivo. Esta implementación no crea
una sincronización de respaldos con servicios externos.

## Validación

Las pruebas de continuidad usan datos ficticios, SQLite temporal y PostgreSQL
en `127.0.0.1:55432`, con bases desechables. Las pérdidas de conexión y de respuesta
se simulan sin desconectar servicios del hospital. No se realiza QA destructivo
ni se cargan pacientes ficticios en producción.

La migración `20261006124609_durable_local_receipt_requests.sql` agrega el UUID,
la huella y el índice único de solicitudes locales. No cambia recibos previos.
El proceso de inicialización contiene la misma preparación para instalaciones
que usan la actualización normal de la aplicación.
