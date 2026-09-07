# Reemplazo 1.1.6 — ventana del Historial para Facturación

## Implementación

- El Historial central permite enviar a Facturación una Emergencia del origen operativo vigente cuando su `created_at_effective_utc` es estrictamente posterior a `CURRENT_TIMESTAMP - INTERVAL '2 days'`.
- Los cuatro roles configurados pueden usar esa ventana: Auxiliar, Administrador, Facturador de auditoría y Auditoría médica y cuentas.
- Administrador y Facturador de auditoría pueden enviar Emergencias históricas del origen vigente sin límite temporal.
- Exactamente 48 horas queda fuera de la ventana de los roles ordinarios. La comparación utiliza el reloj y el `TIMESTAMPTZ` de PostgreSQL, no el reloj de una estación.
- Continúan los bloqueos de anulación, tombstone, tipo distinto de Emergencia, readiness, ARS/cobertura, recibo existente, claim ajeno y origen operativo ajeno.
- El Historial de los roles ordinarios muestra el turno vigente, herencias explícitas y Emergencias dentro de la ventana; no expone el historial antiguo completo.
- Se conserva la versión pública 1.1.6 por instrucción expresa. El tag y los cuatro assets se reemplazan después de validar el nuevo build. Una instalación previa debe descargar nuevamente el ZIP porque no existe incremento de versión.

## Pruebas

- TDD: 10 fallos de caracterización antes de implementar la regla; luego 93 PASS en la suite relacionada.
- PostgreSQL desechable: 7 PASS. Casos de 47 h 59 min, exactamente 48 h, más de 48 h, 30 días, listado reciente/vencido y excepción por rol.
- Cobertura focal con ramas de la política modificada: 61/63 líneas (96,83 %) y 24/26 ramas (92,31 %).
- Ruff en las pruebas modificadas/nuevas: PASS. `CALCULOS_QT.py`: 327 incidencias heredadas antes y después; 0 nuevas.
- Radon de las funciones creadas o refactorizadas para la política: máximo 10.
- Suite relacionada final: 98 PASS, 0 FAIL. Suite completa estable: 1312 PASS, 0 FAIL, 23 SKIP y 60 subtests PASS en 463,12 segundos. Importador: 8 PASS. Lanzador: 5 PASS.
- Build PyInstaller: PASS. Empaquetado de 1.1.6: PASS.
- Verificación: CRC/SHA-256, 1808 archivos contra manifiesto y comparación de bytecode empaquetado con fuentes: PASS.
- Smoke del ejecutable extraído: paquete V15, PDF y reportes PDF/XLSX, los tres con exit code 0: PASS.
- SHA-256 del ZIP sustituto: `be0e1e95966d485d3dd9d56da0b62e8ad4d6620e18fb181da665e10075d43c16`.
- Evidencia: `D:/SIGEH_RELEASE_116_REPLACEMENT/`.

Durante TDD hubo 10 fallos esperados antes de la implementación. Una primera integración produjo 4 fallos por esperar el código interno de alcance en vez del resultado final `ELIGIBLE_PENDING`; el comportamiento permitido ya era correcto y la expectativa se corrigió. Una suite completa intermedia detectó una prueba heredada que exigía el bloqueo anterior del Facturador de auditoría; se actualizó conforme al requisito y la suite final estable pasó completa. Ninguna de esas ejecuciones se cuenta como PASS final.

## Alcance operativo

No se consultaron ni modificaron datos productivos. La prueba PostgreSQL creó y eliminó su propio servidor local temporal. La captura se usó para identificar el mensaje visible; no se incluyó en el repositorio ni en el release.

La corrección temporal queda validada. La certificación física de impresión del release 1.1.6 continúa fuera del alcance y NO VERIFICADO, según el informe anterior.

ESTADO FINAL: NO APROBADO PARA ENTREGA.
