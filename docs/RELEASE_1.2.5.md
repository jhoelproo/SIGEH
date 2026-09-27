# SIGEH 1.2.5

Corrige la omisión del cierre de Facturación cuando una operación administrativa cierra un turno y abre otro. La captura central, la recuperación del PDF y la identificación de atenciones heredadas reconocen esa transición. Corregir el horario conservando el mismo turno no genera un cierre. Los reintentos conservan una única captura y sus cantidades.

Los cierres antiguos que nunca guardaron una captura central necesitan revisión para recuperar sus cifras; esta versión no sustituye ese registro ausente con datos actuales.

Corrige la apertura de una descarga directa cuando la instalación previa fue extraída en una carpeta con versión, como `SIGEH-1.2.4-windows-x64`. El lanzador encuentra la configuración protegida en una carpeta SIGEH versionada conocida y la entrega a la aplicación sin copiar ni escribir la conexión en los archivos descargados.

La búsqueda se limita al Escritorio (incluido OneDrive), la carpeta vecina a la descarga y carpetas cuyo nombre coincide con el formato de versión SIGEH. No recorre carpetas personales arbitrarias.

El ZIP público continúa sin credenciales. Una computadora nueva sin instalación previa ni configuración administrada debe recibir el paquete interno del hospital o la configuración protegida del administrador.

Usa el ZIP de Windows x64. Extráelo junto a la instalación previa y abre `SIGEH.exe`; conserva la carpeta anterior hasta que confirme que la aplicación inicia correctamente.

Validación: 1.764 pruebas y 60 subpruebas aprobadas; build y seis pruebas del ZIP final aprobadas. Una prueba de capacidad sobre base real permanece omitida por requerir activación expresa.

Evidencia y limitaciones: [QA de cierres](https://github.com/jhoelproo/SIGEH/blob/v1.2.5/docs/ADMINISTRATIVE_CLOSURE_1.2.5_QA.md) y [QA de descarga directa](https://github.com/jhoelproo/SIGEH/blob/v1.2.5/docs/DIRECT_DOWNLOAD_1.2.5_QA.md).
