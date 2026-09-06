# Admisión, Facturación y contexto de generación: cierre local

> Actualización posterior, 2026-09-05: el usuario autorizó publicar con los
> pendientes conocidos. Se publicó v1.1.3 en
> https://github.com/jhoelproo/SIGEH/releases/tag/v1.1.3, commit
> `b3e2332f95d6c201a2a4c7932ec93bf5d32c5e88`. La auditoría que sigue conserva
> los resultados de la validación previa; no se convierten en PASS los gates
> pendientes. Véase `output/release-v1.1.3/PUBLICATION_REPORT.md` para el cierre
> de publicación. Este informe local con trazado real no se subió a GitHub.

Fecha de cierre: 2026-09-05. Base: `e63464235a69c1c3d4f18359f93ea1287e8e9685`.
Rama: `codex/admission-billing-consistency`.

**ESTADO FINAL: NO APROBADO PARA ENTREGA.**

Los cambios y la validación automatizada local están preparados. No se han
realizado los gates físicos de dos estaciones; tampoco se declara aprobado el QA
visual integral de reportes. No hubo publicación, push, movimiento de tags,
despliegue, cambio de versión ni modificación de datos productivos.

## 1. Alcance y preservación

Contrato revisado: adjunto `6c7c7556-b9bb-444d-bb35-5c641dcff170/pasted-text.txt`.
Se limita a pendientes obsoletos, propiedad de reservas de Facturación, identidad
central compartida y validación operacional al generar hojas. Se conserva la
autoridad PostgreSQL online, el modo offline autorizado, el historial existente,
sus UUID, turno, representante, PRIMARY/SECONDARY y reglas de cobertura/ARS.

No se ejecutaron SEED, MERGE, reset, eliminación de SQLite, limpieza de outbox,
reasignación de pacientes, relevo ni transferencia de PRIMARY. La lectura real
del caso fue en transacción PostgreSQL read-only. Las escrituras de prueba se
hicieron exclusivamente en bases temporales de PostgreSQL 17, limitadas a
loopback, y archivos temporales locales. Los servidores creados se detuvieron
al finalizar. No se hizo QA destructivo contra producción.

## 2. Incidente A: pendientes obsoletos

Defectos comprobados en código:

- `get_projected_billable_attention()` no exigía EMERGENCIA al revalidar una
  selección; otras rutas sí lo exigían.
- La elegibilidad final y la cola no aplicaban uniformemente `ars.billing_enabled`.
- Algunas consultas elegían la última sesión ACTIVE sin restringirla al epoch
  de producto vigente; otras ya utilizaban esa restricción.

Cambio: las rutas de consulta, reserva y guardado reutilizan
`CURRENT_OPERATIONAL_SHIFT_SQL`, la regla de ARS y la identidad global del recibo.
La reserva y el guardado vuelven a comprobar estado, tipo, turno/herencia
explícita, cobertura, recibos, descartes y propiedad. No se elimina una atención
para hacer desaparecer un pendiente. Una anulada no tombstoned puede seguir
visible en Historial con su estado, pero no es un candidato; un tombstone se
excluye del historial normal y de la cola.

Reconciliación: relectura central e invalidación del caché de validación; no se
aplicó una reparación masiva de filas. Las pruebas repiten lecturas tras cambios
de estado para comprobar que no reaparece el candidato excluido.

## 3. Incidente B: «otra estación»

Defectos comprobados: una sesión distinta podía parecer ajena aun siendo el
mismo operador en su estación; no existía liberación de la reserva al cancelar
el formulario, y el override privilegiado podía apropiarse de reservas vivas.

Modelo conservado: `admission_billing_claims`, clave
`(source_instance_id, attention_id)`, sesión, estación, operador, timestamps,
recibo y procesamiento. Se conserva el TTL existente de 20 minutos.

Ahora:

- Claim ajeno vivo bloquea incluso a Admin. Claim expirado puede recuperarse.
- La misma estación/operador puede reanudar la selección mediante UPSERT atómico.
- Recibo/procesamiento confirmado impide reapropiarse de ese claim.
- Cancelación, sustitución, rechazo y cierre de sesión programan la liberación
  fuera del hilo GUI. Se actualiza `expires_at`; no se borra su fila de auditoría.
- La liberación exige sesión y `claimed_at` exactos: una liberación atrasada no
  invalida una reserva readquirida después.
- Un fallo de conexión se propaga como error, no como «otra estación» o lista vacía.
- Si el formulario está mal formado, el logout limpia la sesión igualmente,
  registra `CLAIM_RELEASE_SNAPSHOT` y no asume propiedad de ninguna reserva.
  En caída del proceso/red, el TTL sigue siendo el mecanismo de recuperación.

