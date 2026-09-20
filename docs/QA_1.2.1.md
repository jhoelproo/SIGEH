# Validación de SIGEH 1.2.1

## Implementación

- El visor histórico conserva referencias estables al documento y a la vista PDF durante toda la ventana.
- La apertura desde **Abrir PDF** y **Vista previa e imprimir** usa el mismo visor integrado y presenta la ventana inmediatamente.
- Se añadió `--self-test-report-viewer` para validar el visor desde el ejecutable empaquetado.
- **Historial de turnos** pasó a la barra inferior del reporte estadístico. La ventana presenta búsqueda y selección en pasos separados, selección descriptiva y paginación anterior/siguiente.
- `sigeh_product.py` y `version_config.json` declaran la versión 1.2.1.

## Pruebas

- Suite completa del cambio antes del ajuste de versión: **1,574 passed, 29 skipped, 60 subtests passed, 0 failed**.
- Regresión final de versión, visor e historial: **49 passed, 0 failed**.
- Empaquetado, actualización y arranque portable: **47 passed, 0 failed**.
- Prueba de integración GUI: selección de un cierre en la tabla, reconstrucción en segundo plano y apertura visible con una página legible.
- Cobertura de `admission_turn_history_dialog.py`: **96.35 % de líneas** y **90 % de ramas**.

## Calidad

- Ruff de módulos nuevos y pruebas modificadas: PASS.
- Mypy del historial con dependencias omitidas: PASS.
- Complejidad Ruff C901 del historial: PASS.
- `compileall` y `git diff --check`: PASS.
- La aplicación heredada conserva hallazgos previos fuera de este cambio; no se hizo una reformateación masiva.

## Build y paquete

- Aplicación PyInstaller onedir: PASS.
- Actualizador PyInstaller onefile: PASS.
- Autodiagnóstico PDF empaquetado: PASS.
- Autodiagnóstico del visor empaquetado: PASS.
- Autodiagnóstico de exportaciones empaquetado: PASS.
- Arranque rápido del lanzador: PASS.
- El arranque completo sin configuración devuelve el error de configuración esperado porque el paquete público no incluye credenciales.
- ZIP: `SIGEH-1.2.1-windows-x64.zip`, **309,825,301 bytes**, **1,823 entradas**, CRC íntegro.
- SHA-256: `8d230675b9ede18ceaee7fe1a272d94ffbb9b4590ffa23eb80635cc70f178429`.
- Manifiesto y versión interna: 1.2.1.

## Quality gates

| Gate | Estado |
| --- | --- |
| Funcionalidad | PASS |
| Unitarias, integración y regresión | PASS |
| Cobertura del código nuevo | PASS |
| Lint, tipos y complejidad aplicables | PASS |
| Build y empaquetado | PASS |
| Smoke test del paquete | PASS |
| Seguridad del paquete público | PASS |
| Impresora física del hospital | NO VERIFICADO — dispositivo no disponible |

**ESTADO FINAL: APROBADO PARA ENTREGA** del software validado.
