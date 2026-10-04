# Cobros de extranjeros y no asegurados — implementación y QA

Fecha: 3 de octubre de 2026. Rama: `codex/foreign-uninsured-billing`.
Base de comparación: `441c0c6`, versión publicada 1.2.6.

Este informe corresponde a la implementación inicial. La ampliación posterior
de Pendiente de pago y sus validaciones se documentan en
[SELF_PAY_PENDING_QA.md](SELF_PAY_PENDING_QA.md).

## IMPLEMENTACIÓN

La selección explícita de cobertura distingue **Asegurado**, **No asegurado** y
**Extranjero**. Los dos últimos utilizan el catálogo y la sala de SENASA
Contributivo, sin crear copias de precios ni cambiar el flujo de los asegurados.
Los recibos conservan sus precios originales al editarlos después de un cambio de
tarifa; los recibos nuevos utilizan el catálogo vigente.

Los extranjeros utilizan **Pagado**. Los no asegurados utilizan **Pagado** o
**Exonerado**, con un motivo de al menos ocho caracteres para exonerar. El motivo
no se conserva si se selecciona Pagado. Estos recibos no requieren autorización,
verificación de paciente ni marcado manual de facturación a una aseguradora.

El botón **Cobros directos** abre un historial propio con fechas, cobertura,
estado, búsqueda de paciente/recibo y páginas de 100 registros. Desde allí se
pueden iniciar cobros, abrir recibos, editarlos y generar reportes PDF o Excel.
Los PDF utilizan el visor integrado. Los reportes se generan localmente y no se
suben a almacenamiento de documentos en la nube.

La recaudación incluye únicamente los pagos. Las exoneraciones conservan su
valor tarifado en una columna separada. El cierre agrega una sección independiente
con las cantidades y los importes de ambas coberturas, sin sumarlos a los
indicadores de asegurados. El conjunto del cierre se captura dentro de la
transacción del relevo: cambiar el estado de un recibo después del cierre no
reescribe el reporte ya cerrado.

Los recibos anteriores no se reclasifican mediante una migración automática:
no se presume que un registro antiguo esté pagado o exonerado. Los historiales y
las reglas normales de autorización, edición y facturación de asegurados se
conservan. No se cambió la versión del actualizador ni se publicó esta función.

| Archivos de implementación | Responsabilidad |
|---|---|
| `self_pay_billing.py` | Coberturas, tarifas, validación de cobros, totales, persistencia y consultas paginadas |
| `self_pay_history_dialog.py` | Historial propio, tareas en segundo plano, PDF, Excel y visor |
| `CALCULOS_QT.py` | Integración del formulario, controles, permisos, guardado, edición y actualización dirigida del esquema |
| `billing_field_policy.py` | Campos editables y protección de sala/ARS |
| `receipt_documents.py`, `pdf_engine/renderer.py`, `pdf_engine/template.html` | Estado y motivo en versiones del recibo y presentación del documento |
| `billing_close_store.py`, `billing_close_model.py`, `billing_close_report.py` | Captura transaccional del cobro, clasificación del cierre y exportación |
| `report_engine/query.py`, `report_engine/report_template.html` | Separación de asegurados y sección específica de cobros |

La tabla adicional `receipt_self_pay` tiene una clave primaria y foránea por
recibo, restricciones de cobertura/estado/motivo y RLS activado. El guardado de
cabecera, ítems, estado de cobro y versión documental comparte una transacción.
Las consultas del historial recuperan columnas explícitas, sin documentos ni
historial clínico completo. La interfaz impide tareas simultáneas y captura los
filtros antes de iniciar el trabajo fuera del hilo gráfico.

## PRUEBAS

Pruebas nuevas: `tests/test_self_pay_billing.py`,
`tests/test_self_pay_billing_postgres.py`, `tests/test_self_pay_billing_ui.py`,
`tests/test_self_pay_history_dialog.py`, `tests/test_self_pay_receipt_rendering.py`.

Pruebas actualizadas: `tests/test_billing_close_model.py`,
`tests/test_billing_close_store_postgres.py`, `tests/test_closure_schema_upgrade.py`,
`tests/test_receipt_attention_link_postgres.py`, `tests/test_turn_incident_postgres.py`.
Los cambios de fixtures representan recibos antiguos sin atribuirles estados de
cobro nuevos y añaden claves primarias exigidas por la nueva relación.

