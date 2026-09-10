# Recuperación de heredadas desde cualquier estación sincronizada — 09/09/2026

## Implementación

- `billing_closure_recovery.py`: una estación de Admisión primaria o secundaria puede reconstruir los datos durables de un cierre central ya confirmado cuando está conectada, sincronizada y tiene identidad de dispositivo.
- La generación y la impresión del reporte continúan limitadas a la estación primaria por `MainWindow.process_shift_closure_report`.
- Se conserva la captura transaccional e idempotente existente: el cierre se bloquea por identidad central y una captura ya creada no se sobrescribe.
- La lista de Facturación continúa mostrando turnos anteriores únicamente mediante una herencia `PENDIENTE`. Mantiene las exclusiones de anuladas, eliminadas, descartadas, no listas, urgencias, consultas, ARS no facturables, recibos existentes y claims ajenos.

La consulta productiva de diagnóstico fue de solo lectura y no incluyó datos personales. Encontró tres relevos centrales confirmados desde el corte autorizado del 07/09/2026 sin captura de cierre. En esos turnos no existían herencias nuevas vinculadas. Esto reproduce la causa de la lista vacía sin inferirla desde las capturas.

## Pruebas

- Regresión RED previa: la estación secundaria sincronizada no programaba la recuperación.
- Pruebas focalizadas finales: 18 passed, 0 failed.
- PostgreSQL local desechable: pendientes conservados durante tres relevos, idempotencia, exclusión de anuladas/tombstones y aparición final en el filtro `HEREDADO`.
- Suite relacionada: 172 passed, 0 failed, 1 skipped antes de habilitar el PostgreSQL auxiliar; las 30 integraciones PostgreSQL se repitieron y pasaron.
- Suite completa final: 1461 passed, 0 failed, 1 skipped, 60 subtests passed. La omisión es una integración externa opcional; no corresponde a este cambio.

## Cobertura y calidad

- Coverage.py con ramas sobre `billing_closure_recovery.py`: 20/20 líneas, 100 %. Coverage.py no identifica puntos de rama instrumentables en este módulo; los resultados PRIMARY, SECONDARY, sin conexión, con sincronización pendiente, sin dispositivo y rol vacío están cubiertos por parámetros.
- Ruff lint: PASS. Ruff format: PASS.
- Mypy `--follow-imports=silent --check-untyped-defs`: PASS.
- Radon: complejidad máxima 6.
- jscpd sobre el módulo modificado: 0 clones.
- `git diff --check` y `py_compile`: PASS.

## Build y QA

Build limpio:

```powershell
python -m PyInstaller --noconfirm --clean --log-level WARN --distpath C:/SIGEH_QA_INHERITED/dist-final --workpath C:/SIGEH_QA_INHERITED/build-final build_app.spec
```

Resultado: PASS. Se verificaron 18 módulos dentro del ejecutable/PYZ contra sus fuentes; todos coinciden. Smoke del ejecutable en perfil aislado: `--check-v15-package`, `--self-test-pdf` y `--self-test-reports`, todos exit 0 y con artefactos presentes.

No se modificaron registros productivos, no se publicó una versión y no se ejecutó impresión física hospitalaria.

## Quality gates

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias, integración, límites y regresión | PASS |
| Cobertura del módulo modificado | PASS |
| Formatter, lint, tipos y análisis estático | PASS |
| Complejidad y duplicación | PASS |
| Build, fuente empaquetada y smoke | PASS |
| Seguridad y consulta productiva de solo lectura | PASS |
| Impresión física en el hospital | N/A — no se modificó la impresión |
| Publicación e instalación hospitalaria | NO VERIFICADO — fuera de esta preparación |

Evidencia local: `C:/SIGEH_QA_INHERITED/`.

ESTADO FINAL: APROBADO PARA ENTREGA
