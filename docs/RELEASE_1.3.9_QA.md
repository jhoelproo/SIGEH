# Validación del parche SIGEH 1.3.9

14 de noviembre de 2026.

## Implementación

Los registros del incidente apuntan a SIGEH 1.3.8 en modo sin conexión. La captura identifica un error en el constructor de la ventana principal. SQLite devuelve los favoritos serializados como listas JSON, mientras que la ventana espera un conjunto de pares. El intento de insertar listas en el conjunto causa el cierre.

- `receipt_continuity.py` convierte los pares válidos en tuplas y descarta entradas incompletas o de tipos inválidos.
- `CALCULOS_QT.py` aplica la conversión al construir la ventana con o sin conexión.
- `sigeh_product.py` y `version_config.json` identifican la versión 1.3.9.
- `tests/test_mainwindow_viewport_oct08.py` reproduce el cierre con SQLite y abre la ventana Qt con el parche.
- `tests/test_receipt_continuity.py` cubre listas, tuplas, duplicados, entradas vacías e inválidas.
- `tests/test_sigeh_update.py` verifica los metadatos y la detección de versiones.

La corrección trabaja sobre la representación en memoria. Los favoritos válidos se conservan y se reutiliza la caché ya existente.

## Pruebas

La prueba de regresión reprodujo el mismo `TypeError` de la captura en 1.3.8. La primera ejecución con el parche abrió la ventana y reveló que el escenario de prueba exigía un módulo que solo se carga en línea. Se corrigió esa aserción de prueba.

| Validación | Resultado |
|---|---|
| Unitarias, integración local, ventanas, actualización y regresión | PASS · 145 aprobadas, 0 fallidas, 0 omitidas |
| Límites de datos y formatos de caché existentes | PASS |
| Cobertura del código modificado | PASS · 13/13 líneas, 8/8 ramas |
| Lógica crítica de favoritos | PASS · 11/11 líneas, 8/8 ramas |
| Formato de los rangos editados y lint diferencial | PASS · cero hallazgos nuevos |
| Tipos y seguridad | PASS · mypy para la función nueva y Bandit diferencial |
| Complejidad de la función nueva | PASS · Radon 9 |
| Duplicación | PASS · Pylint, cero bloques detectados en los dos módulos funcionales |

## Compilación e instalación

```powershell
py -3.14 -m PyInstaller --noconfirm --distpath output/dist --workpath output/build build_app.spec
```

Compilación Windows PASS. Se verificaron los módulos compilados, el archivo completo, el incremental, los manifiestos y los hashes de 1.823 archivos reconstruidos. El paquete público usa el actualizador y SumatraPDF del paquete oficial 1.3.8 previamente verificado.

La instalación incremental desde una copia de 1.3.8 y los seis controles de arranque y documentos pasaron. Se verificaron seis archivos preservados en la instalación sintética. Se ejecutaron las comprobaciones en un perfil temporal con la conexión a producción deshabilitada.

## Publicación

PASS. Publicada como versión estable en [GitHub](https://github.com/jhoelproo/SIGEH/releases/tag/v1.3.9). El tag apunta a 45743deaf9cae5385d9f00bb8081619e37f7d67b. Tamaños y SHA-256 de los cinco archivos comprobados antes y después de publicar. El cliente compilado 1.3.8 detectó 1.3.9 mediante el canal público y seleccionó el incremental de 37,4 MB.

## Pendientes

La instalación en una estación del hospital queda NO VERIFICADA. La conexión central que originó el modo sin conexión se debe evaluar en el hospital si continúa inaccesible. El parche permite usar el inicio de sesión sin conexión con una cuenta válida en la caché existente. Persisten los avisos heredados de PyPDF2.

**ESTADO FINAL: APROBADO PARA ENTREGA**
