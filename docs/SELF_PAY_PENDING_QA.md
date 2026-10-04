# Pendiente de pago en cobros directos — implementación y QA

## IMPLEMENTACIÓN

Extranjeros: Pagado / Pendiente de pago. No asegurados: Pagado / Exonerado /
Pendiente de pago. Se mantienen la selección explícita de cobertura, la tarifa
de SENASA Contributivo, la sala, la protección de tarifas y los permisos existentes.
Guardar un pendiente genera un recibo con deuda y recaudación cero, sin exigir
verificación ni autorización. Para cobrarlo se edita el mismo recibo y se elige
Pagado; no se crea un segundo recibo. Exonerar sigue requiriendo motivo y solo
está disponible para no asegurados.

El historial permite filtrar pendientes. PDF, Excel y cierre separan cantidad e
importe adeudado de pagos y exoneraciones. El PDF pendiente advierte «No acredita
pago», muestra importe pendiente e importe cobrado cero. El estado largo se
ajusta dentro del encabezado sin afectar los recibos asegurados.

`paid_at` registra la transición a Pagado. Una edición de un recibo ya pagado
conserva ese instante. Un pago de un recibo creado en un turno anterior se cuenta
en el turno del pago, con inicio incluido y final excluido. Los cierres previamente
capturados permanecen inmutables. Los reportes generales del historial conservan
su criterio de fecha de generación del recibo; la fecha de pago se usa en el cierre.
No se añadieron pagos parciales: cada recibo se paga por su total.

La actualización local del esquema habilita el estado, conserva recibos anteriores
y asigna a los pagos anteriores su fecha original de generación. La comprobación
de arranque detecta la columna faltante y actualiza también la captura del cierre.
No se modificó producción ni el número de versión.

Archivos: `self_pay_billing.py`, `self_pay_history_dialog.py`, `CALCULOS_QT.py`,
`billing_close_model.py`, `billing_close_store.py`, `billing_close_report.py`,
`pdf_engine/template.html`, `pdf_engine/styles.css`,
`report_engine/report_template.html`; pruebas y este informe.

## PRUEBAS

TDD: ocho pruebas específicas, seis fallos antes del cambio y ocho aprobadas
tras implementarlo. Las pruebas SQL verifican guardado, reapertura, paso a pagado,
conservación de la fecha del pago al editar, pendiente a exonerado, restricciones,
rollback y migración idempotente sin perder recibos existentes. La captura SQL
comprueba un pago de un recibo anterior y un pendiente del turno, con cifras
separadas y cierre inmutable. Una prueba reprodujo la omisión del pago anterior
en la consulta de captura; pasó tras ampliar su selección por fecha de pago.

Pruebas Qt: controles de ambos perfiles, tarifas protegidas, creación de trabajos
de guardado, filtro de pendientes, texto legible y separación de importes.
Pruebas de documento: aviso de deuda, cero cobrado, PDF y Excel, y regresión de
los recibos asegurados. Límites temporales: antes del inicio, inicio exacto y
final exacto; cantidades y importes se cubren con las pruebas existentes.

## RESULTADOS

Suite completa: **2,025 casos PASS, 0 FAIL, 0 errores y 1 skipped**
(incluye subpruebas). La única omisión es la prueba opcional de capacidad de
una base real. Los once grupos se ejecutaron con la misma huella del código
final. Evidencia: `output/self-pay-pending-full-regression-summary.json`,
`output/self-pay-pending-regression-groups.json` y los XML/logs de cada grupo.
Las pruebas específicas adicionales de UI: 53 PASS. La repetición SQL que
confirmó la corrección de captura: 2 PASS; las restantes pruebas SQL y migración
se ejecutaron en la suite completa con PostgreSQL local desechable.

## COBERTURA