No hay evidencia retrospectiva suficiente para atribuir cada popup hospitalario
a un claim concreto. La causa de una reserva específica debe confirmarse por su
UUID, sesión, expiración, procesamiento y recibo, no por el texto del popup.

## 4. Incidente C: caso real de la captura

Trazado read-only de la atención mostrada como ID 325:

| Dato | Evidencia observada durante el diagnóstico |
| --- | --- |
| UUID global | `5d9a391f-30a6-d984-ad0f-958826b8ca71` |
| ID central histórico | 329; no debe confundirse con el ID visible 325 |
| operational_source_id | `748d96bb-808f-4f66-b3fe-d256326b20f9` |
| Turno / generation | 3949 / 10 |
| Estado / tipo | ACTIVA, no deleted / EMERGENCIA |
| Readiness / cobertura | LISTA / ASEGURADO_VALIDADO |
| ARS | FUTURO, facturación habilitada |
| Recibo vinculado por el mismo UUID | 6004, PENDIENTE / PRELIMINAR, no eliminado |
| Claim | Procesado con recibo 6004; no reserva ajena activa |
| Descarte | Ninguno encontrado |
| Elegibilidad actual para recibo nuevo | `false`, `RECEIPT_PENDING` |

La proyección corresponde al mismo source/turn que el historial central. Los
eventos CREATE (versión 1), DETAIL_SHEET_GENERATED (2) y UPDATE (3) conservan la
misma identidad global. En la lectura del turno había 77 activas y 4
anuladas/eliminadas; ese conteo es una observación puntual, no una cifra actual
garantizada.

**No procede forzar su aparición como pendiente de un recibo nuevo:** ya tiene
el recibo 6004. El resultado con el evaluador corregido fue `RECEIPT_PENDING`.
Esto no prueba cuál era el estado exacto al tomar la captura ni justifica
atribuirle retrospectivamente el mismo motivo. No se modificó esa atención.

La búsqueda de candidatos se normaliza para acentos, mayúsculas/minúsculas y
espacios; permite UUID y mantiene NSS/cédula. Se aplica antes de paginar. Una
búsqueda explícita sin candidatos puede diagnosticar hasta cinco coincidencias
centrales y registrar su motivo, sin loguear el nombre/NSS/cédula consultados.
Ese límite diagnóstico no restringe el universo normal de candidatos.

## 5. Incidente D: turno válido, generación bloqueada

Ruta anterior comprobada: `App.generar_pdf()` cargaba el JSON local mediante
`cargar_turno_config()`, validaba su vigencia y exigía que perteneciera al usuario
autenticado. Eso podía contradecir un OperationalState central ACTIVE cuyo
representante fuera otro usuario o cuyo espejo estuviera atrasado.

Ruta integrada corregida:

1. Validación en el worker existente; refrescar estado central cuando online.
2. `runtime.require_write()` y captura del estado autorizado.
3. Rechazar respuesta atrasada comparando source, turn, generation y revisión.
4. Adoptar el snapshot central en GUI y derivar `ConfirmedTurnConfig` en memoria.
5. Antes de persistir, comprobar nuevamente su identidad en el adaptador.

No se consulta el JSON como autoridad central, no se exige usuario autenticado =
representante y no se crea un turno para reparar la configuración. La variante
standalone mantiene su validación legacy. Offline solo utiliza el permiso
operacional vigente; no se habilita escritura por ignorar una falla central.

Estados diferenciados: `CENTRAL_UNAVAILABLE`, `NO_TURN_CONFIGURED`,
`SESSION_INVALID`, `LOCAL_STATE_STALE`, `STALE_OPERATIONAL_SNAPSHOT`. El worker
fallido libera su flag y rehabilita el botón; no deja una generación atascada.

No se dispone del JSON y del snapshot de sesión del instante del incidente.
Que la corrección Admin actualizara el espejo es una explicación compatible con
el código, **no una causa física retrospectiva demostrada**.

## 6. Hallazgos adicionales dentro de la validación

### Orden de migraciones

Al habilitar las 22 integraciones que estaban omitidas por falta de servidor,
todas fallaron inicialmente en `db_init()` con
`psycopg2.errors.UndefinedColumn: column "is_deleted" does not exist`.

La migración `20260828_billing_admission_bridge_identity.sql` creaba el índice
de la proyección antes de instalar sus columnas mediante
`_apply_admission_hybrid_migration()`. La instalación actualizada podía tenerlas
ya; una base vacía no. Se adelantó esa llamada, sin cambiar el SQL de migraciones
ni añadir una nueva. Prueba específica: bootstrap vacío, insertar registro
sintético, repetir bootstrap y comprobar preservación e índice. PASS.

