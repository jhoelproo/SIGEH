# Preparación turno, Excel y borrador — 2026-09-06

**ESTADO FINAL: NO APROBADO PARA ENTREGA.**

Preparación local en `sigeh-v110-release`. No se publicó, instaló ni cambió versión. No se modificaron datos productivos. A petición del usuario se efectuaron lecturas centrales acotadas para la fila 399, con transacciones PostgreSQL de solo lectura, timeout y rollback. No se copiaron nombres, documentos ni payloads clínicos a los informes de QA.

## IMPLEMENTACIÓN

Archivos modificados en esta continuación:

- `excel_artifact.py` (nuevo): temporal único junto al destino, validación CRC y reapertura/lectura XLSX, fsync, reemplazo y conservación de `.last-valid.xlsx`. Bloqueo cooperativo entre hilos y procesos mediante `.lock` persistente. No cierra handles ajenos. Si el destino ya está corrupto, conserva evidencia y respaldo; no lo sustituye automáticamente.
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: guardado común en guardado normal, latest, versionados y copia canónica; validación del origen antes de reutilizarlo o completar la cola. Se retira la recreación automática destructiva. En el diálogo, la corrección administrativa es una opción explícita solo visible para Admin; la hora no elige la operación. La vista previa cambia de representante según la operación. Un relevo confirmado usa la configuración operacional confirmada para crear el espejo local después del commit, sin volver a rechazarlo por horario.
- `admission_source/facturacion_tabs.py`: mismo guardado y retirada del fallback destructivo en la ruta heredada.
- `billing_admission_edit.py`: regla pura de revalidación de la misma atención central. Exige UUID válido coincidente, fuente/ID, fecha, ARS, cobertura y tipo sin cambios, estado ACTIVA y readiness LISTA; excluye sin seguro de esta actualización limitada.
- `CALCULOS_QT.py`: cuando la revalidación satisface esa regla, actualiza nombre y snapshot/claim de la atención sin reiniciar cargos, autorización o versión del recibo. Es una revalidación explícita, no una actualización silenciosa desde la ficha maestra. Cambios de identidad o condiciones de facturación conservan el flujo anterior de revisión.
- `build_app.spec`: incluye el helper Excel.
- Pruebas nuevas: `test_excel_artifact.py`, `test_excel_preservation.py`, `test_turn_nominal_boundaries.py`, `test_turn_operation_dialog.py`, `test_billing_draft_revalidation.py`, `test_identity_eligibility_trace.py`, `test_patient_replica_refresh.py`.
- Pruebas existentes ajustadas: `test_turn_excel_post_commit.py` utiliza un XLSX real para el bloqueo Windows; `test_admission_billing_consistency.py` define explícitamente el estado vacío del paciente en tres fixtures.

Se preservaron las correcciones previas de recibos heredados, exclusión de anuladas/Urgencia/tombstones/claims ajenos/otros recibos, edición por identidad maestra y UUID opcional NULL. No se modificaron comandos centrales ni su contrato de idempotencia. Excel sigue fuera de las transacciones SQLite del cambio de turno.

## TRAZADO ADMINISTRATIVO

El adapter transmite `administrative_override=True` y `allocate_central_turn_id=True` a `admin_set_admission_turn`, que delega en `transition_primary_turn`. Esta última utiliza `_allocate_next_central_turn_id` cuando el flag está activo. La operación conserva representante/PRIMARY pero puede asignar otra identidad de turno. La confirmación del diálogo ahora lo explica. No se cambió esa semántica ni se afirma que haya ocurrido en el incidente productivo.

## DIAGNÓSTICO DE FILA 399

El usuario identificó la fila visible 399 y el incidente del 05/09/2026 en la PC del hospital. Se consultó la configuración central disponible en la raíz del proyecto, sin imprimir su URL ni credenciales.

Resultados de solo lectura:

1. La proyección contiene dos IDs locales 399 de fuentes diferentes; ninguno coincide con el nombre proporcionado.
2. Las coincidencias exactas por nombre corresponden a IDs diferentes, fechas anteriores al incidente y registros anulados/eliminados. No tienen recibo activo ni claim vigente. No se atribuyen al incidente.
3. No se encontró coincidencia activa por los tokens de nombre consultados ni un registro nativo 399.
4. El contexto operacional vigente consultado era turno 3952, fuente distinta de las coincidencias históricas.
5. En los eventos recibidos entre 05/09/2026 04:00Z y 07/09/2026 04:00Z no hubo coincidencias por esos tokens de nombre ni por número 399 en secuencia/local_attention_id/attention_id/id.

**Conclusión limitada:** no quedó identificada la atención de la captura en esta base central. No se demostró desajuste de turno ni fallo de sincronización. Hace falta resolver la fila en la réplica local de la PC del hospital y comprobar el destino central de esa instalación. No se reactivó, movió ni alteró ninguna atención para hacerla aparecer.

