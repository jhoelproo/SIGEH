# SIGEH 1.1.11 — consistencia de Admisión, Facturación y reportes de cierre

Fecha: 12/09/2026.

## Implementación

- Una atención existente en el Historial local reconstruye su proyección central antes de enviarse a Facturación o anularse. La reparación reutiliza el evento central cuando existe y, si falta, publica un evento de reconciliación determinista desde la fila local exacta.
- La reconciliación conserva el turno, la sesión operativa y el usuario original de Admisión; quien ejecuta la reparación no sustituye al autor de la atención.
- La sincronización revisa de forma acotada las atenciones recientes para recuperar proyecciones faltantes sin duplicar registros ni recibos.
- Los cierres automáticos vacíos, pendientes, fallidos o generados sin impresión vuelven a una cola recuperable. El intento de impresión se ejecuta aunque falle la apertura externa del documento.
- Los reportes históricos se abren dentro del visor PDF de SIGEH. Si la copia en caché falta o está dañada, se reconstruye desde el snapshot persistido y se valida antes de abrirla.
- `sigeh_product.py` y `version_config.json`: versión 1.1.11.

## Pruebas y resultados

- Suite completa: 1,479 passed, 0 failed y 60 subtests passed.
- Regresiones focales de proyección, envío a Facturación, anulación, cierres y reportes: 148 passed, 0 failed.
- Regresiones de versión, actualización y empaquetado: 35 passed, 0 failed.
- Integración PostgreSQL aislada: reconstrucción, envío y anulación de una proyección eliminada, PASS.
- Cobertura del código modificado: 202/202 líneas (100 %) y 66/68 ramas (97.1 %).

## Calidad, build y QA

- Ruff lint y format de los módulos nuevos y sus pruebas: PASS. Comparación diferencial del módulo heredado: 0 errores nuevos.
- Mypy diferencial: 0 errores nuevos; permanecen 44 errores heredados sin relación con esta entrega.
- `py_compile` y `git diff --check`: PASS.
- Radon del código agregado o modificado: complejidad máxima 10. La función heredada de cola bajó de 72 a 67.
- jscpd: 0 clones y 0 % de duplicación en los módulos y pruebas nuevos.
- Build limpio de la aplicación y del actualizador con PyInstaller: PASS.
- Smoke del ZIP extraído: lanzador, V15, Historial, Configuración, PDF y exportaciones, todos con exit 0.
- ZIP público: CRC, inventario y hashes individuales de 1,822 archivos, PASS. No contiene credenciales, bases operativas ni archivos runtime.

## Artefacto

- Archivo: `SIGEH-1.1.11-windows-x64.zip`.
- SHA-256: `2fbade8e652855953bfe7217d403561716a2f5c87338cafb73034fc536ad384e`.
- Evidencia local: `C:/SIGEH_RELEASE_1111/`.

ESTADO FINAL: APROBADO PARA ENTREGA