Tras ese ajuste: 20 integraciones PASS y dos fallos de logout por el objeto
inválido utilizado por el harness. La protección de limpieza descrita arriba
resolvió ese borde sin cambiar los datos de las pruebas. La suite final incluyó
y aprobó las 22 integraciones.

### Reportes: limitación del autodiagnóstico

La generación de archivos funciona, pero el fixture de `run_report_exports_self_test`
no proporciona `receipt_label` y `total_label` que usa la plantilla del panel:
quedan dos rótulos y un encabezado sin texto. Además, su resumen sintético de
30/30.000 no corresponde a su único detalle de 1/1.000. No sirve para demostrar
coherencia de datos reales. El recibo con identificador sintético `SELF-TEST`
también desborda el campo diseñado para el número. No se rediseñaron reportes para
ocultar esto; QA visual integral permanece sin aprobar.

## 7. Archivos del cambio

| Archivo | Responsabilidad |
| --- | --- |
| `admission_billing_consistency.py` (nuevo) | Predicados compartidos de sesión vigente, ARS, claim y búsqueda |
| `admission_sheet_state.py` (nuevo) | Configuración tipada, estados de error y comparación de snapshots |
| `CALCULOS_QT.py` | Consultas, reserva/liberación, revalidación del recibo, diagnóstico, tooltip histórico, logout y orden de migraciones |
| `admission_bridge.py` | Transportar el timestamp de adquisición de claim |
| `admission_v15_adapter.py` | Revalidación de identidad antes de guardar una atención |
| `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py` | Validación asíncrona de generación desde el estado autorizado |
| `build_app.spec` | Incluir los dos módulos nuevos en el ejecutable |
| `tests/test_admission_billing_consistency.py` (nuevo) | Contratos SQL, cancelación, propiedad, reemplazo, logout y errores |
| `tests/test_admission_sheet_state.py` (nuevo) | Estado/GUI, offline, worker atrasado, doble solicitud y guardado |
| `tests/test_billing_consistency_postgres.py` (nuevo) | SQL real, concurrencia, elegibilidad y bootstrap temporal |
| `tests/test_admission_receipt_link.py` | Fake de la validación usando la misma conexión |
| `tests/test_admission_v15_eligibility_history.py` | No override de claim y estado histórico separado de elegibilidad |
| `tests/test_admission_validation_extensions.py` | Identificación de la consulta principal frente al diagnóstico |
| `tests/test_performance_catalog_ars_targeted.py` | Presupuesto acotado de consultas al fallar una reserva |
| `docs/ADMISSION_BILLING_CONSISTENCY_AUDIT.md` (nuevo) | Este informe |

Los resultados y utilidades de QA están en `output/consistency/` (ignorado por
Git), no en runtime ni en el ZIP. Los directorios release-candidate preexistentes
no se modificaron ni eliminaron.

## 8. Pruebas del contrato

PASS aquí significa comprobación automatizada o lectura real indicada; nunca
equivale a una prueba física de dos PCs.

