# Auditoría de integración central entre Admisión y Facturación

Fecha de revisión: 17 de julio de 2026  
Alcance: versión local de validación

## Decisión arquitectónica

La aplicación de Facturación de medicamentos pasa a ser la interfaz central de
trabajo, pero las bases no se fusionan.

- **Admisión** sigue siendo la fuente autorizada de pacientes y atenciones.
- **Facturación** sigue siendo la fuente autorizada de recibos, ítems, montos,
  estados financieros, listados de ARS y reportes.
- **Conciliación** es una vista derivada y auxiliar. Detecta diferencias, pero
  no modifica ninguna de las dos fuentes ni reemplaza el Reporte General de
  Medicamentos.

Esta separación evita que una corrección visual, una falla de red o una
diferencia estadística altere cifras financieras confirmadas.

## Evidencia revisada

### Base operativa de Admisión

Ruta local:

`C:\ProgramData\Hospital\GeneradorHojasEmergencia\pacientes.db`

La inspección se realizó con SQLite en modo `mode=ro` y `PRAGMA query_only=ON`.

- 18,557 atenciones.
- 23,382 pacientes.
- 242 días con atenciones en el intervalo observado.
- Entre 1 y 142 atenciones por día; promedio aproximado de 76.7.
- Las atenciones observadas están registradas como Emergencia, activas y con
  identidad validada.
- La estructura ya contempla anulaciones, auditoría, documentos, turnos,
  identificadores alternativos y trabajos de salida.
- Las fechas históricas se almacenan principalmente como `dd/mm/aaaa`, aunque
  otros componentes recientes usan ISO. La integración anterior suponía ISO y
  podía no cargar la fecha del servicio correctamente.
- Los datos inspeccionados no aportan una relación histórica confiable entre
  cada atención y un usuario de Admisión. Por eso no se inventa una atribución.

### Base local de Facturación

La versión de prueba contiene recibos locales y los estados:

- Pendiente.
- Facturado.
- No facturado.
- Histórico sin clasificar.

Facturación ya conserva el usuario creador en `recibos.username`, la fecha real
de generación, la fecha de servicio, el estado financiero y el vínculo
`admission_atencion_id`. Esta es la relación utilizada para las estadísticas
por facturador.

### Motor de reportes

El reporte oficial está centralizado en `PanelDataService` y consulta
`recibos`/`recibo_items`. La conciliación de Admisión no fue incorporada a ese
servicio para evitar que una atención sin recibo se convierta accidentalmente
en dinero reportado.

## Riesgos identificados y controles aplicados

### Duplicidad de recibos

Riesgo: crear dos recibos para una misma atención.

Control:

- índice único para vínculos activos;
- consulta previa por `admission_atencion_id`;
- revalidación de la atención justo antes de usarla;
- si existe un pendiente, se recupera;
- si está facturado o no facturado, se bloquea la edición directa.

### Atención anulada o de Urgencia

Riesgo: usar una atención que ya no debe facturarse en este flujo.

Control:

- solo se consultan atenciones `ACTIVA`;
- se excluye `URGENCIA`;
- se exige identidad `VALIDADA`;
- se excluyen atenciones que requieren revisión;
- se vuelve a consultar Admisión al pasar el paciente a Facturación.

### Escritura accidental en Admisión

Riesgo: que Facturación cambie pacientes o estados de la otra aplicación.

Control:

- URI SQLite con `mode=ro`;
- `PRAGMA query_only=ON`;
- ninguna migración ni sentencia de escritura desde Facturación.

### Fechas incompatibles

Riesgo: interpretar incorrectamente `17/07/2026` como una fecha no válida.

Control:

- normalización de `aaaa-mm-dd`, `dd/mm/aaaa` y `dd-mm-aaaa`;
- comparaciones internas en ISO;
- pruebas automatizadas de fecha histórica.

### Búsqueda demasiado amplia

Riesgo: una búsqueda alfabética sin dígitos podía convertir el patrón de NSS o
cédula en `%%` y devolver pacientes no relacionados.

Control:

- los campos numéricos solo participan cuando la búsqueda contiene dígitos;
- prueba automatizada de nombre, rango y elegibilidad.

### Exposición de identificadores

