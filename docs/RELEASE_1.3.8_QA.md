# SIGEH 1.3.8 — validación de entrega

7 de noviembre de 2026.

## IMPLEMENTACIÓN

- `ADMISION_PYSIDE6_V15/facturacion_tabs_pyside6.py`: distribución adaptable del reporte estadístico, filtros plegables, selección rápida de ARS, navegación entre turnos, vistas guardadas y pestañas de pacientes/comparación.
- `admission_statistical_reports.py`: catálogo de ARS del turno independiente de los filtros adicionales; conserva el conjunto canónico compartido por tabla, indicadores, PDF y Excel.
- `admission_turn_history.py` y `admission_turn_history_dialog.py`: consulta del turno vecino con orden estable, identidad de origen y apertura del reporte histórico.
- `admission_report_tools.py`, `admission_report_favorites.py` y `admission_report_panels.py`: comparación equivalente, seguimiento de recibos, preferencias separadas por usuario y acceso a editores existentes.
- `admission_v15_adapter.py`: conexión y eventos existentes para consultas y apertura de recibos.
- `build_app.spec`: incluye los tres módulos nuevos.
- `sigeh_product.py`, `version_config.json` y `tests/test_sigeh_update.py`: versión 1.3.8 y validación del canal de actualización.
- Pruebas nuevas o actualizadas: `test_admission_report_tools.py`, `test_admission_report_favorites.py`, `test_admission_report_panels.py`, `test_admission_report_postgres.py`, `test_turn_history_report_flow.py`, `test_admission_initial_layout_20260822.py`, `test_admission_statistical_reports_v106.py` y `test_sigeh_update.py`, bajo `tests/`.

Se conservan las reglas contables, los permisos de edición, las plantillas de impresión y los identificadores de pacientes, atenciones y recibos. No se modifica el esquema central. La comparación se presenta en su propia pestaña; las exportaciones existentes siguen representando el reporte seleccionado.

## PRUEBAS Y RESULTADOS

| Ejecución | Passed | Failed | Skipped |
|---|---:|---:|---:|
| Regresión relacionada de Admisión, reportes, historial y ventanas | 249 y 42 subpruebas | 0 | 0 |
| Verificación final de funcionalidad y cobertura | 91 | 0 | 0 |
| Casos adicionales de conexión, navegación y vistas | 50 | 0 | 0 |
| Actualización, descarga, instalación, compatibilidad y versión 1.3.8 | 217 | 0 | 0 |
| Comprobaciones de ejecutables 1.3.8 | 6 | 0 | 0 |

Los alcances se solapan y no deben sumarse como pruebas distintas. La prueba de versión falló inicialmente con el valor 1.3.7 y pasó después de actualizar el producto y sus metadatos.

Unitarias y límites: selección vacía, cero resultados, comparación con base cero, primer/último turno, vistas inválidas, máximo de 40 vistas y 80 caracteres, permisos revocados y errores de almacenamiento/conexión. GUI: botones, búsqueda, favoritos, filtros, cierre y descarte de respuestas antiguas. Integración PostgreSQL desechable: UUID, identidad heredada, varios recibos, exclusión de eliminados y ceros iniciales de autorización. Sin QA destructivo contra producción.

## COBERTURA

Medición real mediante coverage.py con ramas. Archivos nuevos completos; archivos heredados, cambios semánticos frente a la copia anterior a la tarea.

| Alcance | Lines | Branches |
|---|---:|---:|
| Ampliación del reporte | 610/612 · 99,67 % | 146/150 · 97,33 % |
| Rediseño inicial del reporte, verificado antes de la ampliación | 298/299 · 99,67 % | 55/56 · 98,21 % |
| Tres módulos nuevos completos | 465/467 · 99,57 % | 119/122 · 97,54 % |
| Lógica crítica modificada | 130/130 · 100 % | 52/52 · 100 % |
| Constante de versión | 1/1 · 100 % | N/A: no tiene ramas |

Cada módulo modificado del reporte cumple individualmente 90 % de líneas y 85 % de ramas. La lógica crítica supera los objetivos 95 %/90 %.

## CALIDAD

