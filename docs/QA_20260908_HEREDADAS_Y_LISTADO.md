# Anulación administrativa y coherencia del listado — 08/09/2026

## Implementación

- `billing_historical_cancellation.py`: anula una heredada pendiente mediante el servicio canónico de Admisión. Exige Administrador, identidad central, sesión vigente, mismo origen operativo, turno anterior y motivo de al menos ocho caracteres. Rechaza recibos vinculados (también eliminados), herencias completadas y reservas activas. La validación y la anulación comparten una transacción con exclusión frente a escrituras de recibos/reservas. Un error revierte la operación.
- `CALCULOS_QT.py`: botón **Anular heredada pendiente** en Validación y en el Historial de Facturación; visible únicamente al Administrador. Solicita motivo y confirmación, ejecuta fuera del hilo gráfico y protege el cierre mientras termina. Refresca historial y pendientes después de confirmar. El resumen y el arrastre al siguiente cierre descartan proyecciones anuladas o reclasificadas.
- `admission_listing.py`: clasificación compartida del listado. URGENCIA y CONSULTA se muestran con ese tipo; las emergencias mantienen su especialidad clínica.
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: los cambios de tipo invalidan la revisión del Excel; se reconstruye también al editar/anular urgencias y consultas. Una fuente canónica vacía puede retirar la última fila obsoleta. El contador muestra Total atenciones e incluye urgencias y consultas; la recuperación desde Excel reconoce esas categorías.
- `admission_v15_adapter.py`: normalización del tipo para contadores y actualización del Excel tras cambios del historial.
- Pruebas añadidas/actualizadas: `tests/test_billing_historical_cancellation.py`, `tests/test_current_turn_dataset_20260822.py`, `tests/test_turn_excel_post_commit.py`.

Se conserva la identidad central y el historial clínico mediante anulación auditada; no se borran físicamente las atenciones ni los recibos. La acción anterior de descartar de la lista mantiene sus permisos existentes. No se han realizado anulaciones ni cambios de datos en producción. No se modificaron los directorios antiguos de candidatos de publicación.

## Pruebas y resultados

| Validación ejecutada | Resultado |
|---|---|
| Regresiones iniciales del listado antes de corregir | RED: seis fallos reproducidos |
| Suite relacionada de listado, Excel, eventos y reportes | PASS: 129 passed |
| Módulo de anulación, PostgreSQL desechable y Qt | PASS: 50 passed |
| Suite completa final con coverage | PASS: 1385 passed, 22 skipped, 0 failed; 60 subtests passed |
| Integración adicional con servidor PostgreSQL 17 desechable | PASS: 22 passed, 0 failed, 0 skipped |
| Apertura de aplicación empaquetada, Admisión, Historial y configuración | PASS, exit 0 |
| Exportación PDF desde ejecutable | PASS, exit 0 |
| Exportación de reportes PDF/XLSX desde ejecutable | PASS, exit 0 |

La ejecución adicional cubrió las omisiones por falta del PostgreSQL local, con un caso solapado con la suite completa. Queda fuera de alcance la prueba optativa de capacidad de base de datos, que exige `RUN_REAL_CAPACITY_INTEGRATION=1`. Dos avisos de deprecación existentes: PyPDF2 y ttkbootstrap.tooltip.

Se probaron permisos, ausencia de identidad/selección/conexión, sesión cambiada, motivo vacío/7/8 caracteres, reserva vigente/expirada, recibos, atención ya anulada, turno actual, origen distinto, rollback, escritura concurrente, visibilidad en ambos diálogos y cierre durante operación. Las pruebas del servicio usan SQL real para sus guardas y transacción; sustituyen la operación canónica de cancelación para inyectar confirmación/fallos. Las pruebas existentes del servicio canónico y de tombstones se ejecutaron en la suite completa. No se afirma una prueba física entre dos estaciones del hospital.

El Excel se guardó y reabrió en un directorio temporal, comprobando las transiciones EMERGENCIA → URGENCIA → CONSULTA → EMERGENCIA y la retirada de la última fila tras anulación. Se comprobó que los pendientes anulados/reclasificados no se arrastren al siguiente cierre.

## Cobertura y calidad

