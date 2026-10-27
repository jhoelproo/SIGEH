# SIGEH 1.3.5

Actualizador con descargas reanudables y paquetes incrementales opcionales.

- Descarga solo archivos modificados cuando existe un incremental compatible.
- Conserva la descarga parcial al perder conexión; el siguiente intento continúa mediante HTTP Range. Si el servidor ignora Range, reinicia el archivo sin concatenar contenido repetido.
- Verifica SHA-256 del paquete y de cada archivo reconstruido. Un runtime local distinto o incompleto requiere el paquete completo.
- Descarga y prepara los archivos en un hilo de trabajo. Muestra tamaño, velocidad y tiempo restante, con eventos de progreso limitados a diez por segundo.
- Mantiene el instalador externo, respaldo, preservación de configuración y datos, health check y reversión. No cambia Admisión, Facturación, impresión ni sincronización clínica.

## Compatibilidad

La versión 1.3.4 y anteriores no reconocen incrementales ni conservan descargas interrumpidas. Deben recibir una vez el paquete completo que incorpora este cliente. A partir de 1.3.5, las siguientes publicaciones pueden usar incrementales. Se mantiene siempre el ZIP completo para instalaciones nuevas, saltos de versión y recuperación.

El incremental de ensayo 1.3.4 → 1.3.5 permite validar la reconstrucción y la compatibilidad del instalador anterior. No puede seleccionarlo automáticamente el cliente antiguo. No se ha publicado esta versión en GitHub.

## Empaquetado de siguientes versiones

Conservar el ZIP completo publicado de la versión base y verificar su checksum antes de usarlo. Generar el release público con `release_packaging.py --dist <distribución> --updater <updater.exe> --output <salida> --version <nueva> --base-archive <ZIP completo anterior>`.

Publicar los cinco archivos devueltos: ZIP completo, checksum, manifest, índice de archivos e incremental. El manifest vincula el incremental a su versión base y SHA-256. Si no se proporciona una base, se genera un release completo compatible; nunca se exige un incremental a clientes antiguos.

La creación de incrementales está prohibida para paquetes internos con credenciales. Los valores predeterminados de archivos personalizables se incluyen incluso si no cambiaron; el instalador conserva después la personalización existente. Esto evita que un logo personalizado obligue a descargar todo el runtime.

Un corte de red conserva la descarga parcial y abre la versión instalada. No inicia automáticamente una descarga completa por el mero hecho de perder conexión. Un incremental retirado (404/410) o incompatible sí utiliza el paquete completo.

## Validación

Detalle, comandos, métricas, evidencias y limitaciones en [RELEASE_1.3.5_QA.md](RELEASE_1.3.5_QA.md).