Formatter, lint, tipos y análisis estático: PASS mediante Ruff, mypy y Bandit. Cero errores nuevos frente a las bases guardadas, sin debilitar umbrales. Los archivos heredados se revisan por diferencias; los módulos nuevos, completos. Complejidad ciclomática máxima de producción modificada: 10, medida mediante Radon. Pylint no detectó bloques duplicados nuevos con umbral de seis líneas. `git diff --check`: PASS.

## BUILD

```powershell
python -m PyInstaller --noconfirm --distpath output/release-138-app --workpath output/release-138-work output/nov07-report-preview.spec
python -u -X utf8 output/release-138-build-qa.py
```

PASS. Se reutiliza la colección de dependencias analizada con `build_app.spec`, con los módulos nuevos añadidos. Se verificó la igualdad entre el código final y once módulos relevantes empaquetados. El actualizador coincide con su binario estable verificado. Los metadatos y el producto compilado identifican la versión 1.3.8.

Seis comprobaciones aisladas: arranque, actualizador, paquete Admisión, PDF, visor y reportes. Sin acceso al servidor de producción.

## QA

- Funcionalidad, regresión, limpieza del código y evidencia: cuatro pasadas revisadas.
- GUI: resumen a 760 × 580 y 1180 × 720; pacientes a 760 × 620, 960 × 650 y 1180 × 720; comparación a 960 × 650. Temas claro y oscuro, con datos ficticios y acciones visibles.
- Contraste: 24 pares aprobados; mínimo de texto 5,18:1.
- Seguridad: consultas parametrizadas, vínculos existentes, permiso comprobado antes y después de resolver una atención y preferencias sin datos de pacientes.
- Empaquetado e instalación incremental: PASS. Se verificaron CRC y SHA-256 de 1.823 archivos y se reconstruyó el paquete completo desde el incremental de 1.3.7. La instalación real en una copia aislada superó el health check y conservó ocho archivos de configuración/datos comprobados, incluido logo personalizado, nombre, horario, usuarios e historial. El caché derivado del turno conservó sus valores; solo cambió su fecha de actualización. Paquete completo: 311.135.897 bytes; incremental: 20.249.679 bytes.
- La primera simulación se detuvo porque asumía un caché derivado presente en el paquete base. Se corrigió exclusivamente el escenario de QA, sembrando un caché sintético dentro de la copia aislada, y la simulación completa pasó.
- Publicación y canal automático: PASS. Publicada como estable y última versión en https://github.com/jhoelproo/SIGEH/releases/tag/v1.3.8. Los cinco assets se verificaron antes y después de publicar: tamaños y SHA-256 calculados por GitHub coinciden con los locales. Se comprobó que el tag remoto apunta al commit de código `c04e3092e306226b9a04bc5aeb697e4d712afcc4`. El cliente compilado publicado en 1.3.7 detecta 1.3.8 mediante el canal público, valida manifest/checksum y elige el incremental correcto.

## QUALITY GATES

| Gate | Estado |
|---|---|
| Funcionalidad, unitarias, integración, regresión y límites | PASS |
| Cobertura | PASS |
| Formatter, lint, tipos y análisis estático | PASS |
| Complejidad y duplicación | PASS |
| Build y smoke test | PASS |
| Seguridad de código | PASS |
| Instalación final | PASS |
| Publicación | PASS |
| QA final de publicación | PASS |

## PROBLEMAS PENDIENTES

Los favoritos se guardan por usuario en cada computadora; no se sincronizan entre estaciones. Los estados de facturación requieren conexión para actualizarse. La instalación física en equipos del hospital queda NO VERIFICADA; las pruebas usan copias aisladas con datos ficticios. Persiste el aviso heredado de deprecación de PyPDF2.

**ESTADO FINAL: APROBADO PARA ENTREGA**

Evidencia consolidada y hashes: [RELEASE_1.3.8_EVIDENCE.json](RELEASE_1.3.8_EVIDENCE.json). Los registros de pruebas, cobertura, calidad, build, instalación y canal se conservaron bajo `output/`, con prefijos `admission-report`, `nov07-report` y `release-138`.
