# SIGEH 1.2.7 — validación del release

## IMPLEMENTACIÓN

Se incrementa `sigeh_product.py` y `version_config.json` a 1.2.7. El release reúne la conservación de conexión en `database_config.py`, `portable_launcher.py` y `updater.py`, la corrección administrativa de ARS, los listados manuales por fecha de servicio y el historial/documentos de cobros directos. Las notas están en RELEASE_1.2.7.md.

Archivos y reglas detallados en CONNECTION_PERSISTENCE_QA.md, RECEIPT_ARS_CORRECTION_QA.md, MONTHLY_MANUAL_RECEIPTS_QA.md, FOREIGN_UNINSURED_BILLING_QA.md y SELF_PAY_PENDING_QA.md. Se mantienen identidades, permisos, datos históricos, cobertura y transacciones fuera de cada corrección. No se incluyen credenciales ni bases operacionales en el ZIP público.

## PRUEBAS

Suite completa recollectada y ejecutada por once grupos en procesos aislados: PASS. Las pruebas PostgreSQL usan instancias locales descartables o la instancia de QA de loopback; no usan datos del hospital. Se reinició la instancia local de QA y se repitieron los grupos 2 y 4 que inicialmente omitieron diez casos por falta de conexión; las repeticiones pasaron. Los resultados iniciales se conservan con el sufijo before-local-db.

Regresión específica de actualizaciones/paquete/lanzador posterior al incremento: 36 PASS, cero fallos/omisiones (`output/release-127-update-final.xml`). Se actualiza el ejemplo de versión futura de 1.2.7 a 1.2.8; la expectativa antigua falló antes de corregirse (`release-127-version-red.xml`). Las pruebas de límites de negocio, errores, rollback, permisos y formatos se mantienen en la suite.

## RESULTADOS

2028 casos regulares recogidos y ejecutados, sin casos faltantes ni adicionales: 2027 PASS, cero FAIL, cero errores y un skipped de capacidad opcional de base real. Además, 60 subtests PASS. Total: 2087 comprobaciones PASS y una omitida. Evidencia: output/release-127-full-summary.json, release-127-full-suite-completeness.json y once XML/logs de grupos. No se reutilizan resultados de la suite anterior como si se hubieran ejecutado con 1.2.7.

## COBERTURA

Herramienta real: coverage.py con ramas. Los módulos funcionales del paquete coinciden con las fuentes medidas en las validaciones anteriores. Conexión: código modificado 100 % líneas/ramas. ARS: 97,35 % líneas/87,50 % ramas; núcleo crítico 98,82 %/97,06 %. Cobros directos: agregado 96,55 %/89,10 %, núcleo crítico 100 %; modelo de cierre 100 %/98 %. Consulta de candidatos manuales: 100 %/100 %. Se miden funciones modificadas y módulos nuevos; no se afirma cobertura total del monolito.

## CALIDAD

Ruff lint/formatter PASS y Mypy PASS de configuración, lanzador, actualizador, producto y pruebas correspondientes. Las métricas de módulos de ARS/cobros y el diferencial del monolito se conservan con los mismos contenidos: no se aumentan diagnósticos heredados ni se añaden supresiones. Radon: lógica nueva de persistencia 2, escritura 4 y resolución 10; módulo de ARS máximo 10. Excepciones de complejidad heredada documentadas en las validaciones de cada función. Pylint duplicate-code: no se detectan bloques nuevos en los módulos comprobados. Bandit de conexión/actualizador: siete hallazgos heredados, cero nuevos. SQL parametrizado y checks de rol/identidad transaccionales comprobados en QA de ARS. `git diff --check`: PASS, solo avisos de conversión CRLF.

## BUILD

Ambos PASS, exit 0:

```
python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/release-127-app --workpath build/release-127-app build_app.spec
python -m PyInstaller --clean --noconfirm --log-level WARN --distpath output/release-127-updater --workpath build/release-127-updater build_updater.spec
python release_packaging.py --dist output/release-127-app/SIGEH --updater output/release-127-updater/SIGEH_Updater.exe --output output/release-1.2.7 --version 1.2.7
```

