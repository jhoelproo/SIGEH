# SIGEH 1.1.4 — auditoría del hotfix de UUID opcional

## ROOT CAUSE

| Campo | Evidencia |
| --- | --- |
| Table | `public.recibos` |
| Column | `admission_global_attention_id` |
| PostgreSQL type | `uuid`, `is_nullable=YES`, comprobado en producción en sesión de solo lectura |
| Python parameter | `admission_values[15]` del INSERT en `save_receipt_with_items()` |
| Origin of empty string | Último elemento de `_admission_values(None)`; también `str(data.get("global_attention_id") or "")` en el camino vinculado |
| Function / File | `_admission_values()` → `save_receipt_with_items()` / `CALCULOS_QT.py` |
| Excepción reproducida | `psycopg2.errors.InvalidTextRepresentation`, `invalid input syntax for type uuid: ""` |

La prueba RED falló en el INSERT de cabecera, antes del arreglo, sobre PostgreSQL
17 temporal y aislado con el esquema instalado por `db_init()`. El traceback
identificó `CALCULOS_QT.py:11021` en la revisión previa al arreglo. No se reprodujo
creando recibos productivos ni se imprimieron credenciales o datos clínicos.

El esquema productivo tiene **una sola columna UUID en recibos**. Los IDs locales
de Admisión son bigint y `admission_source_instance_id` es TEXT nullable: no deben
validarse como UUID ni reemplazarse por identificadores ficticios.

## WHY BYPASS TRIGGERED IT

Bypass legítimo → `admission_attention=None` → tuple con UUID `""` → INSERT
parametrizado → PostgreSQL rechaza el cast implícito a UUID. La clasificación de
autorización ya era correcta; no causaba el error. El UPDATE tenía un tratamiento
SQL de cadena vacía que no compartía el INSERT. Las pruebas antiguas con conexiones
simuladas incluso esperaban `""`; no comprobaban el tipo real de PostgreSQL.

## FIX / IMPLEMENTACIÓN

- `receipt_uuid.py`: normalización reutilizable, error de validación seguro,
  diagnóstico de tipo/ausencia y contexto de fallo que vuelve a lanzar la excepción.
- `CALCULOS_QT.py`: normaliza antes de abrir la transacción; INSERT y UPDATE
  reciben None/UUID canónico. UPDATE mantiene vínculos existentes mediante
  COALESCE. El worker conserva los canales de duplicado y atención excluida,
  pero no envía excepciones SQL crudas al popup genérico.
- `tests/test_receipt_uuid.py`: contrato, límites, privacidad, rechazo antes del
  repository y señales del worker ante errores.
- `tests/test_receipt_optional_uuid_postgres.py`: PostgreSQL temporal con schema
  real, bypass, edición, rollback, reintento y concurrencia.
- `tests/test_admission_validation_extensions.py`: expectativa de ausencia
  corregida a None, sin debilitar permisos/estados existentes.
- `sigeh_product.py`, `version_config.json`, `tests/test_sigeh_update.py`:
  identidad canónica y pruebas de versión 1.1.4; no reemplazo de versiones históricas.
- `RELEASE_NOTES_1.1.4.md` y este informe: alcance, evidencia y limitaciones.

No se modificaron claims, elegibilidad, roles, turnos, UUID de atenciones,
PRIMARY/SECONDARY, historial ni el esquema productivo. No se ejecutó backfill.
La ausencia de source TEXT también se representa como None; los source históricos
no vacíos conservan su identidad, sin imponer formato UUID.

### Auditoría de otros caminos

Crear, editar, guardar para auditoría y el worker de documentos comparten
`save_receipt_with_items()`. `add_recibo()` y `update_recibo_db()` legacy no
insertan/actualizan la columna UUID; usan su NULL por defecto o preservan el valor.
Los cambios de estado, restauración y finalización no fabrican ese vínculo.
Las consultas comparativas que usan TEXT no son escrituras UUID. La migración
existente que vincula la proyección obtiene UUID tipados de PostgreSQL, no de
campos de texto GUI. No se requirió modificarla.

## UUID NORMALIZATION CONTRACT

