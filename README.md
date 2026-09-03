# SIGEH

SIGEH es el sistema integrado de gestión hospitalaria del Hospital Provincial
Dr. Ángel Contreras. Integra Admisión de Emergencias, Facturación, Listados de
ARS y Reportes en una aplicación de escritorio Windows construida con Python,
PySide6, PostgreSQL y una réplica SQLite privada por estación.

## Módulos

- Admisión y gestión operacional de turnos.
- Facturación médica.
- Listados mensuales de ARS.
- Reportes, recibos y documentos clínico-administrativos.

## Desarrollo local

1. Crear y activar un entorno virtual Python 3.12.
2. Instalar las dependencias del proyecto.
3. Configurar `DATABASE_URL` mediante el mecanismo local protegido.
4. Ejecutar `python CALCULOS_QT.py`.

Nunca publicar `DATABASE_URL`, credenciales, bases SQLite reales, logs,
recibos, PDFs o Excel con datos de pacientes.

## Compilación para Windows

SIGEH se distribuye como PyInstaller `onedir`:

```powershell
python -m PyInstaller --noconfirm --clean build_updater.spec
python -m PyInstaller --noconfirm --clean build_app.spec
```

El resultado completo se genera en `dist/SIGEH`. Una release contiene la
carpeta completa; nunca debe publicarse solo `CALCULOS_QT.exe`.

### Instalación limpia del hospital

El ZIP público no contiene configuración ni credenciales del backend. Para una
instalación hospitalaria nueva se genera un artefacto **interno y no
publicable** mediante el bootstrap portable ya existente:

```powershell
python release_packaging.py `
  --dist dist/SIGEH `
  --updater dist/SIGEH_Updater.exe `
  --output release-internal `
  --version 1.1.2 `
  --internal-deployment `
  --backend-bundle C:\ruta-segura\database_url.bundle
```

La opción es deliberadamente explícita: el empaquetador continúa rechazando
esa configuración en el canal público. El ZIP interno debe transferirse por el
canal privado del hospital, no adjuntarse a GitHub ni conservarse en el
repositorio. Cada paquete genera checksum, manifest de componentes y manifest
recursivo de archivos para comprobar que dos estaciones recibieron exactamente
el mismo artefacto.

## Actualizaciones automáticas

`SIGEH.exe` consulta exclusivamente releases de
`jhoelproo/SIGEH`, valida producto, versión, manifest y SHA-256 del ZIP, y
delega la sustitución completa de `onedir` a
`SIGEH_Updater.exe`, un ejecutable autónomo ONEFILE. El instalador conserva
configuración, documentos y réplica local, ejecuta un health check sin login y
restaura la versión anterior si la instalación falla.

Los assets usan el formato `SIGEH-<version>-windows-x64.zip`, acompañado por
un `.sha256` y `SIGEH-<version>-manifest.json` con `product: SIGEH`.