Herramienta real: coverage.py con ramas, evidencia en
`output/self-pay-pending-coverage.json`. Núcleo de cobros y diálogo: 100 % líneas
/ 100 % ramas. Modelo del cierre completo: 100 % líneas / 98 % ramas; la rama
no cubierta pertenece al cálculo previo de autorización asegurada. La nueva
selección por fecha de pago queda cubierta. No se atribuye cobertura total al
módulo gráfico heredado completo. El agregado medido del cambio completo de
cobros directos contiene 476/493 líneas cubiertas (96,55 %) y 139/156 ramas
(89,10 %), según `output/self-pay-pending-patch-coverage.json`. Las omisiones
están en la integración gráfica heredada; la lógica de pagos y deuda es 100 %.

## CALIDAD

Ruff: formato y lint de módulos nuevos, tests y modelo. Lint diferencial:
0 diagnósticos nuevos en los módulos heredados. Mypy: 0 errores en los módulos
nuevos y 0 diagnósticos nuevos en los módulos heredados. Radon: máximo 9 en los
módulos de cobro/diálogo, 8 en el modelo, 3 en el selector nuevo por fecha de pago.
Pylint: sin bloques duplicados detectados en la comprobación de seis líneas.
Bandit: dos B608 de consultas existentes que combinan cláusulas constantes y
valores externos parametrizados; revisados, sin hallazgos sin resolver.
Persisten las excepciones de complejidad y diagnósticos heredados descritos en
`FOREIGN_UNINSURED_BILLING_QA.md`; no se debilitaron reglas ni se suprimieron avisos.

## BUILD

Comando: `python -m PyInstaller --noconfirm --log-level WARN --distpath
output/self-pay-preview-app --workpath build/self-pay-preview-app build_app.spec`.
Resultado: PASS, salida 0. Log: `output/self-pay-pending-build-final.log`.
El archivo del ejecutable contiene el nuevo estado, el campo de fecha de pago,
la selección por ventana de pago y las plantillas exactas del código fuente.
Los tres autodiagnósticos (`--self-test-pdf`, `--self-test-report-viewer`,
`--self-test-reports`) pasaron con salida 0. Evidencia:
`output/self-pay-pending-package-smoke.json`.

## QA

Se verificaron las cuatro pasadas: funcionalidad y reglas; regresiones; limpieza,
responsabilidades y complejidad; pruebas, métricas, build y presentación.
El PDF pendiente y el reporte PDF/Excel se generaron con datos ficticios y se
revisaron visualmente: una página cada PDF, importes separados, sin recortes.
La demo se abrió con el catálogo real copiado previamente a localhost y el estado
pendiente seleccionado. Ninguna prueba usa datos clínicos o modifica producción.
Las consultas nuevas tienen parámetros; se mantienen autorización de operadores,
restricciones de cobertura y transacciones de recibo/ítems/estado/documento.

## QUALITY GATES

| Gate | Resultado |
|---|---|
| Funcionalidad | PASS |
| Unit tests, límites y regresión completa | PASS |
| Integración PostgreSQL / Qt / documentos | PASS |
| Cobertura | PASS — agregado 96,55 % líneas / 89,10 % ramas; núcleo crítico 100 % |
| Formatter y lint | PASS — sin diagnósticos nuevos |
| Tipos y análisis estático | PASS — sin diagnósticos nuevos; avisos de seguridad revisados |
| Complejidad y duplicación | PASS — módulos ≤9; excepciones heredadas documentadas |
| Build y smoke | PASS — build y tres autodiagnósticos |
| Seguridad y QA final | PASS — consultas, permisos, constraints y revisión visual |
| Producción / pagos reales | N/A — evaluación local |
| Capacidad de una base real | N/A — prueba opcional ajena a este flujo |

## PROBLEMAS PENDIENTES

Evaluación local: no se desplegó ni se validó con pagos reales del hospital.
La prueba opcional de capacidad de una base real no pertenece a este flujo.
Los avisos de Qt sobre estilos de controles heredados y las advertencias de
paquetes opcionales del build se conservan sin ocultarlos.

**ESTADO FINAL: APROBADO PARA ENTREGA — evaluación local.**