Medición real con coverage.py 7.15.4, ramas habilitadas, sobre líneas ejecutables añadidas/modificadas según el diff de Git y los dos módulos nuevos:

- Líneas: **157/161 = 97,52 %**.
- Ramas: **59/64 = 92,19 %**.
- Servicio crítico nuevo de anulación: **49/49 líneas y 24/24 ramas = 100 %**.
- Estos porcentajes corresponden al cambio; no representan la cobertura total del código heredado.
- Ruff: PASS en módulos nuevos y prueba nueva. Comparación de módulos heredados: **0 errores nuevos**; persisten 327 avisos previos en CALCULOS_QT.py y 85 en el módulo V15. El adaptador tiene 0.
- Formato: PASS con `ruff format --check` en los dos módulos nuevos y la prueba nueva; en los archivos heredados se conservaron sus convenciones. `git diff --check`: PASS.
- Tipos: PASS, `mypy --follow-imports=silent --check-untyped-defs admission_listing.py billing_historical_cancellation.py`. No se afirma comprobación de tipos completa de la aplicación heredada.
- Análisis estático/sintaxis: Ruff y `py_compile` de los cinco módulos de aplicación modificados, PASS respecto a nuevos problemas.
- Radon: complejidad máxima de funciones nuevas **10**; búsqueda de contexto 3, manejador de anulación 10, guardas separadas 8/4.
- jscpd: **0 bloques duplicados que intersecten líneas modificadas**, 0 clones en los módulos nuevos; 0,987 % de duplicación detectada en el conjunto analizado. No se cambió configuración para ocultar resultados.

## Build y QA

Build ejecutado:

```powershell
python -m PyInstaller --noconfirm --distpath D:/SIGEH_QA_20260908/dist --workpath D:/SIGEH_QA_20260908/build build_app.spec
```

**PASS**. Ejecutable local: `D:/SIGEH_QA_20260908/dist/SIGEH/CALCULOS_QT.exe`.

Se comparó el código compilado incluido en el ejecutable/PYZ con los cinco módulos fuente modificados: todas las comparaciones coinciden. Smoke tests con datos aislados y Qt offscreen: `--check-v15-package`, `--self-test-pdf` y `--self-test-reports`, todos exit 0 y con artefactos generados. La instancia PostgreSQL desechable se apagó al terminar.

Revisión de seguridad del cambio: permiso comprobado también en servicio, UUID validado, consultas parametrizadas, motivo obligatorio, confirmación explícita, exclusión de recibos y claims bajo transacción, límites de espera y rollback. Los únicos identificadores interpolados del fragmento SQL son constantes internas del código. No se añadieron credenciales ni se usaron pacientes reales como fixtures.

Cuatro pasadas realizadas: funcionalidad contra los requisitos; regresiones y persistencia; responsabilidades/duplicación/complejidad; QA, métricas, build y smoke.

## Quality gates

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias, regresión, integración aplicable, límites | PASS |
| Cobertura del cambio y lógica crítica | PASS |
| Formato del código nuevo / convenciones heredadas, lint sin errores nuevos | PASS |
| Análisis estático, tipos de módulos nuevos | PASS |
| Complejidad nueva y duplicación del cambio | PASS |
| Build y smoke del ejecutable | PASS |
| Revisión de seguridad aplicable y QA final local | PASS |
| Prueba optativa de capacidad de BD | N/A — no se cambió capacidad, mantenimiento ni reclamación física |
| Instalación y convergencia física en equipos del hospital | NO VERIFICADO — no forman parte de este QA local |
| Publicación del cambio | NO REALIZADA |

## Evidencia y límites

Evidencia en `D:/SIGEH_QA_20260908/`: `full-suite.xml`, `integration-suite.xml`, `coverage.json`, `changed-coverage.json`, `lint-comparison.json`, `duplication/`, `build.log`, `build-source-verification.json`, `package-check.json`, `smoke-results.json`, `smoke.pdf`, `report-smoke/`.

No hay causa raíz productiva confirmada para una atención específica de las capturas. Las correcciones están implementadas y verificadas localmente; aún no están publicadas ni instaladas en las estaciones del hospital. La publicación requiere preparar el paquete de distribución con este build.

**ESTADO FINAL: APROBADO PARA ENTREGA** — código y build local verificados; publicación pendiente.
