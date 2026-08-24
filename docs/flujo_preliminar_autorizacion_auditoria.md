# Flujo local: preliminar, autorización y auditoría

## Objetivo

Separar la preparación del recibo, la autorización de la aseguradora y la validación
financiera. Un documento impreso no se considera automáticamente facturado.

## Estados del documento

1. `PRELIMINAR`
   - No tiene número de autorización.
   - Puede imprimirse con banner y marca de agua.
   - Sigue editable para corregir datos o agregar artículos que aún no estaban en el catálogo.
   - No puede asignarse ni confirmarse desde Auditoría.

2. `LISTO_AUDITORIA`
   - Tiene número de autorización, o corresponde a un paciente no asegurado.
   - Genera el documento completo sin banner ni marca de agua.
   - Puede asignarse a Auditoría Médica y Cuentas.
   - Continúa financieramente `PENDIENTE` hasta completar la revisión.

3. `FINAL`
   - Auditoría completó la prevalidación y su lista de verificación.
   - El recibo fue confirmado como `FACTURADO`.
   - Queda protegido contra edición directa.

## Estados financieros

- `PENDIENTE`: incluye preliminares y documentos listos que aún no han sido validados.
- `FACTURADO`: confirmado por un usuario autorizado después de la revisión.
- `NO_FACTURADO`: revisado, pero no procesable; exige motivo estructurado.
- `SIN_CLASIFICAR`: datos históricos cuyo estado real no se conoce.

## Controles de auditoría

Antes de confirmar como facturado se verifica:

- identidad del paciente;
- cobertura y ARS;
- autorización o condición de no asegurado;
- diagnóstico y soporte clínico;
- exactitud de servicios, cantidades, precios y total;
- posibles duplicados;
- sincronización del PDF;
- antigüedad, fecha atrasada y nivel de riesgo.

La evidencia se conserva en el historial con usuario, fecha, lista de verificación,
prevalidación, riesgo, observación y referencia.

## Reapertura y corrección

Al reabrir un recibo facturado:

- sale inmediatamente de los totales y comparaciones de facturación confirmada;
- el reporte financiero actual se recalcula sin ese monto;
- los PDF históricos ya generados se conservan como fotografías del momento;
- el PDF individual anterior se marca como no sincronizado y no puede reutilizarse
  para una nueva validación;
- se limpian la asignación, la lista y la aprobación de auditoría anteriores;
- se incrementa la versión y el recibo se abre directamente en edición;
- se recuperan paciente, fechas, diagnóstico, cobertura, ARS, autorización, sala,
  artículos, cantidades, precios y subtotales;
- al guardar se registra monto anterior, monto nuevo, diferencia, autorización,
  artículos y número de versión;
- debe regenerarse el PDF y completarse una auditoría nueva antes de volver a
  marcarlo como facturado.

La validación posterior utiliza una nueva fecha de confirmación. Por tanto, el monto
entra en el período financiero correspondiente a la nueva validación, mientras que la
producción original conserva su fecha de creación.

## Lectura visual del historial

- Amarillo: documento preliminar o pendiente de autorización.
- Azul: listo para auditoría.
- Verde: facturado y validado.
- Rojo: no facturado.
- Gris: histórico sin clasificar.

El color siempre se acompaña de texto para mantener la comprensión y accesibilidad.
