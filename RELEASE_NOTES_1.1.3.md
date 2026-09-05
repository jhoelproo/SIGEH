# SIGEH v1.1.3 — consistencia de Admisión y Facturación

## Publicación con pendientes conocidos

Publicada por autorización expresa del responsable para utilizar esta versión
en su estado actual. Esto no convierte los controles pendientes en aprobados:
la auditoría integral continúa **NO APROBADA PARA ENTREGA**.

- No se ejecutaron las pruebas físicas simultáneas en dos computadoras.
- El autodiagnóstico de reportes tiene rótulos y datos sintéticos inconsistentes;
  generar un archivo no demuestra coherencia visual ni de cifras productivas.
- Quedan funciones heredadas por encima del objetivo de complejidad del código.

## Correcciones incluidas

- Consulta, selector y revalidación de Facturación comparten la identidad central
  del turno y el epoch vigente. Búsqueda normalizada por nombre y soporte de UUID.
- Revalidación uniforme de EMERGENCIA, cobertura, ARS habilitada, recibos existentes,
  herencias explícitas, descartes y anulaciones; un error central no significa vacío.
- Reservas centrales con propiedad de sesión/estación, recuperación de expiradas
  y protección contra apropiación de una reserva activa incluso por Admin.
- Cancelar o reemplazar una selección libera únicamente la reserva propia en
  background. Una liberación atrasada no invalida una reserva readquirida.
- Generar hojas usa el estado operacional autorizado y no el JSON local como
  autoridad online. Rechaza workers atrasados y vuelve a validar antes de guardar.
- Un formulario inválido ya no impide terminar el cierre de sesión.
- Arranque sobre esquema vacío: se instala el esquema híbrido antes de crear los
  índices de Facturación que dependen de sus columnas.

## Continuidad de datos

No incorpora reset de historial, SEED, MERGE, nuevo turno, reasignación de
representante/PRIMARY ni eliminación de pacientes. No se añade una migración
nueva: se corrige el orden de las existentes. El updater conserva los datos y
la configuración de conexión existentes según su mecanismo habitual.

El ZIP público no contiene bases operacionales ni credenciales. Una instalación
nueva requiere provisionar la conexión mediante el mecanismo seguro existente;
la actualización no debe borrar la configuración de una instalación que funciona.

## Validación ejecutada

- Código funcional: 1.047 pruebas aprobadas, ninguna fallida, una omitida y 60
  subpruebas aprobadas, incluyendo PostgreSQL local aislado y concurrencia SQL.
- Preparación de v1.1.3: 131 pruebas adicionales aprobadas de versión, updater,
  preservación/rollback, empaquetado, selección y generación de hojas.
- Cobertura del cambio funcional: 97,31 % de líneas y 92,65 % de ramas; no es la
  cobertura de toda la aplicación legacy.
- Compilación limpia y comprobaciones del ZIP extraído en perfil temporal:
  launcher, construcción de Admisión/Historial, generación PDF/Excel y entrypoint
  del updater. No equivalen a una actualización física de dos estaciones.

## Actualización

Respaldar primero la instalación y sus datos. Utilizar el actualizador existente
con este paquete completo; no sustituir ni eliminar SQLite, archivos de conexión
o carpetas operacionales manualmente. Verificar después del arranque el mismo
turno, representante y conteo. No iniciar un relevo para corregir una discrepancia.
Ante un fallo, conservar logs y utilizar el rollback del actualizador.
