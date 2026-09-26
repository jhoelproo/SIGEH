# SIGEH 1.2.4

Corrige la separación de un turno al ajustar su horario. La corrección administrativa conserva ahora el inicio, los pacientes y el identificador del turno, de modo que el siguiente relevo cierre el período completo.

También corrige el orden de bloqueo entre la comprobación de conexión y las operaciones de turno, evita que dos ventanas se quiten repetidamente la sesión principal y conserva los eventos locales pendientes al reabrir una instalación ya preparada.

Los informes históricos existentes permanecen intactos. La corrección de un informe ya emitido requiere verificar sus atenciones; esta versión no fusiona ni elimina pacientes automáticamente.

Validación: 1.756 pruebas aprobadas, ninguna fallida y una prueba externa de capacidad omitida. Cobertura del código modificado: 100 % de líneas y ramas. Builds de aplicación y actualizador aprobados; siete comprobaciones del ZIP extraído y 1.822 hashes verificados. Evidencia y limitaciones en [el informe de QA](https://github.com/jhoelproo/SIGEH/blob/v1.2.4/docs/TURN_INCIDENT_20260926_QA.md).

Descarga el ZIP de Windows x64, extráelo por completo y abre `SIGEH.exe`. Al actualizar una estación existente, conserva su configuración de conexión y datos locales. El paquete público no contiene credenciales ni bases de pacientes.