| Requisito | Estado y evidencia |
| --- | --- |
| 43, caso de la captura | PASS de trazado central read-only; no elegible para recibo nuevo por recibo 6004. Escenario equivalente elegible probado en PostgreSQL temporal |
| 44, mismo turno History/Billing | PASS, `test_previous_epoch_never_wins_current_turn` y contratos SQL compartidos |
| 45, historia visible / no facturable | PASS, `test_annulled_without_tombstone_remains_historical_not_pending` |
| 46, candidato vigente | PASS, `test_current_eligible_search`, incluye nombre, NSS, cédula y UUID |
| 47, recibo existente | PASS, `test_pending_receipt_is_not_new_candidate` |
| 48, urgencia | PASS, exclusiones repetidas y revalidación antes del recibo |
| 49, tombstone | PASS, `test_tombstone_and_missing_central_identity_are_distinct` |
| 50, claim activo | PASS, `test_live_claim_one_winner_even_admin_and_expired_recovery` |
| 51, claim expirado | PASS, recuperación por UPSERT y revalidación bajo lock |
| 52, cancelar | PASS, `test_cancel_and_same_station_resume`; liberación atrasada no invalida readquisición |
| 53, atención excluida libera propia reserva | PASS, `test_state_changed_after_selection_prevents_receipt_and_releases_owned_claim` |
| 54, central down | PASS, `test_central_error_propagates_not_empty_or_claimed` |
| 55, auxiliar y turno válido | PASS local, validación autorizada y generación con espejo no autoritativo; NO VERIFICADO físicamente |
| 56, sin turno real | PASS, estados inválidos/missing y bloqueo sin persistencia |
| 57, usuario distinto del representante | PASS, `test_current_representative_not_authenticated_user` |
| 58, no duplicación | PASS, `test_generate_sheet_waits_for_validation_then_saves_once`, claim concurrente con un ganador y unicidad de recibos |
| 59, History/Billing en dos PCs | NO VERIFICADO: falta acceso físico a ambas estaciones |
| 60, búsqueda en dos PCs | NO VERIFICADO: mismo bloqueo de entorno |
| 61, claim en dos PCs | NO VERIFICADO físicamente; concurrencia SQL local PASS no lo sustituye |
| 62, estado de turno en dos PCs | NO VERIFICADO físicamente |
| 63, relevo | PASS automatizado, suites de transición operacional y handover |
| 64, idempotencia de transición | PASS automatizado, `test_operational_primary_transition.py`; no se modificó ese protocolo |
| 65, cierre y reportes | PASS automatizado, suites de cierre/snapshots/Excel; QA visual integral no aprobado |
| 66, edición | PASS, `test_patient_edit_all_roles_v110.py` |
| 67, SQLite busy | PASS automatizado, regresiones de escritura/operación híbrida; no simulación hospitalaria |
| 68, conteo | PASS, `test_turn_summary_stability_v108.py` y datasets del turno; no se cambió el guard ERROR != 0 |
| 69, PRIMARY transfer | PASS automatizado, suites de transferencia; NO VERIFICADO físicamente |
| 70–72, logs sin PHI | PASS de logs de elegibilidad/motivo y decisiones de validación; no captura completa del estado local/central/sesión en el instante del incidente hospitalario |

## 9. Resultados y calidad

Suite completa con PostgreSQL de prueba disponible: **1.047 PASS, 0 FAIL,
1 SKIPPED, 60 subtests PASS**, 184,65 segundos, dos warnings.
Omitida: integración explícita de capacidad sobre PostgreSQL real; no se habilitó
porque no corresponde escribir/medir capacidad productiva en esta reparación.

Evidencia: `output/consistency/final-test-results.xml` y
`output/consistency/final-coverage.json` / `.xml`.

Cobertura real con coverage.py sobre líneas ejecutables añadidas/modificadas y
los dos módulos nuevos: **181/186 = 97,31 % líneas; 63/68 = 92,65 % ramas**.
Los dos módulos nuevos tienen 100 % de líneas y ramas. No se atribuye ese
porcentaje a toda la aplicación legacy. Evidencia reproducible:
`output/consistency/quality-metrics.json` y `measure-quality.py`.

| Quality gate | Resultado |
| --- | --- |
| Funcionalidad local del cambio | PASS; funcionalidad física integral NO VERIFICADO |
| Unitarias / integración / regresión / bordes | PASS, suite completa descrita arriba |
| Coverage del diff | PASS, supera 90/85 y objetivo agregado crítico 95/90 |
| Formatter | PASS en los cinco archivos nuevos de código/tests; no se reformatearon módulos legacy completos |
| Lint y análisis estático | PASS, cero diagnósticos nuevos respecto a HEAD; persisten 327 en CALCULOS_QT y 85 en V15, no se afirma lint global limpio |
| Tipos | N/A: no hay gate de tipos configurado para estos módulos; no se ejecutó mypy |
| Complejidad | FAIL frente al objetivo uniforme <=10: persisten funciones legacy superiores y algunas aumentaron; máximo modificado 55 en db_init, sin aumento. Evaluador 16→21, validación bajo lock 16→18, candidatos 9→14. Helpers nuevos <=10 |
| Duplicación | PASS, jscpd: 340/61.943 líneas = 0,549 %; cero líneas del diff intersectan los bloques duplicados detectados (umbral 10 líneas/70 tokens) |
| Compilación Python / diff whitespace | PASS, compileall y git diff --check |
| Build limpio | PASS, PyInstaller |
| Smoke del ZIP extraído | PASS con el alcance limitado descrito debajo |
| Seguridad aplicable | PASS en revisión local de parametrización SQL, allowlist de alias, ownership/privilegios y ausencia de DB/config secreta en el paquete; no equivale a un pentest |
| QA visual integral de exportaciones | FAIL del fixture descrito; generación de archivos PASS |
| QA físico multiequipo | NO VERIFICADO |
| QA final / entrega | NO APROBADO PARA ENTREGA |

