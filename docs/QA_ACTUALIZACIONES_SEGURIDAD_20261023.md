# Actualizaciones y credenciales — QA del 23/10/2026

ESTADO FINAL: APROBADO PARA ENTREGA

**Alcance: compilación local validada.** La publicación estable de GitHub continúa en **1.2.13**. Esta compilación incorpora cambios posteriores y no se publicó ni se conectó mediante una sesión de usuario a producción.

## IMPLEMENTACIÓN

| Archivo | Cambio |
| --- | --- |
| `password_secrets.py` | PBKDF2-SHA256 versionado, 600.000 iteraciones, salt aleatorio, comparación constante y decodificación limitada. Admite los verificadores anteriores. |
| `CALCULOS_QT.py` | Actualiza el verificador después de un acceso válido, conserva identidad y rol, excluye verificadores de la sesión devuelta y muestra el botón de actualizaciones. |
| `offline_auth.py` | Refuerza y actualiza la caché de acceso local, conservando dispositivo, vencimiento y permisos. |
| `update_controls.py` | Consulta manual asíncrona, icono vectorial, prevención de consultas simultáneas y recuperación del botón ante errores. Cerrar la ventana durante la consulta no deja un QThread activo. |
| `sigeh_update.py` | Rechaza descargas ajenas al repositorio y ruta de publicación autorizados. |
| `lanzador.py` | Mantiene la comprobación antes del acceso en cada apertura de `SIGEH.exe`; utiliza el actualizador del paquete verificado. |
| `updater.py` | Conserva también configuración y logos en ubicaciones heredadas, prioriza la configuración instalada y verifica los archivos conservados. |

El sistema ya utilizaba hashes; se reforzó su almacenamiento y manejo. No se modificaron contraseñas de producción. No se cambiaron esquemas, importes, reglas contables ni permisos del rol. Se preservaron los cambios de impresión y permisos que ya estaban presentes al comenzar.

Pruebas nuevas: `tests/test_oct23_password_security.py`, `tests/test_oct23_password_postgres.py`, `tests/test_oct23_update_controls.py`, `tests/test_oct23_update_preservation.py` y `tests/test_oct23_update_window.py`.

Pruebas ajustadas: `tests/test_sigeh_update.py` usa las URL reales del canal; `tests/test_receipt_uuid.py` declara un actor autorizado y aísla la cola local; `tests/test_self_pay_billing_postgres.py` fija el día de servicio en dos casos que no comprueban antigüedad. Estos ajustes conservan las aserciones originales y no omiten validaciones de producción.

## PRUEBAS

- **Unitarias y límites:** claves correctas e incorrectas, Unicode, entrada vacía y larga, formatos corruptos, salts y resúmenes inválidos, límites de iteraciones, usuarios inactivos y compatibilidad con hashes anteriores.
- **Integración PostgreSQL:** migración al acceder, lectura desde otra conexión, conservación de identificador/rol, ausencia de duplicados, rollback y actualización concurrente de contraseña que no se sobrescribe.
- **Integración SQLite:** cierre y reapertura, acceso por dispositivo, ausencia del texto de la contraseña, conservación del vencimiento al actualizar el verificador.
- **Actualización:** configuración instalada frente a valores del paquete, logo, usuarios, conexión protegida, historial, rollback, manifiesto y checksum.
- **GUI real, Qt offscreen:** botón visible y accesible a 1280×680 y 1920×1000; consulta disponible/actual/sin conexión, desactivación temporal, recuperación y cierre durante la consulta. Capturas inspeccionadas visualmente.
- **Regresión:** todos los módulos recogidos por pytest, ejecutados en 58 grupos aislados. Bases desechables y servidor de QA limitado a loopback; ningún QA destructivo en producción.

Evidencia RED inicial reproducida sobre las funciones anteriores: **18 fallos esperados y 19 aprobados**, sin errores de infraestructura. El icono también se reprodujo mediante tres pruebas fallidas antes de corregirlo. Los cuatro fallos encontrados posteriormente en pruebas antiguas correspondían a fixtures; el worker y la política de fechas coinciden con la copia previa a esta tarea.

## RESULTADOS

**PASS:** 3.151 casos principales recogidos; JUnit registra además 74 subpruebas. Resultado total: **3.224 aprobados, 0 fallos, 0 errores y 1 omitido**.

La prueba omitida, `test_real_capacity_dry_run_preserves_operational_counts`, requiere habilitación explícita para una comprobación de capacidad y no corresponde a estos cambios. Las integraciones PostgreSQL de esta tarea sí se ejecutaron.

Los primeros intentos detectaron restricciones del entorno: TCP local bloqueado dentro del aislamiento y temporales en OneDrive rechazados correctamente por la protección de SQLite. La ejecución final utilizó temporales locales privados y PostgreSQL de QA. No se desactivaron esas protecciones.

