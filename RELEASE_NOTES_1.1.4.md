# SIGEH v1.1.4 — hotfix de UUID opcional en Facturación bypass

## Corrección

- Un recibo autorizado sin vínculo con Admisión guarda
  `admission_global_attention_id` como SQL NULL, nunca como cadena vacía.
- Se normalizan valores legacy vacíos/blancos antes del INSERT y UPDATE.
  Un UUID válido se conserva; uno inválido no vacío se rechaza antes del SQL.
- No se crean atenciones ni UUID ficticios. Los identificadores de estación
  históricos siguen siendo TEXT y no se fuerzan a UUID.
- Se mantienen los permisos y estados del bypass: autorización válida →
  LISTO_AUDITORIA/CLEAR; inválida o corta → LISTO_AUDITORIA/PENDING_REVIEW;
  ausente → PRELIMINAR.
- Diagnóstico seguro de tipos/ausencia y fallos de persistencia; la ventana no
  muestra SQL crudo cuando falla el worker de guardado.

## Validación

- Reproducción previa del error `InvalidTextRepresentation` en PostgreSQL
  temporal, con el esquema real creado por SIGEH.
- Suite completa: 1.088 PASS, 0 FAIL, 1 omitida de capacidad productiva ajena
  al hotfix, 60 subpruebas PASS.
- Revisión final después de ordenar el import: 77 PASS y 3 subpruebas PASS.
- Pruebas PostgreSQL de bypass, edición, UUID legacy/válido, rollback después
  de insertar ítems, reintento y doble guardado concurrente.
- Regresiones de verificación normal, claims, elegibilidad, updater y rollback.
- Cobertura del cambio: 100 % de líneas y ramas; 0 errores nuevos de lint.
- Build limpio y smoke del ZIP final extraído: launcher, Admisión/Historial,
  PDF, Excel y entrypoint del updater PASS. ZIP sin base operacional ni secretos.

SHA-256 del ZIP final:
`c5b6dd1b9cebff64b4d661ca69cfcabbf5c4c27805ea016e46ecc2555a7878d3`

No se ejecutó un guardado manual de F5 en la instalación hospitalaria ni una
prueba física simultánea en dos computadoras. Los controles automatizados no
se presentan como prueba física.

## Datos y actualización

No se añade una migración ni se modifican pacientes, recibos existentes,
historial, turno, representante, PRIMARY, epoch o configuración productiva.
La consulta de esquema productivo fue exclusivamente de lectura.

El ZIP público excluye bases operacionales y secretos. Para actualizar una
instalación que funciona, respaldarla y usar el updater existente; conservar
su conexión, SQLite y archivos operacionales. Una instalación nueva requiere
el aprovisionamiento seguro de conexión existente. No ejecutar reset, SEED,
MERGE ni relevo para instalar este hotfix.

Se mantienen los pendientes ajenos a este cambio documentados en v1.1.3:
QA físico multiestación, verificación visual/numérica de los reportes y deuda
de complejidad del módulo heredado. Esta release no afirma corregirlos.
