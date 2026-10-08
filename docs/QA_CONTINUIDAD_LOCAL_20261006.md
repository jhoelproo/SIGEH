# Validación de continuidad local — 6 de octubre de 2026

**ESTADO FINAL: APROBADO PARA ENTREGA**

La aprobación corresponde al código y a la compilación local de prueba. No se
publicó una nueva versión ni se aplicó la migración en Supabase de producción.
Base de comparación: `6fe2f557a12a44875fcd6a33ac1f7afe06531ab5`.

## IMPLEMENTACIÓN

Archivos de aplicación:

- `CALCULOS_QT.py`: guardado local previo al envío, publicación idempotente,
  disponibilidad del catálogo sin conexión, historial local y pausa compartida
  entre sincronización, guardado y verificación final de Admisión.
- `local_receipts.py`: cola SQLite, validación de importes, estados y caché.
- `receipt_command_sync.py`: UUID y huella centrales, bloqueo transaccional y
  recuperación de confirmaciones previamente guardadas.
- `receipt_continuity.py`: rutas locales, catálogo persistente y copia PDF.
- `local_receipts_dialog.py`: consulta y apertura de copias propias del usuario.
- `network_retry.py`: pausa por restricción y reintentos de red espaciados.
- `admission_hybrid.py`: interrupción del envío al detectar restricciones.
- `admission_v15_adapter.py`: coordinación de reintentos y respaldo periódico.
- `admission_source/emergency_core/backup.py`: verificación y recuperación del
  respaldo diario, con programación de comprobaciones y reintento ante fallos.
- `pdf_engine/renderer.py`, `pdf_engine/template.html`, `pdf_engine/styles.css`:
  presentación del documento local y su UUID completo.
- `supabase/migrations/20261006124609_durable_local_receipt_requests.sql`:
  columnas e índice único para confirmación idempotente.

Se agregaron nueve archivos de pruebas: `test_continuity_admission_cycle.py`,
`test_continuity_backups.py`, `test_continuity_restrictions.py`,
`test_continuity_integration_paths.py`, `test_local_receipts.py`,
`test_local_receipt_postgres.py`, `test_receipt_command_sync.py`,
`test_receipt_continuity.py` y `test_receipt_continuity_worker.py`.
Se corrigió el importe de sala de un fixture en `test_receipt_uuid.py` para
mantener coherente su total y seguir verificando el error de UUID opcional.

Se preservan la autorización de Admisión y su plazo de escritura local, los
permisos existentes, la edición central, la numeración definitiva y las reglas
de facturación. Facturación sin conexión requiere un rol previamente autorizado
a omitir verificación y tarifas ya guardadas; no habilita a todos los roles.

## PRUEBAS

- Unitarias: validación de actor, paciente, fechas e importes; exclusión de
  credenciales; UUID y huella; permisos; presentación y señales del trabajador.
- Integración: SQLite real, cierre y reapertura, PostgreSQL local desechable,
  confirmación central y recuperación tras perder una respuesta después del commit.
- Admisión: corte antes del envío y después de guardar centralmente, reapertura
  y replicación entre estaciones conservando la identidad de la atención.
- Respaldos: restauración de atención y evento pendiente; restauración de cola
  y catálogo; rechazo y reemplazo de una copia diaria corrupta.
- Límites: cola con 101 comandos procesada en lotes de 100; cantidades inválidas,
  totales vacíos, negativos y no finitos; pausa comprobada justo antes y al llegar
  a los 900 segundos; reintentos limitados a 300 segundos para fallos de red.
- Regresión: toda la colección aplicable en 14 procesos agrupados, con aislamiento
  de módulos gráficos que comparten fixtures de Qt.

La prueba que reproducía solicitudes repetidas bajo restricción falló antes de
la corrección y pasó después. También se cubrió el camino de verificación final
del paciente vinculado, que debe respetar la misma pausa.

## RESULTADOS

Resultado de la regresión completa, según JUnit:

| Resultado | Casos |
|---|---:|
| Ejecutados/registrados | 2.588 |
| Passed | 2.587 |
| Failed | 0 |
| Errors | 0 |
| Skipped | 1 |

El total incluye subpruebas registradas por JUnit. Dentro de la colección se
ejecutaron 118 casos específicos de continuidad. La única omisión es
`test_real_capacity_dry_run_preserves_operational_counts`, que requiere
`RUN_REAL_CAPACITY_INTEGRATION=1`; no pertenece a la nueva funcionalidad.

Después de la regresión se aplicó el formatter únicamente al archivo de pruebas
del trabajador. Sus 25 casos se repitieron y pasaron. El código de aplicación no
cambió durante la regresión definitiva.

## COBERTURA

Medición real con Coverage.py, incluyendo ramas, combinando los 14 grupos y la
prueba gráfica local. El alcance es todo el código de los módulos nuevos y las
sentencias semánticamente modificadas de los módulos heredados; no representa
la cobertura global de toda la aplicación heredada.

| Alcance | Lines | Branches |
|---|---:|---:|
| Cinco módulos nuevos | 100% | 100% |
| Cambios en `CALCULOS_QT.py` | 99,15% | 97,50% |
| Cambios en Admisión y respaldos | 100% | 100% |
| Cambio en preparación del PDF | 100% | N/A: sin rama nueva |
| Total nuevo/modificado | **99,66%** | **98,65%** |