Se comprobaron coberturas válidas/incorrectas, pago/exoneración, motivos de 7/8/9
caracteres, cantidades 0/1/-1, importes negativos, centavos, subtotales incorrectos,
totales vacíos o inconsistentes, tarifas ausentes, tarifas históricas y vigentes,
páginas de 0/1/200/201 registros y desplazamientos negativos. También se probaron
búsquedas con contenido de inyección SQL y escape de metadatos HTML/Excel.

En PostgreSQL local desechable se verificaron creación, lectura, edición,
rollback, claves foráneas, restricciones, preservación del creador y fecha,
versiones documentales, separación de historiales, permisos, edición de Pagado a
Exonerado y captura inmutable del cierre. Los límites del turno incluyen su
inicio y excluyen su final. No se hizo QA destructivo contra producción.

En Qt real, fuera de pantalla, se comprobaron apertura y cierre de la ventana,
selección, búsquedas, controles habilitados/deshabilitados, estados vacíos,
paginación, eventos, errores, cancelación de exportaciones y prevención de tareas
duplicadas. Se revisaron visualmente el historial, el reporte y un recibo
exonerado con datos sintéticos. El recibo directo muestra su estado de cobro,
sin presentarse como pendiente de autorización ni listo para auditoría.

## RESULTADOS

Resultado final: **2.004 casos PASS, 0 FAIL y 1 skipped**: 1.944 pruebas y
60 subpruebas aprobadas. La única omisión es la prueba opcional de capacidad
descrita a continuación.

Los resultados definitivos de la suite completa, incluidas las repeticiones
necesarias después de restaurar el servidor local de QA, se registran en
`output/self-pay-full-regression-summary.json`. Cada grupo conserva su XML y log
en `output/self-pay-final-group-*.xml` y `output/self-pay-final-repeat-group-*.xml`.
La comprobación de cobertura y persistencia conserva resultados adicionales en
`output/self-pay-verified-coverage-tests.xml` y
`output/self-pay-final-closure-excel-tests.xml`.

Una prueba opcional de análisis de capacidad exige configurar explícitamente
una base real de prueba y se marca N/A para esta función. Las pruebas SQL de
cobros y cierres sí se ejecutaron contra PostgreSQL local. Dos comprobaciones
del esquema se repitieron correctamente después de recrear el servidor local;
también se repitió íntegramente el grupo que incluye cinco pruebas de historial
y cierre de sesión. Todas pasaron; las omisiones transitorias no se utilizan
como resultado final.

## COBERTURA

Herramienta: `coverage.py`, con cobertura de ramas habilitada. No se estimaron
porcentajes. Evidencia: `output/self-pay-verified-coverage.json` y
`output/self-pay-patch-coverage.json`.

- `self_pay_billing.py`: 100 % de líneas y ramas.
- `self_pay_history_dialog.py`: 100 % de líneas y ramas.
- `billing_field_policy.py`: 100 % de líneas y ramas.
- `billing_close_model.py`: 100 % de líneas y 98 % de ramas en el módulo completo;
  la clasificación nueva queda cubierta íntegramente.
- El conjunto medido del código nuevo/modificado: 96,71 % de líneas y 92,31 % de
  ramas. Este gate se evalúa sobre las líneas ejecutables del cambio, no sobre
  todo el módulo heredado `CALCULOS_QT.py`.

El detalle por archivo se conserva en el JSON: la integración en
`CALCULOS_QT.py` tiene 88,73 % de líneas y 79,31 % de ramas del cambio; las líneas
sin cobertura corresponden principalmente al constructor de la ventana principal.
La cobertura de la lógica nueva de cobros, tarifas y clasificación del cierre es
100 %. No se presenta la cobertura parcial del módulo principal como 100 %.

## CALIDAD

Ruff verificó formato en los módulos nuevos y los módulos pequeños formateados,
y lint diferencial en los archivos heredados. Mypy verificó los módulos nuevos
y comparó los diagnósticos existentes con la base 1.2.6. No se añadieron errores
de lint ni de tipos ni se bajaron configuraciones o umbrales.

Radon: complejidad máxima 9 en los módulos nuevos, 7 en la clasificación de
cobros del cierre y 7 en `build_close_snapshot`. La nueva protección del cambio
de tarifa se separó del evento del formulario. La lógica nueva comprobable se
mantiene fuera de las funciones grandes de persistencia/formulario heredadas.

