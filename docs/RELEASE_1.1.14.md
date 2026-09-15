# SIGEH 1.1.14

Corrige la generación del cierre automático de Facturación al relevar el turno.

- El arranque detecta y repara el campo de identidad faltante en instalaciones anteriores, que impedía guardar el cierre.
- La generación automática comienza con los cierres posteriores a la activación de esta actualización. No regenera los reportes anteriores.
- Los pendientes heredados se obtienen de las atenciones de turnos anteriores, aunque falte su reporte de cierre. Se excluyen las autorizaciones anteriores al inicio del turno y se respeta la fecha de corte del cierre.
- Conserva el registro del PDF, su apertura y solicitud de impresión automática desde la estación principal, y su posterior consulta desde el historial.

Cerrar la versión anterior en todas las estaciones antes de abrir la 1.1.14, para que todos usen la nueva regla de generación desde la actualización. Los cierres y la impresión requieren conexión y una impresora configurada en la estación principal.