La persistencia, idempotencia y pausa nuevas superan el objetivo crítico de
95% de líneas y 90% de ramas. No se redujeron thresholds ni se excluyeron caminos
de error para obtener estos resultados.

## CALIDAD

- Formatter: PASS en los cinco módulos nuevos y los nueve archivos de pruebas;
  `git diff --check`: PASS. Los archivos heredados conservan sus convenciones.
- Ruff: PASS en módulos y pruebas nuevos; comparación del código heredado:
  cero errores nuevos.
- Mypy: PASS en módulos nuevos y cero errores nuevos en la comparación heredada.
- Análisis estático/seguridad con Bandit: PASS para código nuevo; cero hallazgos
  nuevos. Hay 105 hallazgos previos en el conjunto heredado, no ocultados.
- Radon: máxima complejidad 8 en módulos nuevos y 10 en funciones nuevas del
  módulo principal; no aumentan las funciones heredadas que ya superaban 10.
- jscpd 5.4.0: duplicación 2,20% frente a 2,23% inicial; cero bloques nuevos;
  se mantienen las 1.233 líneas duplicadas heredadas en diez archivos analizados.

Excepción técnica de complejidad: `save_receipt_with_items` conserva la
transacción heredada de reservas, auditoría, numeración y snapshots para evitar
una refactorización fuera del alcance. Su complejidad baja de 190 a 189. La
integración nueva se extrajo en helpers pequeños; no se declara esa función
heredada como ajustada al objetivo absoluto de 10.

## BUILD

Comando ejecutado:

```powershell
python -m PyInstaller --noconfirm --distpath output/continuity-app --workpath output/continuity-build build_app.spec
```

Resultado: **PASS**. Se comparó el código compilado de 13 módulos del paquete con
las fuentes actuales: **13/13 PASS**.

Compilación: `output/continuity-app/SIGEH`. Debe conservarse la carpeta completa,
incluidos `SIGEH.exe`, `CALCULOS_QT.exe` y sus dependencias. Es una compilación de
prueba; no cambia la versión publicada v1.2.11.

## QA

Cuatro pruebas del ejecutable: **4/4 PASS**.

- `SIGEH.exe --self-test`.
- `CALCULOS_QT.exe --self-test-pdf`.
- `CALCULOS_QT.exe --self-test-report-viewer`.
- `CALCULOS_QT.exe --self-test-reports`.

GUI: apertura de Facturación sin conexión con tarifas guardadas, disponibilidad
por permisos, consulta del historial local, selección y apertura de la copia:
**PASS**, con cero solicitudes centrales en esa prueba.

PDF: revisión visual de paciente ficticio, NSS largo, importes, etiqueta
preliminar e identificador local completo: **PASS**.

Seguridad: consultas parametrizadas, identificación normalizada para rutas,
comprobación de propiedad del recibo y revalidación del usuario central:
**PASS**. No se alteraron pacientes reales ni se hizo QA destructivo en producción.

Las pérdidas de conexión se simularon en pruebas reproducibles; no se cortó
físicamente la electricidad ni se desconectó la red del hospital.

## QUALITY GATES

| Gate aplicable a esta entrega | Estado |
|---|---|
| Funcionalidad | PASS |
| Unit tests | PASS |
| Regression tests | PASS |
| Integration tests | PASS |
| Coverage | PASS |
| Boundary tests | PASS |
| Formatter | PASS: alcance nuevo y convenciones heredadas |
| Lint | PASS: cero errores nuevos |
| Static analysis | PASS: cero hallazgos nuevos |
| Type checker | PASS: módulos nuevos y comparación heredada |
| Complexity | PASS: código nuevo; excepción heredada documentada |
| Duplication | PASS |
| Build | PASS |
| Smoke test | PASS |
| Security checks | PASS: alcance nuevo |
| QA final | PASS |

Las cuatro pasadas finales comprobaron funcionalidad, regresión, clean code y
QA. Las evidencias se conservan en `output/continuity-verified-group-*.xml`,
`continuity-final-test-counts.json`, `continuity-coverage-summary.json`,
`continuity-quality.json`, `continuity-duplication.json`,
`continuity-build-final.log`, `continuity-package-verification.json` y
`continuity-smoke.json`, dentro de `output`.

## PROBLEMAS PENDIENTES Y LÍMITES

- NO VERIFICADO: instalación y operación física de esta compilación en las tres
  computadoras del hospital; esta validación corresponde al entorno local de QA.
- NO VERIFICADO: reducción porcentual del egreso real con esta compilación. Los
  cambios evitan tráfico futuro innecesario; no eliminan el consumo acumulado del
  ciclo ni garantizan permanecer dentro del plan gratuito.
- Producción: no se publicó esta compilación ni se aplicó allí la migración nueva.
  La preparación del esquema debe existir antes de sincronizar solicitudes locales.
- Un conflicto central conserva el documento en revisión; todavía no hay editor
  ni botón de reenvío manual de esos comandos.
- Los respaldos son locales y se comprueban con la aplicación activa. La pérdida
  del disco requiere además una copia externa; no se agregó un servicio externo.
- La copia de cobro sin conexión permanece preliminar y no acredita pago central.

Guía operativa: [CONTINUIDAD_LOCAL.md](CONTINUIDAD_LOCAL.md).
