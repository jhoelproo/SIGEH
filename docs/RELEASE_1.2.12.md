# SIGEH 1.2.12

Esta versión corrige las ventanas de Facturación y el historial en pantallas pequeñas: conserva espacio útil para los ítems, las filas y las acciones, y ajusta la distribución al redimensionar. En ventanas estrechas, Catálogo y Recibo se muestran en pestañas.

El buscador de recibos permite borrar con la X y seguir escribiendo después de usar filtros; Limpiar filtros devuelve el foco al buscador. Los listados ARS permiten buscar por número de recibo y corregir nombre, fecha, autorización, especialidad e identificación, con sincronización auditada entre recibo, listados pendientes y Admisión vinculada. Se admiten NO. PÓLIZA para Renacer, NO. AFILIADO para Humano/Primera y NO. CARNET para SEMMA.

Las opciones de especialidad son EMERGENCIOLOGÍA, GINECOLOGÍA y PEDIATRÍA; GENERAL equivale a EMERGENCIOLOGÍA. Se conservan valores históricos desconocidos. Admisión consulta por fecha calendario y documentos, muestra primero las atenciones recientes en Este turno y evita confundir pacientes de días anteriores con duplicados del turno actual. Los reingresos autorizados conservan su origen, motivo e identidad al sincronizar.

Un recibo FACTURADO genera documento final aunque no esté vinculado. La vinculación permite buscar en todo el historial elegible de Admisión por nombre, NSS o cédula. Se conservan los controles de permisos, auditoría, importes, numeración y estados, y se retira del menú la opción de actualizar manualmente la base de Admisión.

La continuidad local incorpora una cola durable de recibos, sincronización idempotente, pausa de consultas por restricciones y respaldos verificados. Un recibo pendiente de sincronización conserva su condición preliminar; la facturación sin conexión requiere el rol autorizado y tarifas previamente guardadas.

El paquete público contiene la misma compilación validada en las pruebas de aceptación. No contiene credenciales ni bases operativas. La actualización conserva la configuración y el historial local existentes. La evidencia funcional y de calidad está en [QA de 1.2.12](QA_1.2.12_20261008.md).