## COBERTURA

Coverage.py con medición de ramas: **154/154 líneas y 34/34 ramas, 100%/100%**, sobre los cambios semánticos respecto a la copia inicial. Los dos módulos nuevos completos alcanzan **112/112 líneas y 20/20 ramas**. La lógica crítica modificada alcanza **100%/100%**.

Estas cifras no representan la cobertura de todas las líneas heredadas de `CALCULOS_QT.py`.

## CALIDAD

- Formatter y lint, Ruff: **PASS**, sin errores nuevos; rangos modificados en archivos heredados y archivos nuevos completos.
- Tipos, mypy: **PASS** en los módulos nuevos; **0 errores nuevos** frente a la copia inicial en los módulos heredados.
- Análisis estático y seguridad, Ruff/mypy/Bandit: **PASS**, sin hallazgos nuevos de seguridad.
- Complejidad, Radon: máximo **6** en funciones nuevas. La construcción heredada de la ventana mantiene **28**, sin incremento; se evita una refactorización fuera del alcance. Las demás funciones heredadas modificadas no aumentan su complejidad.
- Duplicación, Pylint: **0 bloques nuevos detectados**, umbral de seis líneas; sin aumento detectado. No se presenta este resultado como un porcentaje de duplicación de todo el repositorio.

## BUILD

**PASS**, Python 3.14.7 y PyInstaller:

```text
python -X utf8 -m PyInstaller --noconfirm --distpath output/oct23-app --workpath output/oct23-build build_app.spec
python -X utf8 -m PyInstaller --noconfirm --distpath output/oct23-updater --workpath output/oct23-updater-build build_updater.spec
```

Se configuró `PLAYWRIGHT_BROWSERS_PATH` con el Chromium instalado. La distribución local incluye el actualizador y su metadato de versión. Las siete fuentes de producción modificadas coinciden con su código compilado. El paquete supera la validación que rechaza credenciales y bases operacionales incluidas por accidente.

## QA

**Seis smoke tests empaquetados PASS:** lanzador, ayuda del actualizador, integridad de Admisión V15, PDF, visor de informes e informes. La actualización ejecutada con el código del actualizador compilado y su comprobación real de arranque conserva seis archivos sintéticos de configuración, logo, usuarios y conexión. Solo se sustituyó el reinicio hacia el acceso real para impedir conexiones de producción.

El canal real `jhoelproo/SIGEH` devuelve **1.2.13**, con manifiesto y checksum validados; no se descargó ni se publicó una nueva versión remota durante esta comprobación.

Cuatro pasadas: funcionalidad **PASS**; regresión **PASS**; clean code y diferencias frente a la copia inicial **PASS**; QA de fuentes y paquete **PASS**.

## QUALITY GATES

| Gate | Resultado |
| --- | --- |
| Funcionalidad, unitarias, integración y regresión | PASS |
| Cobertura y condiciones de borde | PASS |
| Formatter, lint, tipos y análisis estático | PASS — sin errores nuevos en código heredado |
| Complejidad y duplicación | PASS — sin incremento heredado |
| Build, smoke, seguridad del cambio y QA final | PASS |
| Migración de esquema | N/A — no se requiere |
| Prueba optativa de capacidad | N/A — ajena al cambio; omitida explícitamente |

## PROBLEMAS PENDIENTES

- **Despliegue coordinado:** los clientes antiguos no entienden los nuevos hashes versionados. Antes de usar esta compilación contra la base central, debe distribuirse el cliente actualizado a las estaciones; no se deben mezclar versiones al migrar credenciales.
- **Primer salto desde un instalador antiguo:** para garantizar las nuevas ubicaciones protegidas, debe emplearse el actualizador nuevo en ese primer despliegue. El código nuevo del lanzador protege las actualizaciones posteriores usando el actualizador verificado del paquete.
- La publicación de estos cambios y el despliegue en los equipos no forman parte de esta entrega local. La versión estable remota permanece intacta.

## EVIDENCIA

En `output/`: `oct23-suite-status.json`, `oct23-suite-manifest.json`, los 58 XML/logs de grupos, `oct23-coverage.json`, `oct23-coverage-summary.json`, `oct23-quality.json`, `oct23-package-validation.json`, `oct23-github-channel.json`, `oct23-red-reproduced.log`, `oct23-icon-red.log`, `oct23-icon-green.log`, `oct23-uuid-baseline.log`, `oct23-uuid-green.log`, `oct23-unchanged-date-policy.json`, `oct23-window-1280.png` y `oct23-window-1920.png`.

Referencia del factor de trabajo: [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html). Canal comprobado: [SIGEH 1.2.13](https://github.com/jhoelproo/SIGEH/releases/tag/v1.2.13).
