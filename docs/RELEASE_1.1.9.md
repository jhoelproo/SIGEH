# SIGEH 1.1.9 — urgencias locales y recuperación de heredadas

Fecha: 09/09/2026.

## Implementación

- El resumen del turno contabiliza las urgencias locales que permanecen visibles por un conflicto de sincronización, sin duplicarlas cuando ya existe su proyección central.
- Las estaciones primarias y secundarias sincronizadas pueden reconstruir los datos durables de relevos centrales confirmados. Así se crean las herencias pendientes aunque la estación principal no haya completado el reporte del cierre.
- La generación e impresión del reporte continúan limitadas a la estación principal.
- Se preservan las exclusiones de atenciones anuladas, eliminadas, descartadas, ya facturadas, no listas, consultas, urgencias y reservas ajenas en la cola de Facturación.
- `sigeh_product.py` y `version_config.json`: versión 1.1.9.

## Pruebas y resultados

- Regresiones específicas de herencias, PostgreSQL y GUI: PASS.
- Pruebas de versión, empaquetado y actualizador: 47 passed.
- Suite completa final: 1461 passed, 0 failed, 1 skipped, 60 subtests passed. La omisión es una integración externa opcional.
- Una ejecución anterior tuvo un fallo de latencia SQLite ajeno al cambio; la prueba se repitió y la suite completa final pasó sin modificar el límite ni el código probado.
- Cobertura de `billing_closure_recovery.py`: 20/20 líneas, 100 %.

## Calidad, build y QA

- Ruff lint y format, mypy, `py_compile` y `git diff --check`: PASS.
- Radon: complejidad máxima 6. jscpd: 0 clones en el módulo modificado.
- Build limpio de la aplicación y del actualizador: PASS.
- Los 18 módulos verificados dentro del ejecutable/PYZ coinciden con sus fuentes.
- Smoke del ejecutable: paquete V15, PDF y reportes, todos exit 0.
- ZIP público: CRC, inventario y hashes individuales de 1820 archivos, PASS.
- El paquete no contiene credenciales, bases operativas ni archivos de ejecución local.

## Artefacto

- Archivo: `SIGEH-1.1.9-windows-x64.zip`.
- SHA-256: `817961de183291722fd281c70c4c599499d795c0a771a63b537f6f998afb265e`.
- Evidencia local: `C:/SIGEH_RELEASE_119/`.

ESTADO FINAL: APROBADO PARA ENTREGA