Riesgo: mostrar NSS o cédula completos en una lista general.

Control:

- se muestran enmascarados y solo los últimos cuatro dígitos quedan visibles;
- los valores completos solo se conservan en la vinculación necesaria.

### Confusión entre estadística y reporte financiero

Riesgo: interpretar el total de atenciones como monto de medicamentos.

Control:

- rótulo permanente “Conciliación estadística auxiliar”;
- resultado técnico con
  `authoritative_report=REPORTE_GENERAL_DE_MEDICAMENTOS`;
- el motor oficial de reportes no fue modificado por esta integración;
- una atención sin recibo se muestra como pendiente de trabajo y suma RD$ 0.00.

### Diferencias silenciosas

Riesgo: que el nombre, fecha, cobertura o ARS cambien después de vincularse.

Control:

- la vista compara los datos actuales de Admisión con el recibo vinculado;
- marca `Coincide`, `Sin recibo` o `Revisar`;
- identifica diferencias de nombre, fecha, cobertura y ARS.

### Rendimiento

Riesgo: bloquear la interfaz consultando todo el histórico.

Control:

- consulta inicial sobre la fecha elegible más reciente;
- rango máximo de 63 días;
- límite técnico de 10,000 atenciones por consulta;
- búsqueda visual sobre el resultado ya cargado.

## Flujo implementado

1. El usuario abre el módulo superior **Admisión**.
2. El sistema carga la fecha elegible más reciente de la base operativa.
3. La lista muestra pacientes, cobertura, vínculo, estado, facturador y monto.
4. El usuario selecciona una atención.
5. Facturación vuelve a validar que siga activa y elegible.
6. Si no tiene recibo:
   - asegurado: carga nombre, fecha y ARS en el formulario;
   - no asegurado: crea una cabecera pendiente única y la abre para completar.
7. Si ya tiene un recibo pendiente, lo recupera.
8. Si ya fue facturado o marcado no facturado, no permite modificarlo sin
   reapertura de Auditoría.

El formulario compacto por NSS/cédula se conserva como alternativa para no
romper el flujo conocido.

## Conciliación por día y facturador

La comparación diaria usa la fecha de servicio de la atención. Muestra:

- atenciones admitidas;
- atenciones con y sin recibo;
- recibos facturados, pendientes y no facturados;
- monto generado en recibos;
- monto facturado confirmado.

La comparación por usuario usa `recibos.username`, es decir, el facturador que
generó el recibo. No representa al operador de Admisión.

Solo Administración y Auditoría Médica y Cuentas pueden ver esta comparación.
Los facturadores pueden consultar y seleccionar pacientes, pero no acceden al
consolidado estadístico.

## Qué no se implementó deliberadamente

- No se unificaron los ejecutables ni las bases.
- No se copió el padrón completo de pacientes a PostgreSQL.
- No se asignaron atenciones históricas a usuarios inexistentes.
- No se alteró el Reporte General de Medicamentos.
- No se generó dinero a partir de una atención sin ítems.
- No se escribió en la base de Admisión.
- No se publicó nada en GitHub ni en producción.

## Recomendación para una fase futura

Cuando ambas aplicaciones estén estabilizadas conviene reemplazar la lectura
directa del archivo SQLite por un pequeño servicio local versionado. Ese
servicio debería exponer:

- atención por ID;
- búsqueda por identificador;
- atenciones elegibles por rango;
- versión del esquema;
- estado de salud y marca de última sincronización.

La interfaz y el modelo de autoridad implementados ahora permiten hacer ese
cambio sin modificar el reporte financiero.

## Criterios de aceptación local

- Admisión aparece como módulo superior de la aplicación central.
- Las atenciones anuladas, de Urgencia o con identidad pendiente no aparecen.
- Seleccionar un paciente carga su nombre sin redigitación.
- Una atención no puede producir dos recibos activos.
- Un recibo facturado no puede editarse directamente.
- Los identificadores se muestran enmascarados.
- Las diferencias de nombre, fecha, cobertura y ARS quedan visibles.
- La estadística por día y facturador no cambia el reporte general.
- Las pruebas automatizadas y la apertura sin interfaz visible finalizan sin
  errores.