| Entrada | Antes del SQL |
| --- | --- |
| None | None → SQL NULL |
| `""` | None → SQL NULL |
| espacios/tabulaciones | None → SQL NULL |
| UUID válido como string | String canónico de la misma identidad |
| `uuid.UUID` | Se conserva en el normalizador; se adapta a texto canónico al formar parámetros psycopg2 |
| String no vacío inválido / tipo incorrecto | `InvalidOptionalUUID` antes del INSERT/UPDATE; no se registra el valor |

No existe `uuid4()` ni generación de atenciones en este arreglo. El DTO general
de Admisión conserva compatibilidad legacy; la frontera de persistencia del
recibo normaliza sus valores ausentes sin reescribir SQLite.

## TRANSACCIÓN, ROLLBACK Y REINTENTO

Cabecera, ítems, auditoría y snapshot se guardan en el mismo `PostgresWrapper`.
El commit sigue ocurriendo al salir correctamente del contexto; una excepción
provoca rollback. La prueba fuerza un fallo de snapshot **después de insertar
ítems** y verifica 0 cabeceras, 0 ítems, 0 historial de facturación y 0 acciones
parciales. El reintento guarda un recibo; repetirlo se rechaza como duplicado.
Dos guardados concurrentes también producen exactamente un recibo y un ítem.
Se conserva el advisory lock y la deduplicación existentes, sin inventar IDs.

## BYPASS TESTS

| Prueba | Resultado / evidencia |
| --- | --- |
| Valid authorization / no admission | PASS: LISTO_AUDITORIA, CLEAR, UUID/ID/source NULL, ítem, total y auditoría presentes |
| No authorization | PASS: PRELIMINAR / AUTHORIZATION_MISSING |
| Invalid authorization | PASS: LISTO_AUDITORIA / PENDING_REVIEW / INVALID_AUTHORIZATION_FORMAT |
| Short authorization | PASS: LISTO_AUDITORIA / PENDING_REVIEW / AUTHORIZATION_TOO_SHORT |
| Legacy empty UUID | PASS: None, cadena vacía y espacios guardan NULL en PostgreSQL; INSERT y UPDATE |
| Valid admission UUID | PASS: identidad conservada en persistencia; también regresión integral normal con proyección y claim real de prueba |
| Invalid non-empty UUID | PASS: rechazo previo a ejecutar SQL |
| Rollback | PASS: error después de ítems no deja registros parciales |
| Retry no duplicate | PASS: un recibo después de error/reintento y bajo concurrencia |
| NORMAL BILLING | PASS: suite de enlace, candidatos, exclusión, claims activos/expirados, ya facturados y recorrido integral |
| No fake admission | PASS: tablas de proyección y eventos permanecen vacías al guardar bypass aislado |
| GUI error channel | PASS automatizado: worker emite fallo seguro, no éxito ni SQL/valores privados |
| F5 físico en hospital / dos PC | NO VERIFICADO; no se presentó la prueba automatizada como prueba física |

La prueba de persistencia legacy aísla explícitamente elegibilidad con un stub;
no se usa para afirmar que un vínculo inexistente pasa la verificación normal.
Esta última se comprueba en la suite integral y las pruebas de claims.

## RESULTADOS Y EVIDENCIA LOCAL

- RED: 1 fallo esperado, `InvalidTextRepresentation`, antes del fix.
- Primer conjunto funcional: 72 PASS.
- Primera suite completa: detectó 3 subcasos con expectativa legacy `""` y
  2 expectativas de versión antiguas; se actualizaron al contrato requerido.
- Suite completa final: **1.088 PASS, 0 FAIL, 1 SKIP, 60 subpruebas PASS**.
- Revisión final tras ordenar el import: **77 PASS, 0 FAIL, 3 subpruebas PASS**.
- Omitida: `test_real_capacity_dry_run_preserves_operational_counts`; requiere
  habilitar explícitamente la prueba de capacidad productiva, ajena a este hotfix.
- Archivos locales: `output/bypass-uuid/final-test-results.xml`,
  `final-focused.xml`, `final-coverage.json`, `quality-metrics.json`.
- El PostgreSQL de pruebas se crea en loopback con puertos controlados y se
  detiene al terminar; no existe fallback de pruebas a producción.

## COBERTURA / CALIDAD

- Coverage.py real, cambio funcional: **41/41 líneas y 8/8 ramas, 100 % / 100 %**.
  Módulo crítico nuevo: 35/35 líneas y 8/8 ramas. No es cobertura de todo SIGEH.