Evidencia sin nombres/documentos: `identity-399-readonly.json`, `identity-399-sync-readonly.json`, `identity-399-number-events.json` en el directorio de resultados. La búsqueda por nombre solo sirve para desambiguar; nunca sustituye un UUID canónico.

## PRUEBAS

Se reprodujeron antes de corregir:

- escritura parcial que truncaba el destino;
- apertura de corrupción que invocaba recreación;
- versionado corrupto tratado como vacío;
- clasificación del diálogo por reloj a las 20:00, 20:01 y medianoche;
- revalidación de la misma atención que reiniciaba el borrador o reaplicaba controles innecesariamente.

Después del cambio pasan. Se ejecutan hilos/procesos, bloqueo real Windows, errores de disco/reemplazo/backup, ZIP/XLSX/CRC inválidos, conservación del último válido, formato y cola post-commit. Los límites 19:59/20:00/20:01/medianoche se verifican sin mutación central. La caracterización de reinicio reconstruye objetos de estado; no reinicia una instalación hospitalaria.

Qt real en modo offscreen verifica visibilidad de la opción por rol, cambio de vista previa, no superposición con Aplicar, actualización de nombre y preservación física de celdas, autorización y etiqueta de versión. Se revisó la captura del diálogo y corrigió el recorte del texto nuevo. El harness no representa el tema completo de la aplicación.

La prueba de dos réplicas usa dos SQLite físicos temporales y una central simulada: la segunda replica converge tras `verify_with_cloud`, conserva identidad/revisión y persiste al reabrir. No se declara prueba de dos PCs reales ni sincronización automática inmediata.

## RESULTADOS

Directorio de evidencia: `D:/SIGEH_QA_TURN_EXCEL_20260906/results`.

| Ejecución final | PASS | FAIL | SKIPPED | Evidencia |
| --- | ---: | ---: | ---: | --- |
| Suite ampliada, importador/launcher separados | 1229 | 0 | 23 | `final-suite.xml`, 60 subtests PASS |
| Integraciones PostgreSQL que estaban omitidas | 22 | 0 | 0 | `final-integration.xml` |
| Importador aislado | 8 | 0 | 0 | `final-importer.xml` |
| Launcher aislado sin variable de conexión | 5 | 0 | 0 | `final-launcher.xml` |
| Suite relacionada con cobertura | 316 | 0 | 0 | `continuation-related.xml` |
| Regresiones focales finales | 101 | 0 | 0 | `continuation-final-focal.xml` |
| Diálogo final | 35 | 0 | 0 | `dialog-final.xml` |
| Historial/eligibilidad sintética | 2 | 0 | 0 | `identity-trace.xml` |
| Réplicas SQLite | 1 | 0 | 0 | `patient-replicas.xml` |

Los conteos se solapan; no se suman como pruebas únicas. Veintidós de las 23 omisiones de la suite se ejecutaron con PostgreSQL desechable posteriormente. Subsiste la prueba de capacidad que requiere activación explícita. Las instancias PostgreSQL creadas para QA se cerraron.

Se conservan logs de fallos intermedios: una ejecución anterior tuvo un fallo de launcher por la URL inválida del entorno; otra tuvo tres fixtures incompletos y una aserción de texto fuente dividida entre literales. Las ejecuciones finales indicadas pasan. No se modificaron tests para ocultar una regresión funcional.

El aborto histórico del importador no se reprodujo aislado (8 PASS), pero no se demuestra que su interacción con toda la suite en un único proceso haya quedado resuelta. Por ello el importador se ejecutó separadamente.

## COBERTURA

`continuation-coverage.json`, coverage.py real con ramas:

- `billing_admission_edit.py`: 44/44 líneas, 16/16 ramas, **100 % / 100 %**.
- `excel_artifact.py`: 67/72 líneas, 9/10 ramas, **93,06 % / 90 %**.
- No se añadieron exclusiones ni se redujeron umbrales. Las cinco líneas y la rama no ejecutadas de Excel son el bloqueo POSIX; QA se hizo en Windows.
- PASS para gates generales 90/85. El objetivo crítico 95 % de líneas no se alcanza si se incluye POSIX.
- Cobertura completa del diff de monolitos: NO VERIFICADO; estas cifras no son cobertura global de la aplicación.

## CALIDAD

