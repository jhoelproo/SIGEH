# Prueba local: Admisión, recibos y listados mensuales

Esta versión es exclusivamente local. La aplicación de Facturación abre la base
operativa de Admisión en modo SQLite `read-only`; nunca la migra ni escribe en
ella.

## 1. Validar un paciente asegurado

1. Inicia la versión local de Facturación.
2. Selecciona el módulo superior **Admisión**.
3. Confirma que se cargue la fecha más reciente disponible y busca por nombre,
   atención, NSS, cédula o ARS.
4. Selecciona una atención y pulsa **Usar paciente en Facturación**. También
   puedes hacer doble clic.
5. Como alternativa compacta, en el formulario puedes pulsar **Validar paciente
   en Admisión** y buscar por NSS o cédula.
6. Comprueba que el nombre y la fecha queden bloqueados y provengan de Admisión.
7. Si la ARS todavía no tiene equivalencia en el catálogo local, selecciona la
   ARS correcta antes de guardar.
8. Agrega cargos y guarda el preliminar.

Resultado esperado: el recibo queda vinculado al identificador permanente de la
atención. Una segunda búsqueda de esa misma atención recupera el recibo existente
y no crea un duplicado.

## 2. Revisar la conciliación estadística

1. Entra con **Auditoría Médica y Cuentas** o **Administrador**.
2. Selecciona **Admisión** y abre **Comparación estadística**.
3. Consulta uno o varios días, con un máximo de 63.
4. Revisa los bloques por día y por facturador.
5. En **Pacientes del período**, verifica la columna **Validación**.

Resultado esperado:

- `Sin recibo` identifica una atención elegible todavía no vinculada.
- `Coincide` confirma nombre, fecha, cobertura y ARS.
- `Revisar` enumera el dato diferente sin modificarlo automáticamente.
- Las cifras se identifican como auxiliares y no cambian el Reporte General de
  Medicamentos.

## 3. Comprobar exclusiones

- Una atención `ANULADA` no debe aparecer.
- Una atención con tipo `URGENCIA` no debe aparecer.
- Una atención con identidad pendiente de revisión no debe aparecer.
- Si una atención cambia a uno de esos estados antes de guardar, Facturación
  debe bloquear el guardado al verificarla nuevamente.

## 4. Validar un paciente sin seguro

1. Busca por la cédula de una atención marcada como `SIN SEGURO`.
2. Confirma la atención.

Resultado esperado:

- Se crea una sola cabecera de recibo preliminar automáticamente.
- El historial la muestra en el renglón **SIN SEGURO**.
- El recibo queda pendiente, con monto cero, hasta que el facturador agregue los
  cargos.
- Al completarlo y guardarlo, queda listo para el flujo de Auditoría.

## 5. Confirmar facturación

1. Completa el recibo y su autorización cuando corresponda.
2. Abre **Auditoría Médica**.
3. Completa la lista de verificación.
4. Confirma el recibo como **Facturado**.

Resultado esperado: la fecha de facturación es la fecha y hora de esta
confirmación, no la fecha de creación del preliminar.

## 6. Generar un listado mensual

1. Entra con un usuario de **Auditoría Médica y Cuentas** o **Administrador**.
2. Selecciona la pestaña superior **Listados de ARS**. Toda el área central
   cambiará al módulo de listados sin abrir otra ventana.
3. Pulsa **Generar listado mensual**.
4. Selecciona año, mes y ARS o `SIN SEGURO`.
5. Confirma la generación.

Resultado esperado:

- Solo aparecen recibos vinculados a Admisión y confirmados como `FACTURADO`.
- El mes se calcula por la fecha de confirmación de facturación.
- El corte muestra la hora exacta de generación.
- Cada fila conserva paciente, NSS, ARS, comprobante/NCF, fecha de facturación
  y monto como fotografía del listado.
- El comprobante/NCF pertenece solamente al listado. No aparece ni modifica el
  recibo de Facturación.
- El comprobante aparece como `PENDIENTE` hasta editarlo dentro del listado.
- La búsqueda permite localizar paciente, NSS, recibo, ARS o comprobante.

## 7. Editar el listado

- **Retirar del listado** exige un motivo y conserva el evento en el historial.
- **Agregar al listado** solo ofrece recibos facturados que correspondan al mismo
  mes y ARS.
- Reabrir un recibo facturado lo retira automáticamente de listados en borrador.
- Un listado cerrado protege sus recibos y exige corregir primero el listado.

## 8. Cómo está conectada Admisión

Facturación consulta directamente la base local:

`C:\ProgramData\Hospital\GeneradorHojasEmergencia\pacientes.db`

La conexión usa SQLite en modo `mode=ro` y `PRAGMA query_only=ON`. Facturación
puede buscar y revalidar una atención, pero no puede crear, editar ni anular
registros dentro de Admisión.

En esta etapa no se deben fusionar los programas en un único ejecutable.
Mantenerlos separados permite validar el flujo sin poner en riesgo el registro
de Admisión. La integración usa el identificador estable de la atención y una
copia controlada de los datos necesarios para auditoría.

No uses datos reales en capturas o mensajes de prueba. El NSS debe tratarse como
información sensible.