Logs: release-127-app-build.log y release-127-updater-build.log. Coincidencia bytecode empaquetado/fuentes: cinco verificaciones de aplicación/lanzador/actualizador y doce módulos funcionales adicionales PASS. No se cambia código de producción después del build.

## QA

ZIP: CRC y tamaño/hash SHA-256 de 1822 archivos PASS; versiones interna y externa 1.2.7 PASS; archivo sin configuración privada ni bases operacionales PASS. SHA-256 del ZIP: `fba2fdea182c11257e338b41fe133ade7f04a3e9c4b1eb22ddb5a9bb4f8b4a40`.

Seis comprobaciones reales del paquete extraído: lanzador, CLI del actualizador, paquete de Admisión, PDF, visor y reportes, todos exit 0. Se verifica el ZIP original 1.2.6 contra su checksum y el hash publicado, antes de usarlo como instalación inicial. Dos instalaciones consecutivas con filesystem real y health check de los ejecutables: PASS con código del actualizador extraído de los ejecutables instalados 1.2.6 y 1.2.7. Conexión cifrada e historial sintético byte por byte sin cambios, y dos pacientes conservados. Solo se sustituye el callback final de apertura de sesión para evitar contactar producción. Evidencia: output/release-127-package-validation.json. Se reaplica el mismo paquete para simular un siguiente ciclo; no se afirma haber construido ni probado una versión futura distinta.

El primer intento del harness dejó abierta su conexión SQLite de prueba y Windows rechazó mover la carpeta. Se corrigió el cierre de esa conexión, sin modificar el producto. La repetición preservó cifrado e historial; el intento inicial queda conservado en output/release-127-installation-qa. Se realiza la comprobación adicional con código extraído de los ejecutables instalados para reflejar el flujo del lanzador.

Cuatro pasadas PASS: funcionalidad implementada comprobada, regresión completa finalizada, clean code revisado, QA de paquete y actualización con datos sintéticos. Publicación autorizada explícitamente por el usuario pese a la falta de logs del incidente del hospital.

## QUALITY GATES

Funcionalidad del release, unitarias/integración específica, regresión completa, cobertura, límites, formato/lint/tipos/análisis estático diferencial, complejidad documentada, duplicación, seguridad, build, smoke y QA final del release: PASS. Prueba opcional de capacidad real: N/A — requiere entorno explícito y no se necesita para validar actualización. Validación con datos reales del hospital: N/A — no se realiza QA destructivo ni se simulan pagos reales. Publicación y canal de actualización: PASS, verificación remota realizada.

## PUBLICACIÓN

Publicado el 2026-10-04T15:20:58Z: https://github.com/jhoelproo/SIGEH/releases/tag/v1.2.7. Tag sobre e49fa10cc57078d869f7a63dfd724ac7fbaa2915. Cuatro assets remotos completos, tamaños y SHA-256 idénticos a los archivos locales validados. Publicado como estable y latest después de comprobar todos los assets del draft. La API pública, consultada mediante sigeh_update, devuelve 1.2.7 y valida manifest/checksum remotos. Detecta actualización desde 1.2.6 y rechaza volver a instalar la misma 1.2.7. Evidencia: output/release-127-draft-verification.json y release-127-publication-verification.json.

## PROBLEMAS PENDIENTES

El log del hospital registra recuperación de cierres fallida y un error de interfaz on_success. No se presentan como resueltos. Falta el log posterior al fallo genérico del lanzador. El usuario autorizó publicar sin resolver esos diagnósticos; permanecen fuera de la corrección confirmada de este release. Un primer equipo sin configuración ni una instalación anterior necesita provisionar su conexión; una actualización conserva la configuración local existente. No se garantiza el funcionamiento ante cualquier condición de disco, antivirus o permisos.

**ESTADO FINAL: APROBADO PARA ENTREGA — alcance del release validado; incidentes diferidos por autorización del usuario.**