La complejidad no se ocultó mediante exclusiones ni bajando thresholds. Se
extrajeron reglas nuevas a helpers pequeños, pero no se hizo una refactorización
masiva de los controladores heredados para volver verde ese gate.

## 10. Build y paquete local, no release

Comando final:

```text
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath output/consistency/verified-dist --workpath output/consistency/verified-build build_app.spec
python -m release_packaging --dist output/consistency/verified-dist/SIGEH --updater output/consistency/dist/SIGEH_Updater.exe --output output/consistency/verified-package
```

Updater reconstruido previamente desde su spec limpio, sin cambios de código
desde esa compilación. Los warnings de hooks Tcl/Tk y módulos opcionales se
conservan como advertencias: el spec incluye Tcl/Tk explícitamente y la prueba
frozen de construcción de Admisión pasó.

Se conserva la versión canónica **1.1.2**, definida en `sigeh_product.APP_VERSION`
y reflejada en `version_config.json`. Este ZIP es exclusivamente de QA local,
no una nueva release ni reemplazo de la existente.

Archivo: `output/consistency/verified-package/SIGEH-1.1.2-windows-x64.zip`.
SHA-256:

```text
fcc13b2780219777dc48c39d6aa9454972f8f4fd815fd41a1503d9c6fd6e6090
```

El manifiesto de archivos conserva ruta relativa, tamaño y hash. Los campos
legacy `publishable`/`published_at` del manifiesto de empaquetado **no indican
que se haya publicado**: no se llamó a GitHub ni se movieron tags.

Extraído en `C:/Users/ampar/AppData/Local/Temp/SIGEH_verified_package_5wj78ewg`,
con USERPROFILE/APPDATA/LOCALAPPDATA temporales, directorio de trabajo externo al
binario y sin credenciales de producción. Resultados:

- Launcher sin provisionar: exit 5 esperado, fallo de configuración explícito.
- Launcher con URL sintética local no accesible: exit 0 en self-test de resolución;
  **esto no demuestra conectividad PostgreSQL**.
- `CALCULOS_QT.exe --check-v15-package`: PASS frozen, Admisión construida,
  Historial y Configuración abiertos, sin panel de error.
- `--self-test-pdf` y `--self-test-reports`: exit 0; cuatro PDF y dos Excel.
- `SIGEH_Updater.exe --help`: exit 0; prueba de entrypoint, no actualización real.
- Inspección de ZIP: sin DB operacional, SQLite, logs, `.env` ni bundle de URL.

Evidencia: `output/consistency/final-package-checks.json`. El paquete público usa
provisión externa segura de conexión; **no se presenta como fresh install online
autoconfigurado**. No hay credenciales productivas incorporadas para forzar PASS.

La inspección visual anterior del mismo motor de exportación renderizó todas
las páginas PDF y los dos Excel. Los Excel abren con Resumen y Datos, sin errores
de fórmula detectados; sus fixtures no demuestran un universo clínico real.

## 11. Cierre pendiente y prueba física preparada

1. Resolver/aceptar explícitamente el gate de complejidad heredada y corregir el
   fixture de QA de reportes antes de afirmar validación visual integral.
2. En entorno hospitalario de pruebas con ambas estaciones, capturar
   source/turn/generation/revision y el conjunto de UUID antes de operar.
3. Comparar Historial y búsqueda por nombre/NSS/cédula contra la misma atención
   central vigente, no solo cantidades. La atención ya facturada debe conducir
   a su recibo existente, nunca a otro INSERT.
4. A reserva; B intenta reservar y debe bloquear. A cancela; B adquiere. Verificar
   una fila de claim y ningún recibo duplicado; no alterar turno ni representante.
5. Probar auxiliar autorizado con representante distinto y mirror temporalmente
   atrasado en una copia controlada. Generar una sola atención y comprobar el
   UUID compartido, persistencia durable y conteo en ambas estaciones.
6. Probar error de red, recuperación y worker atrasado; UNKNOWN no puede
   convertirse en EMPTY, otra estación o creación automática de turno.
7. Registrar resultados de 59–62 con timestamps y UUID sin información clínica
   innecesaria. Probar el mismo ZIP en ambas PCs, no builds diferentes.

Se realizaron cuatro pasadas locales: funcionalidad contractual, regresiones,
clean code/métricas y QA/build/artefacto. Los límites anteriores impiden marcar
todos los gates como PASS. Las guías Supabase/PostgreSQL orientaron el aislamiento
y los locks; las de PDF/Spreadsheets obligaron a distinguir archivo generado de
resultado visual/dataset realmente validado.

**Resultado contractual: BLOQUEADO para aprobación integral y publicación;
cambios locales y evidencia automatizada disponibles para revisión.**
