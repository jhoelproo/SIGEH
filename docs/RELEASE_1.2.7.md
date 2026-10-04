SIGEH 1.2.7 conserva la configuración de conexión durante las actualizaciones y guarda cifrada la conexión recuperada de una instalación anterior. La escritura atómica protege el archivo anterior ante errores de disco o interrupciones durante su reemplazo.

- Conservación de configuración en la raíz y en `_internal`, incluyendo instalaciones configuradas mediante el reparador.
- Corrección administrativa de ARS desde Facturación, con actualización de Admisión cuando el recibo está vinculado y recálculo de precios.
- Inclusión de recibos manuales facturados en los listados ARS según la fecha de servicio, evitando duplicados en expedientes activos.
- Historial separado para extranjeros y no asegurados, con estados pagado, pendiente y exonerado según la cobertura, y documentos que distinguen cobros de deudas.

Las instalaciones existentes mantienen su configuración local. Una instalación nueva sin configuración necesita provisionarla. El ZIP público no incluye credenciales ni bases de datos hospitalarias.

Pendiente de diagnóstico: los errores repetidos de recuperación de cierres que aparecen en el log del hospital y el fallo genérico del lanzador posterior a la actualización. Esta versión no se presenta como una corrección confirmada de esos incidentes.
