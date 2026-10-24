# Importación Word de precios — QA del 24 de octubre de 2026

## IMPLEMENTACIÓN

- `CALCULOS_QT.py`: determina el catálogo del documento mediante sus encabezados, tanto en Facturación como en Gestión ARS. El Word adjunto deja de enviarse al importador de servicios por estar activa una pestaña ARS. Sin un encabezado reconocido conserva la selección anterior.
- Reutiliza los lectores, normalización de nombres, tarifas y operaciones SQL existentes. Centraliza el resumen y rechaza documentos sin ítems válidos antes de abrir la transacción. Actualiza la memoria de Facturación tras una importación universal desde Gestión ARS, evitando recargar ese catálogo cuando no hubo cambios.
- `tests/test_word_catalog_import.py`: regresiones de destino, encabezados en párrafos y tablas, pestañas, mensajes, actualización de memoria, errores, dependencia ausente y documentos vacíos.
- `tests/test_word_catalog_import_postgres.py`: persistencia, reimportación, identificadores estables y rollback en PostgreSQL local desechable.
- `tests/fixtures/word/catalogo_sintetico.docx`: fixture sintético de QA. El documento real aportado se probó localmente y no se incluye en el repositorio.

El documento real aportado se procesó localmente con **63 medicamentos y 34 materiales**. La regresión publicada usa datos sintéticos. Los precios se conservan como base; el margen existente se aplica al mostrar medicamentos en Facturación. No se modifican contabilidad, recibos históricos, impresión, permisos ni datos productivos. El ajuste anterior de cantidad junto al buscador permanece intacto.

## PRUEBAS

- RED: se reprodujo la selección incorrecta del importador desde las cuatro pestañas de servicios y Gestión ARS; el lector de servicios obtiene cero ítems del archivo mientras el universal obtiene 97. Evidencia en `output/word-import-red.log`.
- GREEN: **27 PASS** en la suite específica, incluidos dos casos de integración real en PostgreSQL desechable. Casos: seis pestañas, Gestión ARS, encabezados de servicios, tabla, catálogo por defecto, ausencia de ARS, ausencia de python-docx, vacío, error y recarga condicional.
- Valores de borde del documento: 0.43, 2,722.50 y 9,180.00; conserva dosis decimales y separadores. La segunda importación actualiza los mismos 97 identificadores sin duplicarlos. Un error intermedio revierte todas las inserciones de la transacción.
- GUI: botón real de la ventana a 1280×680, con pestaña Honorarios activa y confirmación nativa. Reconoce el formato del documento, refresca el catálogo y conserva las cantidades junto a búsqueda. La persistencia se sustituye por datos controlados en esta prueba; se verifica realmente por separado en PostgreSQL.
- La primera captura inmediata de la ventana ocasionó una excepción nativa Qt. Se sincronizó el arnés con el bucle de eventos antes de capturar; la prueba completa pasó y se inspeccionaron ambas imágenes. Se conserva el log previo en `output/word-import-gui-before-events.log`; no se presenta ese intento como aprobado ni como un bug de aplicación reparado.

## RESULTADOS

Regresión completa: **3,350 PASS**, **0 FAIL**, **0 errores**, **1 SKIP**. 3,277 casos recogidos, subtests adicionales y 63 grupos aislados. La prueba omitida corresponde a capacidad real optativa, ajena al cambio. La huella de fuentes permaneció estable durante la ejecución.

Prueba específica y PostgreSQL: 27 PASS. QA adicional con ventana real: 1 PASS. Smoke del paquete compilado: 6 PASS. Los recuentos específicos forman parte de la regresión y no se suman dos veces.

## COBERTURA

Coverage.py con ramas: **100.0% de líneas modificadas** (47/47) y **100.0% de ramas modificadas** (22/22). Cumple tanto los mínimos generales como el objetivo de lógica crítica. Medición sobre cambios semánticos respecto a la copia inicial; no se afirma cobertura integral del monolito.

## CALIDAD

- Formatter Ruff en cambios y pruebas: PASS.
- Lint Ruff, tipos mypy y análisis de seguridad Bandit: PASS, cero diagnósticos nuevos frente al estado inicial; se conservan diagnósticos heredados.
- Complejidad Radon: máximo 7 en funciones nuevas; máximo 10 en funciones modificadas. Importación universal 10→10, ARS 9→8 y acción principal 7→6. Sin funciones nuevas mayores de 15.
- Duplicación Pylint: cero bloques nuevos detectados. Resúmenes y validación compartidos, sin relajar configuraciones.
- Revisión manual: SQL parametrizado existente, ningún acceso nuevo a red, credenciales ni ejecución de instrucciones del Word. Perfil de QA y datos sintéticos separados de producción.

## BUILD

PASS: `python -m PyInstaller --noconfirm --distpath output/word-import-app --workpath output/word-import-build output/catalog-quantity-collect.spec`.

Se reutiliza el análisis previo completo de dependencias y se recompilan PYZ, ejecutables y distribución. No hay imports nuevos. El smoke compara el código compilado con las fuentes actuales y valida el paquete antes de ejecutar diagnósticos.

## QA

Se ejecutaron lanzador, actualizador, comprobación V15, PDF, visor e informes con conexión sintética a loopback y perfil aislado: seis salidas 0. QA visual de `output/word-import-confirmation.png` y `output/word-import-window.png`.

Cuatro pasadas: funcionalidad PASS; regresiones PASS; clean code PASS; QA PASS. `git diff --check`: PASS.

## QUALITY GATES

| Gate | Resultado |
|---|---|
| Funcionalidad, unitarias, integración y regresión | PASS |
| Casos vacíos, errores, límites, reimportación y rollback | PASS |
| Cobertura de líneas y ramas modificadas | PASS |
| Formatter, lint, tipos y análisis estático del cambio | PASS |
| Complejidad y duplicación del cambio | PASS |
| Build, smoke, seguridad focalizada y QA final | PASS |
| Cambios de esquema / migración productiva | N/A — no se requieren |
| Modificación o entrega de un nuevo Word | N/A — se corrige el importador |

## PROBLEMAS PENDIENTES

La corrección está implementada y compilada **localmente**. No se publicó una nueva versión ni se importaron precios en producción. No se modificó el Word original.

Evidencia reproducible: `output/word-import-*` y pruebas versionadas.

**ESTADO FINAL: APROBADO PARA ENTREGA** del ajuste local.
