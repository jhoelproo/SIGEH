# SIGEH 1.2.3

- Corrige el redondeo que convertía precios como RD$ 6.00 en RD$ 5.99. Conserva los precios 5.99 legítimos.
- Recupera edad, teléfono y dirección desde los datos guardados de la atención. Las correcciones del paciente se propagan a sus atenciones de los últimos siete días, conservando identidades, turnos y recibos anteriores.
- Cambia el cierre a un resumen de cantidades e importes, sin imprimir cientos de pacientes pendientes.
- Un recibo válido guardado, incluso preliminar, resuelve la atención vinculada. Las autorizaciones de al menos cuatro dígitos se muestran como subconjunto, sin duplicar totales.
- Separa pendientes del turno actual, del turno anterior y heredadas históricas. El backlog anterior al 23/09/2026 queda fuera de las nuevas pendientes, conservando el historial.
- Congela el cierre con los datos centrales al confirmar el relevo, incorpora los recibos de todas las estaciones y protege los reintentos contra cambios de turno duplicados.
- Incluye la sincronización incremental y el control de transferencia previamente evaluados.

Descarga el ZIP de Windows x64, extráelo por completo y abre `SIGEH.exe`. Para actualizar una estación existente, conserva su configuración de conexión y datos locales. El paquete público no contiene credenciales ni bases de pacientes.

Validación: 1.724 pruebas aprobadas; cobertura del código modificado 98,53% de líneas y 96,59% de ramas. Detalle y limitaciones en `CONSISTENCY_AND_CLOSE_QA.md`.
