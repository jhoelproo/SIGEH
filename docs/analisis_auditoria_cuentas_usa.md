# Análisis local: auditoría y cuentas hospitalarias

Fecha del análisis: 16 de julio de 2026.

## Referencias examinadas

- [HHS-OIG: General Compliance Program Guidance](https://oig.hhs.gov/compliance/general-compliance-program-guidance/)
- [CMS: Electronic Health Care Claims](https://www.cms.gov/medicare/coding-billing/electronic-billing/electronic-healthcare-claims)
- [CMS: Review Reason Codes and Statements](https://www.cms.gov/data-research/monitoring-programs/medicare-fee-service-compliance-programs/review-reason-codes-and-statements)
- [CMS: Medicare Claims Processing Manual](https://www.cms.gov/regulations-and-guidance/guidance/manuals/internet-only-manuals-ioms-items/cms018912)
- [CMS: ICD-10 Implementation Guide for Small Hospitals](https://www.cms.gov/files/document/icd10sm-hosphandbook0604131pdf)
- [Epic: Access & Revenue Cycle](https://www.epic.com/software/access-and-revenue-cycle/)
- [Oracle Health: Claims, Prior Authorizations and Payments](https://docs.oracle.com/en/industries/health/claims-prior-authorizations-payments/index.html)

## Capacidades observadas en sistemas maduros

1. Verificación de cobertura, pagador primario y autorización antes de facturar.
2. Integridad de cargos: servicios, cantidades, precios y total deben reconciliarse.
3. Soporte de necesidad médica y documentación clínica antes del envío.
4. Ediciones previas al reclamo para detectar datos incompletos y duplicados.
5. Colas de trabajo con responsable, antigüedad, prioridad y excepciones.
6. Motivos normalizados para rechazo o no facturación.
7. Historial inmutable de decisiones, correcciones y responsables.
8. Ciclo posterior: lote, envío electrónico, acuse, rechazo, denegación,
   apelación, remesa, pago, glosa y conciliación contractual.
9. Indicadores operativos: pendientes, antigüedad, tiempo de validación,
   tasa de rechazo, denegaciones, recuperación y cuentas por cobrar.

## Brechas del sistema antes de esta iteración

El sistema ya distinguía `PENDIENTE`, `FACTURADO`, `NO_FACTURADO` y
`SIN_CLASIFICAR`, conservaba historial y separaba permisos. Faltaban:

- prevalidación objetiva antes de confirmar;
- evidencia estructurada de lo revisado;
- detección de posibles duplicados;
- cola con responsable y antigüedad;
- riesgo para ordenar casos;
- razones codificadas;
- datos de auditoría en Excel.

## Cambios adoptados en la versión local

- Cola de auditoría limitada a recibos nuevos `PENDIENTE`; los históricos
  `SIN_CLASIFICAR` no contaminan el indicador operativo.
- Acciones **Asignarme** y **Liberar**, con control de concurrencia para impedir
  que un auditor valide el caso asignado a otro.
- Antigüedad, pendientes con tres días o más, casos sin asignar y ARS de mayor
  volumen.
- Prevalidación de identidad, cobertura, diagnóstico, cargos, total, PDF y
  posibles duplicados.
- Puntaje de riesgo de 0 a 100 y niveles Baja, Media y Alta.
- Lista obligatoria de verificación antes de confirmar como facturado.
- Motivos normalizados para `NO_FACTURADO`.
- Fotografia JSON de prevalidación y checklist en el historial inmutable.
- Exportación Excel con asignado, riesgo y código del motivo.

## Funciones deliberadamente pospuestas

No deben simularse hasta diseñar el ciclo real con las ARS dominicanas:

- elegibilidad electrónica y autorización;
- codificación ICD/CPT/HCPCS automatizada;
- reclamación EDI y clearinghouse;
- lotes por ARS y acuses de recepción;
- denegaciones, apelaciones y glosas;
- remesas, pagos, cobros y conciliación contractual;
- cuentas por cobrar y antigüedad desde el envío.

La siguiente etapa lógica es definir el **lote por ARS** y sus estados antes
de implementar denegaciones o cobros.
