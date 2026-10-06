# SIGEH 1.2.10

Esta versión facilita la corrección de datos y la búsqueda en Historial de recibos y Listados de ARS.

- Búsqueda por número de recibo en «Añadir pacientes de la ARS», tanto con el filtro «Recibo» como con «Todos».
- Corrección del nombre desde el recibo o el listado, sincronizada con los expedientes editables. Para pacientes vinculados se actualizan también el paciente y su atención en Admisión. Los listados emitidos conservan su copia histórica.
- Especialidades guardadas y sugeridas al escribir: Emergenciología, Pediatría y Ginecología, junto con General y las especialidades personalizadas. Se recupera la especialidad de Admisión cuando corresponde.
- El buscador del historial recupera el foco después de usar filtros, limpiar o terminar una búsqueda, sin tener que reiniciar el sistema.

Se conservan las reglas de precios, importes, autorizaciones, estados, permisos y los cambios visuales de la versión anterior. Una autorización registrada no marca como listo un paciente al que todavía le faltan datos.

## Instalación y actualización

Abra SIGEH desde `SIGEH.exe` para recibir la actualización automática. El paquete conserva la configuración de conexión y los datos locales existentes, verifica su integridad y ejecuta una comprobación del programa antes de completar la actualización.

Para instalar manualmente, descargue `SIGEH-1.2.10-windows-x64.zip` y conserve la carpeta completa; el ejecutable necesita los archivos de `_internal`. El ZIP público no contiene credenciales ni datos de pacientes. Una instalación nueva requiere configurar su conexión; una instalación ya configurada conserva la suya al actualizarse.

Validación técnica y alcance de las pruebas: [RELEASE_1.2.10_QA.md](RELEASE_1.2.10_QA.md).