- Ruff y formatter de helpers/pruebas nuevas: PASS; 9 archivos formateados.
- Comparación Ruff con HEAD: V15 85→85; legado 242→242; CALCULOS_QT 327→327. **0 diagnósticos nuevos**, normalizando solo referencias de número de línea dentro del mensaje F811 para comparar errores desplazados. `final-lint-comparison.json`.
- Mypy `--follow-imports=silent --ignore-missing-imports --check-untyped-defs excel_artifact.py billing_admission_edit.py`: PASS en ambos helpers. Tipos integrales de monolitos: NO VERIFICADO.
- `py_compile` de los archivos de aplicación modificados/spec: PASS.
- Radon: máximo 4 en Excel, 10 en la nueva regla de borrador. Gate de funciones nuevas PASS. El gate de complejidad heredada integral del informe previo sigue pendiente.
- jscpd de ambos helpers: 0 duplicados, 0 %. No es una nueva medición de toda la aplicación.
- No se introdujeron prints/TODOs de producción, shell/SQL dinámico nuevo ni acceso remoto en los helpers.

## BUILD

PASS, salida 0:

```text
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath D:/SIGEH_QA_TURN_EXCEL_20260906/final-dist --workpath D:/SIGEH_QA_TURN_EXCEL_20260906/final-build build_app.spec
```

`final-build.log` conserva avisos de imports opcionales. Builds incrementales anteriores conservaron una revisión previa; se descartaron como evidencia final. El build limpio final se comprobó extrayendo bytecode: `CALCULOS_QT`, V15, `excel_artifact` y `billing_admission_edit` coinciden con las fuentes finales (`final-source-match.json`).

SHA-256 `CALCULOS_QT.exe` de QA:

```text
66A71D4937D3CC8760F159410B065636053597E0ABF834BE19B7EEE9D68957C4
```

No hay ZIP, publicación ni instalación autorizada.

## QA

Sobre `final-dist/SIGEH/CALCULOS_QT.exe`, perfil temporal, directorio de trabajo ajeno al repositorio y URL inválida loopback:

- `--check-v15-package`: PASS, salida 0; widget, Historial y configuración construidos.
- `--self-test-pdf`: PASS, salida 0.
- `--self-test-reports`: PASS, salida 0; tres PDF y dos XLSX sintéticos.

Evidencia: `final-smoke.json` y `D:/SIGEH_QA_TURN_EXCEL_20260906/final-smoke`.

Cuatro pasadas realizadas: funcionalidad contra los hallazgos; regresiones de contratos y rutas afectadas; clean code y métricas; QA/pruebas/build/smoke. La revisión de seguridad del cambio conserva autorización, filtros y claims. La auditoría de seguridad integral no se ejecutó.

## QUALITY GATES

| Gate | Estado |
| --- | --- |
| Funcionalidad preparada de Excel/diálogo/revalidación | PASS en pruebas indicadas |
| Incidente real 399 completamente diagnosticado | NO VERIFICADO — falta fila/identidad de la réplica hospitalaria |
| Unitarias/regresiones ejecutadas | PASS |
| Integración PostgreSQL ejecutada | PASS |
| Suite monolítica completa | NO VERIFICADO — importador/launcher separados y capacidad explícita pendiente |
| Boundary cases | PASS |
| Cobertura helpers general | PASS |
| Cobertura de todo el diff/objetivo crítico Excel 95 % | NO VERIFICADO / objetivo no alcanzado |
| Formatter/lint nuevos | PASS |
| Tipos helpers/análisis sintáctico | PASS |
| Tipos/análisis integral | NO VERIFICADO |
| Complejidad/duplicación nuevas | PASS |
| Complejidad heredada integral | NO VERIFICADO en esta continuación; gate anterior pendiente |
| Build final y smoke empaquetado | PASS |
| Seguridad del cambio revisado | PASS; auditoría integral NO VERIFICADO |
| Convergencia en dos PCs hospitalarias | NO VERIFICADO |
| QA integral para entregar/instalar | NO VERIFICADO |

## PROBLEMAS PENDIENTES

1. Resolver la identidad de la fila 399 del 05/09/2026 en la base local de la PC hospitalaria y verificar que esa instalación usa el mismo destino central. No trasladar ni reactivar pacientes para compensarlo.
2. Verificar físicamente edición maestra, revalidación del borrador y convergencia entre estaciones. Se conserva la revisión cuando cambian condiciones de facturación; no hay actualización silenciosa de cargos ni cobertura.
3. El bloqueo Excel coordina escritores que usan el helper. No garantiza orden semántico de snapshots distintos ni atomicidad conjunta del JSON de estado/latest; no controla programas externos ni demuestra la causa del archivo original corrupto. Un backup válido previo solo existe después de un reemplazo previo exitoso.
4. Cerrar cobertura completa de monolitos, objetivo crítico de portabilidad y gates heredados antes de aprobar entrega. No se afirma resuelto el aborto histórico del importador por pasar aislado.
5. Los artefactos compilados son exclusivamente QA. No se tocaron datos productivos salvo las lecturas autorizadas descritas.

**ESTADO FINAL: NO APROBADO PARA ENTREGA.**