- Ruff: 0 errores nuevos respecto a HEAD anterior; módulo y pruebas nuevas sin
  errores. Los 327 diagnósticos históricos del monolito no aumentan.
- Ruff format: PASS en módulo y pruebas nuevos. No se formatea masivamente el
  monolito como parte de un hotfix.
- Mypy `--check-untyped-defs receipt_uuid.py`: PASS, entorno de herramientas
  temporal; no se afirma tipado completo del código heredado.
- Radon: máximo nuevo 6. Complejidad heredada sin incremento: `_admission_values`
  18, `save_receipt_with_items` 143, `PDFDatabaseWorker.process` 16.
  Excepción técnica explícita: se mantiene la unidad transaccional existente;
  dividir el guardado completo ampliaría el alcance/riesgo del hotfix prohibido
  por la solicitud. La lógica nueva se extrajo a funciones pequeñas.
- jscpd: 0 líneas nuevas en clones; 0,5225 % de duplicación en los dos módulos
  analizados. No se afirma un porcentaje de todo el repositorio.
- Seguridad: SQL parametrizado, mensajes sin valores sensibles, ningún UUID
  fabricado, ningún permiso ampliado, sin secreto nuevo ni migración destructiva.

## BUILD / QA / QUALITY GATES

Build mediante `python -m PyInstaller --noconfirm --clean --log-level WARN`
con `build_app.spec` y `build_updater.spec`, en carpetas nuevas
`output/release-v1.1.4-final`. El nuevo módulo se incluye por import explícito.
El comando `python -m release_packaging` construye ZIP, SHA-256 y manifests.
La comprobación del paquete se hace sobre una extracción del ZIP final en perfil
temporal, no solamente sobre dist.

| Gate del hotfix | Estado |
| --- | --- |
| Funcionalidad / unitarias / boundaries | PASS |
| Integración PostgreSQL / rollback / concurrencia | PASS |
| Regresiones aplicables | PASS |
| Cobertura del cambio crítico | PASS |
| Formatter / lint incremental / análisis estático | PASS |
| Tipos del módulo nuevo | PASS |
| Complejidad nueva / duplicación nueva | PASS; excepción legacy documentada arriba |
| Build principal, launcher y updater | PASS: ambos comandos PyInstaller terminaron con código 0 |
| Smoke desde el ZIP final extraído | PASS: 6 comprobaciones, incluidos fallos esperados sin configuración |
| Contenido del ZIP | PASS: ningún archivo prohibido detectado |
| Migración nueva | N/A: esquema UUID ya nullable |
| Prueba destructiva productiva | N/A: prohibida y no necesaria |

La validación del ZIP y su hash se registra separadamente en
`output/release-v1.1.4-final/package-verification.json` y los manifests publicados.
Los controles de launcher sin configuración confirman un fallo explícito esperado;
la configuración sintética sólo verifica resolución, **no conexión productiva**.

ZIP: `SIGEH-1.1.4-windows-x64.zip`.
SHA-256: `c5b6dd1b9cebff64b4d661ca69cfcabbf5c4c27805ea016e46ecc2555a7878d3`.
La extracción final construyó Admisión y abrió Historial/configuración;
generó cuatro PDF y dos Excel, sin instalarse en el hospital. Estos smoke tests
no certifican coherencia visual/numérica de reportes productivos.

## RIESGO RESIDUAL / ALCANCE DE APROBACIÓN

No se certifica una prueba física hospitalaria ni se cierran los pendientes de
v1.1.3 relativos a reportes o QA multiestación. La compilación puede advertir
imports opcionales/Tcl del entorno heredado; los smoke tests determinan si los
entrypoints Qt/PDF/Excel utilizados siguen funcionando. Un esquema diferente
del inspeccionado requiere diagnóstico, no cambios automáticos a producción.

FINAL STATUS DEL HOTFIX FUNCIONAL: **COMPLETADO** mediante pruebas automatizadas
e integración aislada. La aprobación de este cambio no certifica toda la
aplicación heredada ni sustituye la comprobación física posterior a actualizar.

ESTADO FINAL de homologación integral: **NO APROBADO PARA ENTREGA** mientras
continúen los pendientes físicos y generales declarados de v1.1.3. La publicación
del hotfix solicitada no convierte esos pendientes en PASS.
