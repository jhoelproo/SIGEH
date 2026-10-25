# SIGEH 1.3.2

- El autocompletado utiliza el catálogo base y los diagnósticos configurados por administración. Los textos históricos sin revisar no se convierten automáticamente en sugerencias; se excluyen opciones incompletas, compuestas o con variantes cercanas mal escritas.
- El índice de diagnósticos se reutiliza al escribir. Su reconstrucción, validación del modelo y guardado de caché durante la sincronización se realizan en segundo plano.
- La administración de recomendaciones carga las filas por lotes breves, comparte los modelos de opciones y reutiliza el índice de búsqueda para mantener operativa la ventana.
- Se puede cancelar una carga sin esperar en la interfaz. Los resultados tardíos se descartan. El guardado mantiene sus validaciones y la protección contra operaciones duplicadas.

Se conservan los diagnósticos históricos, asociaciones, recibos, importes, permisos y datos locales. La elección de ítems sigue siendo manual.

Validación funcional, rendimiento, cobertura y publicación: `RELEASE_1.3.2_QA.md`.
