# SIGEH 1.2.5 — recuperación de configuración en descarga directa

## Hallazgo de la 1.2.4

El smoke de la 1.2.4 ejecutó el lanzador sin configuración y aceptó como resultado previsto la salida 5 (`CONFIGURATION_MISSING`). Eso verificó el rechazo seguro, pero no probó descargar en una carpeta nueva teniendo una versión previa extraída en una carpeta versionada. El ZIP público no contiene credenciales por diseño. Este caso se corrigió y probó como mejora de recuperación; no demuestra la causa de la captura del hospital. El usuario confirmó posteriormente que su fallo de instalación se resolvió liberando espacio.

## Cambio

`database_config.py` ahora busca primero las ubicaciones convencionales SIGEH/HOSPITAL y también carpetas SIGEH cuyo nombre coincida estrictamente con `SIGEH-x.y.z`, `SIGEH-x.y.z-windows-x64` y su variante interna. Examina solo las carpetas inmediatas dentro de la descarga, el Escritorio del perfil y los escritorios OneDrive configurados. No recorre carpetas arbitrarias. Mantiene la prioridad existente de la configuración del propio paquete y recupera únicamente archivos protegidos previamente emitidos por la aplicación.

La versión cambió a 1.2.5 debido a que 1.2.4 ya fue publicada.

## Pruebas

- Archivo protegido sintético en `Desktop/SIGEH-1.2.4-windows-x64/SIGEH/_internal` y nueva descarga en `Desktop/SIGEH-1.2.5-windows-x64/SIGEH`: se recupera la misma configuración y el lanzador la entrega al proceso principal.
- Se ignora `SIGEH-backup-1.2.4-windows-x64` y cualquier nombre fuera del patrón estricto.
- Continúan las pruebas de configuración local, variable de entorno, DPAPI, autenticidad del contenedor y prioridad.
- Smoke final del paquete: elimina configuración del proceso, crea únicamente una instalación previa SIGEH sintética en una ruta versionada, extrae el ZIP de 1.2.5 junto a ella y ejecuta `SIGEH.exe --self-test`. Esperado: salida 0 y registro `config_source=existing_install`. Luego valida PDF, visor, exportación de informes, transferencia y paquete de Admisión.

La prueba usa una credencial sintética con servidor no enrutable y no se conecta a producción. La actualización no puede recuperar configuración que nunca se guardó localmente o que esté en una ubicación no configurada. En tal caso se requiere instalación interna del hospital.

## Estado

PASS para la mejora de recuperación: 41 pruebas focales; 27/27 líneas ejecutables modificadas y 8/8 ramas modificadas cubiertas con Coverage.py. Ruff check/format y Mypy PASS; complejidad máxima de helpers nuevos 7. El build y la regresión final compartidos con el ajuste de cierres constan en `ADMINISTRATIVE_CLOSURE_1.2.5_QA.md`.

El ZIP final, posterior al ajuste de cierres, tiene SHA-256 `ef2afdedadeef8f0138eb105bfce4b98d2592690ec4d95776f224bc9fa6d27c8`. Sus 1.822 hashes se verificaron y las seis pruebas de extracción devolvieron 0. No se considera que esta prueba explique el error de espacio reportado por el usuario.
