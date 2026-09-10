# SIGEH 1.1.10 — heredadas operativas y conteo de urgencias

Fecha: 10/09/2026.

## Implementación

- Facturación reconoce como heredadas las emergencias de un turno cerrado mediante relevo primario confirmado, aunque la recuperación del artefacto de cierre todavía no haya creado su fila materializada.
- El Historial de Admisión y la lista de Validación comparten el mismo alcance activo. Las heredadas anteriores al reinicio autorizado del 07/09/2026 permanecen en el historial general, pero no vuelven a pendientes.
- La recuperación de cierres se solicita al abrir Emergencias y continúa reintentándose cada 30 segundos en estaciones sincronizadas.
- El resumen del turno y los reportes de turno incorporan las urgencias locales pendientes de sincronización sin duplicar una identidad ya confirmada por la proyección central.
- Se agregó un índice para localizar cierres confirmados por turno sin recorrer todo el historial operacional.
- `sigeh_product.py` y `version_config.json`: versión 1.1.10.

## Pruebas y resultados

- Suite completa: 1,444 passed, 25 skipped, 0 failed y 60 subtests passed.
- Regresiones focales de heredadas, PostgreSQL, conteo de urgencias, reportes, interfaz y recuperación de cierres: PASS.
- Límites del reinicio: un segundo antes del 07/09/2026 queda fuera; el instante exacto del corte queda incluido.
- Cobertura de `billing_inheritance_scope.py` y `billing_closure_recovery.py`: 28/28 líneas, 100 %.

## Calidad, build y QA

- Ruff lint y format de los módulos nuevos y sus pruebas: PASS.
- Comparación Ruff crítica del código heredado: 0 errores nuevos; permanecen 5 referencias dinámicas preexistentes en `CALCULOS_QT.py`.
- Mypy de los dos módulos críticos nuevos, `py_compile` de los módulos afectados y `git diff --check`: PASS.
- Radon: complejidad máxima 6 en los módulos críticos nuevos; la función nueva de unión estadística tiene complejidad 3.
- jscpd: 0 clones y 0 % de duplicación en los módulos nuevos.
- Build limpio de la aplicación y del actualizador con PyInstaller: PASS.
- Smoke del ejecutable extraído: `--check-v15-package`, `--self-test-pdf` y `--self-test-reports`, todos exit 0. Admisión, Historial y Configuración abrieron; se generaron cuatro PDF y dos Excel.
- ZIP público: CRC, inventario y hashes individuales de 1,820 archivos, PASS. No contiene credenciales, bases operativas ni archivos de ejecución local.

## Artefacto

- Archivo: `SIGEH-1.1.10-windows-x64.zip`.
- SHA-256: `58103f0a16172ca68684562fb9d6660a10655021062db296322ad81e6f7576cc`.
- Evidencia local: `C:/SIGEH_RELEASE_1110/`.

ESTADO FINAL: APROBADO PARA ENTREGA
