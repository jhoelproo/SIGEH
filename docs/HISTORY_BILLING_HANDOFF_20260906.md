# Historial de Emergencias → Facturación — preparación local

## Implementación

- V15: botón y menú contextual «Enviar a Facturación». La selección conserva el UUID de la fila, incluso cuando dos estaciones muestran el mismo número local. No se transmite nombre, cédula ni NSS por el bus.
- `admission_v15_adapter.py`: señal de solicitud. `CALCULOS_QT.py`: revalidación central asíncrona, reserva compartida con el selector y apertura del recibo existente. Las consultas no crean una atención alternativa ni cambian su turno.
- `billing_history_handoff.py`: identidad, destino permitido y exclusión del borrador visible de la lista de pendientes. No se borra el registro clínico. Al liberar el borrador puede volver a ser candidato; al guardar se excluye por el recibo central.
- Continuación heredada: se reconoce la herencia procesada del recibo vinculado, con turno de origen coincidente y conservando la validación de fuente operacional. El vínculo al recibo permanece: no se permite crear otro.
- `build_app.spec`: empaqueta el módulo nuevo.

## Anulación y límites

Las rutas revisadas de anulación local/central requieren usuario autorizado y motivo. Reinicializar la réplica conserva una atención activa. La sincronización sigue aplicando anulaciones existentes: deshabilitar su réplica permitiría resucitar registros anulados. No se modificó esta lógica ni se restauraron tombstones históricos.

El acceso nuevo conserva readiness, cobertura/ARS, tipo EMERGENCIA, alcance actual/herencia explícita, permisos, reserva ajena y anulación. URGENCIA sigue excluida conforme al contrato previo. El acceso desde Historial no convierte una atención no sincronizada o ilegítimamente excluida en facturable; muestra la negativa central. No se retiró el descarte manual de la cola rápida ni se relajaron otros filtros existentes.

No se ha demostrado por qué el registro 399 del 05/09/2026 no aparecía en la instalación hospitalaria. Un número local no identifica por sí solo un UUID central. No hay cambios productivos, publicación ni instalación.

## Pruebas y resultados

- RED: faltaba el módulo de handoff; después, PostgreSQL reprodujo la negativa al continuar la herencia completada. GREEN: se localiza el mismo recibo y un segundo guardado mantiene un único documento.
- Focal: 33 PASS. GUI y callbacks: 30 PASS, incluyendo apertura de la ventana real y presencia del botón. Las cifras se solapan.
- Suite relacionada inicial: 104 PASS. Incluye guardados heredados, exclusión/concurrencia PostgreSQL, permisos de anulación y relevo.
- Importador aislado: 8 PASS. Lanzador aislado: 5 PASS.
- Primera suite amplia: 1260 PASS, 2 FAIL, 23 SKIP; ambos fallos de `inspect.getsource` se produjeron al cambiar posiciones del archivo después de importarlo. Se repite con fuentes estables; ese intento no se contabiliza como PASS.
- Suite final con fuentes estables: **1263 PASS, 0 FAIL, 23 SKIP, 60 subtests PASS**, 258,46 segundos. Evidencia: `results/history-final-suite.xml` y `.log`. Se ejecutaron aisladamente importador y lanzador como se indica arriba.
- Integración PostgreSQL local: **22 PASS, 0 FAIL**, 66,13 segundos (`results/history-integration.xml`). Se reejecutaron 22 de los 23 casos omitidos en la suite; resta la prueba optativa de capacidad. PostgreSQL temporal, puerto local 55432, detenido al finalizar. No sumar estas cifras como si todas las ejecuciones fueran disjuntas.
- Captura revisada: `D:/SIGEH_QA_TURN_EXCEL_20260906/history-gui/test_real_history_window_conta0/history-billing.png`. La ventana y el botón caben sin recorte. El entorno de prueba usa tema/fuentes de Qt fuera de una sesión hospitalaria.

## Cobertura y calidad

- Nuevo módulo: 25/25 líneas y 8/8 ramas, 100 %/100 %, cobertura real con `coverage run --branch`. No representa cobertura total de los monolitos.
- Ruff del módulo/tests nuevos: PASS; sin nuevas incidencias de Ruff en CALCULOS, V15 ni adaptador respecto de HEAD.
- Mypy del módulo nuevo: PASS. Complejidad máxima nueva del módulo: 9 (radon).
- jscpd del módulo nuevo: 0 clones, 0 % de duplicación; informe en `results/history-duplication/jscpd-report.json`.
- Compilación Python: PASS. Análisis estático/tipos/cobertura de todos los monolitos: NO VERIFICADO como gate integral.
- Revisión de seguridad acotada: UUID inequívoco, consultas parametrizadas, permisos y reservas centrales preservados; fixtures PostgreSQL desechables. Auditoría integral: NO VERIFICADO.

## Build y QA final

Build limpio en directorio separado mediante `python -m PyInstaller --noconfirm --clean --log-level WARN --distpath D:/SIGEH_QA_TURN_EXCEL_20260906/history-dist --workpath D:/SIGEH_QA_TURN_EXCEL_20260906/history-build build_app.spec`.

Build: **PASS**, salida 0. Se extrajo el bytecode del ejecutable y de PYZ para compararlo con las fuentes: CALCULOS, V15, adaptador y helper coinciden (`results/history-source-match.json`). SHA256 del ejecutable: `BD8368BD1402BFC97E42B53A484E422F0BD600E28195C6F47C4F8DE9A4FCA7E4`.

Smoke empaquetado: **PASS** en las tres invocaciones, salida 0: `--check-v15-package`, `--self-test-pdf` y `--self-test-reports`. Admisión, Historial y Configuración construidos. Evidencia: `results/history-smoke.json` y `history-smoke/v15.json`. No se instaló el ejecutable.

Cuatro pasadas locales realizadas: requisitos/identidad/continuación; regresiones; clean code/métricas; QA de GUI, suite y artefacto empaquetado. `git diff --check`: PASS.

## Quality gates

Funcionalidad local focal: PASS. Unitarias/regresión completa/integración focal: PASS. Boundary de identidad y estados: PASS. Cobertura/lint/tipos/complejidad/duplicación del helper: PASS. Build y smoke: PASS. Gates integrales de monolitos y comprobación entre PCs hospitalarias: NO VERIFICADO. Sin autorización de entrega productiva.

ESTADO FINAL: NO APROBADO PARA ENTREGA
