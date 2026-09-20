# SIGEH 1.2.0

## Admisión y pacientes

- El buscador de «Editar paciente» recibe el teclado al abrir la ventana.
- Corregir nombre, teléfono o dirección ya no queda bloqueado por documentos duplicados antiguos que no se están cambiando. Si se cambia la cédula o el NSS por uno que pertenece a otra ficha, se mantiene la protección y se explica el conflicto. No se fusionan pacientes.
- Nuevo botón **Historial de turnos** en **Reporte estadístico**: búsqueda por fechas y usuario, disponible para el personal autorizado a consultar reportes de Admisión. Seleccione una fila, pulse **Usar turno en reporte** y luego **Generar reporte**.
- La selección incluye las atenciones del usuario que hizo la admisión y conserva el horario real del turno. Puede exportarse a PDF o Excel con los filtros del reporte. Los archivos se generan localmente desde los datos; esta función no almacena documentos en la nube.

## Cierres y reportes

- Una autorización válida con al menos cuatro dígitos, registrada antes del cierre, cuenta como facturada sin exigir la marca manual. Un preliminar posterior no oculta otro recibo autorizado de la misma atención.
- **Heredadas**: pendientes originadas en el turno inmediatamente anterior. **Históricas**: pendientes de turnos anteriores a ese. Se muestran históricas recibidas, facturadas durante el turno y pendientes al cierre por separado.
- Los cierres previamente capturados conservan sus cifras y reglas. No se generan masivamente cierres faltantes anteriores.
- **Abrir PDF** y **Vista previa e imprimir** usan el visor integrado sin exigir guardar antes una copia.

## Actualización

Actualice todas las estaciones a la 1.2.0 y cierre las versiones anteriores antes de volver a trabajar. El primer arranque instala la compatibilidad de las categorías del cierre sin reiniciar la fecha de activación de los cierres automáticos. La generación e impresión siguen el flujo de cambio de turno establecido.

Descargue el ZIP, extraiga su contenido y ejecute **SIGEH.exe**. Una instalación existente conserva su configuración; una estación completamente nueva necesita la configuración habitual de conexión. Los datos de producción no están incluidos en el paquete público.

Validación: 1,599 pruebas aprobadas, 60 subpruebas aprobadas y una prueba de capacidad de producción omitida deliberadamente. Aplicación, lanzador y actualizador compilados; arranque y generación de documentos verificados desde el paquete. La impresora física del hospital no estuvo disponible para esta validación.