Excepción técnica documentada: las funciones antiguas `save_receipt_with_items`,
`generate_pdf` y `load_recibo_for_editing` superan el umbral antes de este cambio.
Se preservaron sus interfaces y transacciones para evitar una refactorización
masiva del sistema hospitalario; la validación y los cálculos nuevos están
extraídos y probados en módulos independientes. Este trabajo no corrige la
deuda de complejidad ni los diagnósticos heredados generales.

Pylint no detectó duplicación entre bloques de seis o más líneas en los módulos
revisados. La tabla HTML compartida del reporte utiliza una macro para evitar
duplicación. Bandit conserva tres avisos nuevos B608 revisados: dos consultas
interpolan únicamente cláusulas construidas con nombres constantes permitidos,
con todos los valores externos parametrizados; el tercero concatena dos cadenas
constantes del esquema. No interpolan entradas del usuario. No se silenciaron
los avisos. Evidencia: `output/self-pay-security-review.json`.

Otros archivos de evidencia: `output/self-pay-lint-diff.json`,
`output/self-pay-types-diff.json`, `output/self-pay-complexity-final.json`,
`output/self-pay-duplication-final.txt`, `output/self-pay-bandit-final.json`.

## BUILD

Comando ejecutado:

```text
python -m PyInstaller --noconfirm --log-level WARN --distpath output/self-pay-preview-app --workpath build/self-pay-preview-app build_app.spec
```

Resultado: PASS. Log final: `output/self-pay-build-verified.log`.
La inspección del archivo del ejecutable comprobó los módulos de cobros, el
clasificador actualizado y las plantillas exactas del código fuente.
Evidencia: `output/self-pay-package-check.json`.

## QA

El ejecutable compilado pasó `--self-test-pdf`, `--self-test-report-viewer` y
`--self-test-reports`, todos con código de salida 0. El último genera también el
PDF y Excel de extranjeros/no asegurados. Se comprobó que la exoneración conserve
su importe en la columna correspondiente y tenga recaudación cero.
Evidencia: `output/self-pay-package-smoke.json`.

Se revisaron las cuatro pasadas: requerimientos y reglas de negocio; regresiones
de asegurados y turnos; responsabilidades, complejidad y duplicación; pruebas,
cobertura, análisis, compilación y documentos reales. Las revisiones de seguridad
incluyeron parametrización SQL, permisos, restricciones, transacciones, escape
HTML/Excel y generación de archivos locales.

## QUALITY GATES

| Gate | Estado y alcance |
|---|---|
| Funcionalidad | PASS — cobertura explícita, tarifa compartida, estados e historial propio |
| Unit tests y boundary cases | PASS — entradas válidas, inválidas, vacías y límites |
| Integration tests | PASS — PostgreSQL local, Qt, PDF, Excel y snapshot del cierre |
| Regression tests | PASS — suite completa y repeticiones afectadas |
| Coverage | PASS — gate agregado del cambio 96,71 % / 92,31 %; lógica nueva crítica 100 % |
| Formatter | PASS — módulos nuevos/pequeños y pruebas nuevas; convenciones heredadas preservadas |
| Lint y type checker | PASS — cero errores nuevos; deuda heredada documentada |
| Static analysis y seguridad | PASS — avisos Bandit revisados, sin problemas nuevos sin resolver |
| Complexity | PASS — módulos nuevos ≤9; excepciones de funciones heredadas documentadas |
| Duplication | PASS — sin bloques nuevos detectados por la comprobación ejecutada |
| Build y smoke | PASS — ejecutable compilado y tres autodiagnósticos |
| QA final | PASS — reglas, regresión y presentación con datos sintéticos |
| Publicación y prueba en producción | N/A — esta entrega es para evaluación local |
| Prueba opcional de capacidad de una base real | N/A — no requerida para el flujo de cobros |

## PROBLEMAS PENDIENTES

Esta función permanece en evaluación local. No se ha desplegado ni validado con
pagos reales del hospital. No se asignaron estados de pago a recibos antiguos
de forma automática. Continúan los diagnósticos y la complejidad heredados
documentados; no se introdujeron supresiones para ocultarlos.

**ESTADO FINAL: APROBADO PARA ENTREGA — evaluación local.**

Esta aprobación corresponde al alcance local y a las excepciones heredadas
documentadas, no a un despliegue en producción.
