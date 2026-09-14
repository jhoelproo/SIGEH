# SIGEH 1.1.12 — integridad al editar recibos

- El administrador puede corregir el nombre y la fecha de servicio de un recibo editable. Se mantienen las restricciones de documentos cerrados y los controles de concurrencia.
- La ARS y la cobertura permanecen bloqueadas durante la edición, tanto en pantalla como al guardar.
- Los recibos conservan el creador y la fecha de generación originales; el editor queda identificado en la auditoría. El PDF usa al creador persistido.
- Las fechas históricas admitidas se normalizan antes de cargar o guardar; una fecha inválida produce un error explícito.
- El indicador distingue la edición de la creación y mantiene un mensaje coherente para autorizaciones cortas o largas. Las reglas de auditoría existentes permanecen vigentes.
- Se conserva la ARS histórica aunque ya no aparezca en el catálogo visible.

Esta entrega no incluye la función de extranjeros ni cambia automáticamente registros históricos. La fecha de servicio indicada como 13/09/2026 requiere identificar y contrastar el recibo antes de una corrección individual.

La evidencia de pruebas, cobertura, compilación y empaquetado se documenta en `RECEIPT_EDIT_QA.md`.

Validaciones ejecutadas: 1.538 pruebas y 60 subpruebas PASS, 0 FAIL; una prueba optativa de capacidad no ejecutada. Cobertura medida del código corregido: 100% de líneas y ramas. Build de aplicación y actualizador: PASS. ZIP: CRC y hashes de 1.822 archivos PASS; smoke del lanzador, PDF y reportes PASS.

SHA-256 del ZIP: `d994eeaff28c55a6a60a33bbd5e2eea429cb3c8a548f71099d2c618a555e58e9`.

Estado: borrador pendiente de cerrar el caso individual de fecha indicado en el informe de QA. No distribuir todavía a producción.
