# SIGEH 1.2.14 — Validación de entrega

## IMPLEMENTACIÓN

Honorarios por ARS/especialidad y materiales asociados a medicamentos; ajustes proporcionales del borrador, validación de permisos y cargos al guardar; conservación de conceptos e importes históricos. Se incluyen impresión Carta, permisos de edad, actualización manual/al abrir y conservación de configuración local.

Archivos funcionales modificados o añadidos:
- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`
- `CALCULOS_QT.py`
- `admission_pdf_layout.py`
- `billing_date_policy.py`
- `billing_item_rule_store.py`
- `billing_item_rules.py`
- `billing_material_cart.py`
- `lanzador.py`
- `medication_material_dialog.py`
- `offline_auth.py`
- `password_secrets.py`
- `pdf_printing.py`
- `receipt_continuity.py`
- `sigeh_product.py`
- `sigeh_update.py`
- `update_controls.py`
- `updater.py`
- `user_permissions_dialog.py`

Metadatos: `sigeh_product.py`, `version_config.json`. Migración: `migrations/20261023184143_ars_specialty_medication_materials.sql`. Pruebas y documentación incorporadas junto a estas fuentes. El manifiesto funcional registra las huellas SHA-256 de la fuente entregada.

Compatibilidad: las 12 cuentas centrales conservan sus verificadores PBKDF2 compatibles de 120.000 iteraciones; no se reescriben durante el acceso. Las altas y restablecimientos siguen siendo legibles desde 1.2.13. El acceso local usa 600.000 iteraciones y actualiza verificadores anteriores sin modificar su vencimiento. No se almacena la contraseña en texto plano.

El primer salto utiliza el instalador real de 1.2.13. Durante su control de instalación, el nuevo lanzador recupera las ubicaciones adicionales del respaldo temporal. Fuentes ambiguas, fechas inseguras o errores de conservación provocan fallo y restauración de la instalación anterior. Los saltos siguientes usan el actualizador del paquete verificado.

## PRUEBAS

Unitarias, integración PostgreSQL desechable, regresión de Admisión/Facturación/listados/PDF/permisos, caché local y Qt. Límites: cantidades, especialidades, permisos falsificados u obsoletos, entradas inválidas, contraseñas Unicode, verificador corrupto, rollback transaccional, respaldos viejos/futuros/ambiguos y directorios redirigidos.

Se reprodujo incompatibilidad de contraseñas antes de corregirla: tres casos fallaron. Se reprodujo la falta de recuperación del primer salto: diez casos fallaron. Sus regresiones permanecen en la suite. No se realizaron pruebas destructivas ni altas clínicas sintéticas contra producción.

## RESULTADOS

Suite completa: **3,318 PASS**, **0 FAIL**, **0 errores**, **1 SKIP**. 3,245 casos recogidos; el total PASS incluye los subtests registrados por JUnit; 60 grupos aislados. La omisión corresponde a la prueba optativa de capacidad real, ajena a esta entrega.

La ejecución final conserva la misma huella de fuentes de principio a fin. Las ejecuciones interrumpidas o invalidadas por cambios de fuentes no se incluyen en estos resultados ni en la cobertura.

## COBERTURA

Coverage.py real con ramas; fuentes finales y comparación semántica contra la versión de producción anterior, no contra una compilación intermedia.

| Ámbito | Líneas | Ramas |
|---|---:|---:|
| Módulos nuevos | 100.0% | 100.0% |
| Cambios completos | 99.52% | 99.65% |
| Lógica crítica modificada | 99.52% | 99.65% |

No se presenta esta medición como cobertura íntegra del código heredado.

## CALIDAD

Ruff formatter/lint, mypy, Bandit, Radon y Pylint: **PASS** en el cambio; sin errores nuevos frente a las copias previas. Módulos nuevos de materiales: complejidad máxima 9; recuperación nueva: 6; verificador: 6. Las funciones heredadas de mayor complejidad se conservaron o redujeron. Duplicación: cero bloques nuevos detectados; la validación previa de impresión con jscpd registró 0% en sus módulos nuevos. No se relajaron umbrales ni se añadieron exclusiones para ocultar errores.

## BUILD

**PASS**. `python -m PyInstaller --noconfirm --distpath output/release-1214-app --workpath output/release-1214-build build_app.spec` completó el análisis y la distribución. Tras cerrar la protección del primer salto, `output/release-1214-collect.spec` recompiló PYZ, ejecutables y distribución usando los análisis terminados; se verificó que todas las dependencias del actualizador están presentes en ambos grafos.

Actualizador: `python -m PyInstaller --noconfirm --distpath output/release-1214-updater-final --workpath output/release-1214-updater-final-build build_updater.spec`: **PASS**.

Paquete público: cuatro archivos, 1822 entradas verificadas por tamaño/SHA-256; CRC correcto, sin bases operacionales, archivos de conexión, credenciales ni logs. SHA-256 ZIP: `9b4bbeb62dcf10793468168f9a8c4791642cb726cf55d51d50e3f66370b85d79`. Fuente compilada contrastada con las fuentes finales, incluido el actualizador y ambos metadatos 1.2.14.

## QA

Seis smoke tests del ZIP exacto: lanzador, ayuda del actualizador, paquete V15, PDF, visor e informes: **PASS**. Actualización real 1.2.13→1.2.14 y segundo ciclo con 1.2.14: **PASS**. Se conservaron byte a byte ocho archivos sintéticos de conexión, configuración, logo, horarios, usuarios e historial. La conexión protegida se descifró y las filas sintéticas se comprobaron después de ambos ciclos. El reinicio final hacia sesión real se sustituyó por un callback; los controles reales de arranque sí se ejecutaron.

Base central: migración aditiva aplicada y comprobada. Permanecen 12 cuentas, 20 ARS y 1.046 artículos; políticas vacías compatibles y cero vínculos nuevos por defecto. RLS activo y cero permisos PUBLIC/anon/authenticated en la tabla nueva. Sin alteraciones de tarifas ni registros clínicos. Avisos de seguridad: ninguna advertencia o error nuevo; un INFO esperado por tabla privada sin políticas públicas.

Cuatro pasadas: funcionalidad **PASS**; regresión **PASS**; clean code **PASS**; QA **PASS**.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias, integración, regresión y límites | PASS |
| Cobertura de líneas/ramas y objetivo crítico | PASS |
| Formatter, lint, tipos y análisis estático del cambio | PASS |
| Complejidad y duplicación nuevas | PASS |
| Build, smoke, seguridad focalizada y QA final | PASS |
| Migración central aditiva y comprobación de permisos | PASS |
| Prueba optativa de capacidad | N/A — ajena al cambio |

## PUBLICACIÓN

Paquete y base preparados. La publicación y el canal remoto se verifican después de subir el release; este documento no afirma todavía su publicación.

## PROBLEMAS PENDIENTES

- Instalación en cada estación y prueba de impresora hospitalaria física: **NO VERIFICADO** en este entorno. Las pruebas de actualización y de impresión PDF usan instalaciones y perfiles aislados.
- El refuerzo del formato central a 600.000 iteraciones queda pendiente de una estrategia compatible con toda la flota; esta entrega evita bloquear estaciones anteriores.
- Se mantienen cuatro advertencias centrales previas ajenas a la migración: tres funciones con [search_path mutable](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable) y [pg_trgm en public](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public). No aumentaron.

Evidencia reproducible bajo `output/release-1214-*`: grupos JUnit/logs, manifiesto de fuentes, cobertura completa/diferencial, calidad, archivo público, controles empaquetados, migración/advisores y verificación remota.

**ESTADO FINAL: APROBADO PARA ENTREGA** del paquete de software validado. No equivale a afirmar que todas las estaciones ya lo instalaron.
