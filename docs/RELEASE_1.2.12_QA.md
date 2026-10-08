# Publicación de SIGEH 1.2.12 — 8 de octubre de 2026

**ESTADO FINAL: APROBADO PARA ENTREGA** del paquete publicado en el canal estable. Aceptación del usuario recibida antes de publicar. Publicación: 2026-10-08T21:03:44Z (17:03:44, America/La_Paz). [Versión estable](https://github.com/jhoelproo/SIGEH/releases/tag/v1.2.12).

## IMPLEMENTACIÓN

Se publicó la compilación ya validada, sin cambiar su código funcional. Fuente/tag: `41dcdec06710fca9f1de6b78a48309d5af09f360`. Se guardaron los 82 archivos del cambio acumulado y sus pruebas en ese commit; las notas nuevas están en `docs/RELEASE_1.2.12.md`. Las funciones, archivos y comportamiento preservado se detallan en [QA funcional](QA_1.2.12_20261008.md) y [continuidad local](QA_CONTINUIDAD_LOCAL_20261006.md). Esta publicación añade este informe y las notas; no altera los ejecutables.

Se aplicaron las migraciones aditivas `durable_local_receipt_requests` y `receipt_insurance_identification` al proyecto central autorizado. Quedaron registradas como 20261008203925 y 20261008210221: UUID/hash de solicitudes locales, dos campos de identificación de seguros y un índice único parcial. Las cuatro columnas son nullable; el índice es válido y RLS permanece activado. No se ejecutaron INSERT/UPDATE/DELETE de datos clínicos ni se cambiaron grants/políticas. Se fijaron límites de espera para evitar una espera prolongada del DDL.

## PRUEBAS

La huella de las 259 entradas funcionales/pruebas/plantillas sigue siendo `e3c63b2a9f66a99c4309ed4b7570f298ca6c9b5d1dde86468019d77f2c25311d`, idéntica a la suite completa validada. Se reutiliza esa evidencia porque la publicación no cambia esas fuentes. Unitarias, integración, regresiones y límites están detallados en el informe funcional; no se presentan como una nueva ejecución en esta publicación.

Comprobaciones adicionales realmente ejecutadas: CRC y hashes de los 1.818 archivos del ZIP; seis smoke del paquete extraído; actualización 1.2.11 → 1.2.12 con el código del actualizador extraído del ejecutable instalado; un segundo ciclo con el actualizador 1.2.12; conservación byte a byte de configuración protegida e historial SQLite sintéticos. Ambos ciclos ejecutan el health check empaquetado real. Solo se sustituyó el callback final de apertura del lanzador para evitar login/contacto con producción. Reaplicar 1.2.12 no equivale a probar una versión futura.

Se verificaron la estructura real del esquema central y el historial de migraciones mediante consultas de lectura. No se cargaron pacientes ficticios ni se hizo QA de escritura contra producción. Se comprobó el borrador antes de publicarlo y el canal público después: tag/commit, cuatro assets, tamaños, SHA-256, manifest/checksum y detección de actualización desde 1.2.11; la misma versión no provoca reinstalación.

## RESULTADOS

| Comprobación | PASS | FAIL / ERROR | SKIP |
| --- | ---: | ---: | ---: |
| Suite funcional previa, fuentes sin cambios, 221 módulos / 49 procesos | 2.900 | 0 | 1 |
| Smoke adicionales del ZIP extraído | 6 | 0 | 0 |
| Ciclos adicionales del actualizador | 2 | 0 | 0 |
| Assets remotos con tamaño/hash coincidentes | 4 | 0 | 0 |

La omisión previa es el ensayo optativo de capacidad real, no necesario para esta publicación. No se suman smoke ni ciclos al total unitario.

## COBERTURA

Coverage.py medido antes de publicar, fuentes idénticas: código nuevo/modificado 713/714 líneas (99,86%) y 177/178 ramas (99,44%); lógica crítica 503/504 líneas (99,8%) y 137/138 ramas (99,28%). Los seis módulos nuevos alcanzan 100%/100%. No representa toda la aplicación heredada. Cobertura adicional por la publicación: N/A — no introduce lógica funcional.

## CALIDAD

Formatter, lint, mypy, análisis estático/Bandit: PASS del cambio y cero errores nuevos, según evidencia funcional sin cambios. Complejidad nueva máxima 10; cero bloques duplicados nuevos. Se conservan las excepciones y deuda heredadas descritas en el QA funcional. `git diff --check` y el index antes del commit: PASS. Porcentaje global recomendado de duplicación: NO VERIFICADO, sin alterar el gate obligatorio de duplicación nueva.

## BUILD

PASS previo: `python -m PyInstaller --noconfirm --distpath output/oct08-app --workpath output/oct08-build build_app.spec`, build del updater y empaquetado. No se recompiló ni reempaquetó tras las pruebas de aceptación. Código compilado/recursos ya comparados con fuentes; huella conservada. SHA-256 del ZIP: `f825daeecebcc4406c2cb206200f54992226dd357a647cac4920bb1c4c264710`. El digest remoto coincide.

## QA

Las cuatro pasadas comprobaron: funcionalidad y aceptación; regresión/actualización y conservación local; clean code y fuentes sin cambios; paquete, smoke, migraciones y canal público. Supabase devuelve los mismos avisos de seguridad anteriores: 65 INFO de RLS sin políticas, tres WARN de search_path y un WARN de extensión en public, sin avisos nuevos. Son deuda existente; no se afirma que toda la base esté libre de avisos ni se amplió acceso público. [Advisor RLS](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy), [search_path](https://supabase.com/docs/guides/database/database-linter?lint=0011_function_search_path_mutable), [extensiones](https://supabase.com/docs/guides/database/database-linter?lint=0014_extension_in_public).

## QUALITY GATES

| Gate aplicable | Estado |
| --- | --- |
| Funcionalidad/aceptación, unitarias, regresión, integración y límites | PASS — fuentes comprobadas sin cambios y evidencia previa conservada |
| Cobertura, formatter, lint, tipos, estático, complejidad y duplicación nueva | PASS — mismo alcance validado |
| Build y concordancia de fuentes/paquete | PASS |
| Integridad ZIP y seis smoke adicionales | PASS |
| Actualización y conservación de configuración/historial | PASS |
| Migraciones centrales, índice válido y RLS preservado | PASS |
| Seguridad del cambio: cero avisos nuevos | PASS |
| Tag/commit, assets, checksum/manifest y canal latest público | PASS |
| Cuatro pasadas de QA final | PASS |
| Nuevas pruebas unitarias/cobertura de publicación | N/A — sin lógica funcional nueva |
| Capacidad real optativa | N/A — no requerida |

## PROBLEMAS PENDIENTES

La publicación y las migraciones están completas. Instalación efectiva de 1.2.12 en cada estación del hospital: NO VERIFICADO desde este entorno; deben aplicar la actualización ofrecida por SIGEH. El usuario informó que completó las pruebas de aceptación; no se presenta eso como una ejecución propia. La versión 1.2.11 sigue publicada como referencia anterior. Los avisos y límites heredados documentados permanecen fuera de este despliegue. No se afirma un costo cero del servicio ni un consumo mensual futuro.

Evidencia local: `output/release-1212-fingerprint.json`, `release-1212-package-validation.json`, `release-1212-draft-verification.json`, `release-1212-publication-verification.json`, `release-1212-production-schema-before.json` y `release-1212-production-schema-verification.json`. Los resultados completos de código/build/GUI están enlazados en el QA funcional.
