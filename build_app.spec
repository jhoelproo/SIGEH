# -*- mode: python ; coding: utf-8 -*-

import json
import os
import sys
from importlib.util import find_spec
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


ROOT = Path(SPECPATH).resolve()
ASSETS = ROOT / "assets"
PDF_ENGINE = ROOT / "pdf_engine"
REPORT_ENGINE = ROOT / "report_engine"
ADMISSION_SOURCE = ROOT / "admission_source"
ADMISSION_CORE = ADMISSION_SOURCE / "emergency_core"
SUMATRA_PDF = ADMISSION_SOURCE / "SumatraPDF.exe"
V15_PACKAGE = "ADMISION_PYSIDE6_V15"
V15_SOURCE = (ROOT / V15_PACKAGE).resolve()
V15_ASSETS = V15_SOURCE / "assets"
V15_TEMPLATES = V15_SOURCE / "HOJAS"
DATABASE_BUNDLE = Path(
    os.environ.get("SIGEH_DATABASE_BUNDLE", ROOT / "database_url.bundle")
).resolve()
if not DATABASE_BUNDLE.is_file():
    raise FileNotFoundError(
        "Falta database_url.bundle. Defina SIGEH_DATABASE_BUNDLE para el build de producción."
    )
V15_MODULE_FILES = tuple(
    V15_SOURCE / name
    for name in (
        "__init__.py",
        "admission_context.py",
        "admission_widget.py",
        "facturacion_tabs_pyside6.py",
        "project_bootstrap.py",
        "qt_compat.py",
    )
)
V15_AVAILABLE = (
    V15_SOURCE.is_dir()
    and V15_ASSETS.is_dir()
    and V15_TEMPLATES.is_dir()
    and all(path.is_file() for path in V15_MODULE_FILES)
)
# La identidad institucional se entrega una sola vez desde assets/logo.jpg.
# V15 mantiene sus SVG funcionales bajo assets/; su LOGO_PATH se adapta en
# admission_v15_adapter.py antes de construir la interfaz.
V15_IMAGE_FILES = ()
ADMISSION_VALIDATION_MIGRATION = (
    ROOT / "migrations" / "20260801_admission_validation_history.sql"
)
ARS_HONORARIUM_MIGRATION = (
    ROOT / "migrations" / "20260820_ars_honorarium_prompt.sql"
)
PRIMARY_LEASE_MIGRATION = (
    ROOT / "migrations" / "20260820_admission_primary_lease_generation.sql"
)

REQUIRED_FILES = [
    ROOT / "CALCULOS_QT.py",
    ROOT / "app_icons.py",
    ROOT / "app_resources.py",
    ROOT / "admission_contract.py",
    ROOT / "admission_bridge.py",
    ROOT / "admission_v15_adapter.py",
    ROOT / "admission_hybrid.py",
    ROOT / "admission_refresh_coordinator.py",
    ROOT / "patient_directory.py",
    ROOT / "patient_seed_tool.py",
    ROOT / "admission_database_import.py",
    ROOT / "sqlite_write_coordinator.py",
    ROOT / "offline_auth.py",
    ROOT / "admission_pyside6" / "__init__.py",
    ADMISSION_SOURCE / "facturacion_tabs.py",
    ROOT / "private_insurance_exporter.py",
    ROOT / "lanzador.py",
    ROOT / "updater.py",
    ROOT / "config_local.py",
    ROOT / "database_config.py",
    ROOT / "sigeh_product.py",
    ROOT / "sigeh_update.py",
    ROOT / "display_layout.py",
    ROOT / "historical_documents.py",
    ROOT / "receipt_documents.py",
    ROOT / "report_documents.py",
    ROOT / "responsive_validation.py",
    ADMISSION_VALIDATION_MIGRATION,
    ARS_HONORARIUM_MIGRATION,
    PRIMARY_LEASE_MIGRATION,
    SUMATRA_PDF,
    ASSETS / "logo.jpg",
    ASSETS / "favicon.ico",
    PDF_ENGINE / "__init__.py",
    PDF_ENGINE / "renderer.py",
    PDF_ENGINE / "template.html",
    PDF_ENGINE / "styles.css",
    REPORT_ENGINE / "__init__.py",
    REPORT_ENGINE / "data_service.py",
    REPORT_ENGINE / "excel_exporter.py",
    REPORT_ENGINE / "html_renderer.py",
    REPORT_ENGINE / "query.py",
    REPORT_ENGINE / "report_template.html",
    REPORT_ENGINE / "report_styles.css",
    ROOT / "version_config.json",
    ADMISSION_CORE / "__init__.py",
    *V15_MODULE_FILES,
    *V15_IMAGE_FILES,
    *(
        (
            V15_TEMPLATES / "EMERGENCIA GENERAL.pdf",
            V15_TEMPLATES / "EMERGENCIA GINECOLOGIA.pdf",
            V15_TEMPLATES / "EMERGENCIA PEDIATRICA.pdf",
        )
        if V15_AVAILABLE
        else ()
    ),
]
missing_files = [str(path) for path in REQUIRED_FILES if not path.is_file()]
missing_directories = []
if V15_AVAILABLE:
    missing_directories = [
        str(path) for path in (V15_ASSETS, V15_TEMPLATES) if not path.is_dir()
    ]
if missing_files or missing_directories:
    raise FileNotFoundError(
        "Faltan recursos requeridos para compilar:\n"
        + "\n".join(missing_files + missing_directories)
    )

if str(V15_SOURCE.parent) not in sys.path:
    sys.path.insert(0, str(V15_SOURCE.parent))


def find_playwright_browser():
    """Localiza el Chromium Headless Shell de la versión instalada."""
    playwright_spec = find_spec("playwright")
    if playwright_spec is None or not playwright_spec.submodule_search_locations:
        raise RuntimeError("Playwright no está instalado en el Python usado para compilar.")

    playwright_dir = Path(next(iter(playwright_spec.submodule_search_locations))).resolve()
    manifest_path = playwright_dir / "driver" / "package" / "browsers.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    browser_info = next(
        browser
        for browser in manifest["browsers"]
        if browser["name"] == "chromium-headless-shell"
    )
    revision = str(browser_info["revision"])
    folder_names = (
        f"chromium_headless_shell-{revision}",
        f"chromium-headless-shell-{revision}",
    )

    browser_roots = []
    configured_root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured_root == "0":
        browser_roots.append(playwright_dir / "driver" / "package" / ".local-browsers")
    elif configured_root:
        browser_roots.append(Path(configured_root).expanduser())

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        browser_roots.append(Path(local_app_data) / "ms-playwright")
    browser_roots.append(playwright_dir / "driver" / "package" / ".local-browsers")

    browser_dir = next(
        (
            root / folder_name
            for root in browser_roots
            for folder_name in folder_names
            if (root / folder_name).is_dir()
        ),
        None,
    )
    if browser_dir is None:
        raise FileNotFoundError(
            "No se encontró Chromium Headless Shell para Playwright "
            f"(revisión {revision}). Instálalo con el mismo Python del build: "
            "python -m playwright install chromium"
        )
    return browser_dir


PLAYWRIGHT_BROWSER = find_playwright_browser()
PLAYWRIGHT_DATAS = collect_data_files("playwright")
PLAYWRIGHT_HIDDEN_IMPORTS = collect_submodules("playwright")
OPENPYXL_DATAS = collect_data_files("openpyxl")
OPENPYXL_HIDDEN_IMPORTS = collect_submodules("openpyxl")
ADMISSION_CORE_HIDDEN_IMPORTS = [
    f"emergency_core.{path.stem}"
    for path in sorted(ADMISSION_CORE.glob("*.py"))
    if path.stem != "__init__"
]
V15_HIDDEN_IMPORTS = collect_submodules(V15_PACKAGE) if V15_AVAILABLE else []
ADMISSION_PYSIDE6_HIDDEN_IMPORTS = collect_submodules("admission_pyside6")
TTKBOOTSTRAP_HIDDEN_IMPORTS = collect_submodules("ttkbootstrap")

main_datas = [
    (str(DATABASE_BUNDLE), "."),
    (str(ASSETS / "logo.jpg"), "assets"),
    (str(ASSETS / "favicon.ico"), "assets"),
    (str(PDF_ENGINE / "template.html"), "pdf_engine"),
    (str(PDF_ENGINE / "styles.css"), "pdf_engine"),
    (str(REPORT_ENGINE / "report_template.html"), "report_engine"),
    (str(REPORT_ENGINE / "report_styles.css"), "report_engine"),
    (str(ROOT / "version_config.json"), "."),
    (str(ADMISSION_VALIDATION_MIGRATION), "migrations"),
    (str(ARS_HONORARIUM_MIGRATION), "migrations"),
    (str(PRIMARY_LEASE_MIGRATION), "migrations"),
    (str(ADMISSION_CORE), "admission_source/emergency_core"),
    (str(ADMISSION_SOURCE / "facturacion_tabs.py"), "admission_source"),
    # V15 busca SumatraPDF directamente en sys._MEIPASS. En onedir este
    # destino queda en _internal/SumatraPDF.exe y permite imprimir sin una
    # instalación externa ni una ruta absoluta del equipo de desarrollo.
    (str(SUMATRA_PDF), "."),
    *[(str(source_path), V15_PACKAGE) for source_path in V15_IMAGE_FILES],
    *([(str(V15_ASSETS), f"{V15_PACKAGE}/assets")] if V15_AVAILABLE else []),
    *([(str(V15_TEMPLATES), f"{V15_PACKAGE}/HOJAS")] if V15_AVAILABLE else []),
    # V15 resuelve plantillas e identidad desde sys._MEIPASS al ejecutarse
    # empaquetada; se incluyen tambiÃ©n en la raÃ­z interna sin cambiar V15.
    *([(str(V15_TEMPLATES), "HOJAS")] if V15_AVAILABLE else []),
    *[(str(source_path), ".") for source_path in V15_IMAGE_FILES],
    (
        str(PLAYWRIGHT_BROWSER),
        f"playwright-browsers/{PLAYWRIGHT_BROWSER.name}",
    ),
] + PLAYWRIGHT_DATAS + OPENPYXL_DATAS

main_hidden_imports = sorted(
    set(
        [
            "pdf_engine",
            "pdf_engine.renderer",
            "psycopg2",
            "psycopg2.extras",
            "psycopg2.pool",
            "docx",
            "dotenv",
            "playwright.sync_api",
            "openpyxl",
            "historical_documents",
            "receipt_documents",
            "report_documents",
            # V15 se congela como paquete Python desde la raíz actual. Los
            # recursos físicos de V15 se agregan exclusivamente en ``datas``.
            "logging.handlers",
            "PyPDF2",
            "admission_hybrid",
            "admission_refresh_coordinator",
            "patient_directory",
            "patient_seed_tool",
            "admission_database_import",
            "offline_auth",
            "tkinter",
        ]
        + PLAYWRIGHT_HIDDEN_IMPORTS
        + OPENPYXL_HIDDEN_IMPORTS
        + ADMISSION_CORE_HIDDEN_IMPORTS
        + V15_HIDDEN_IMPORTS
        + ADMISSION_PYSIDE6_HIDDEN_IMPORTS
        + TTKBOOTSTRAP_HIDDEN_IMPORTS
    )
)


main_analysis = Analysis(
    [str(ROOT / "CALCULOS_QT.py")],
    pathex=[str(ROOT), str(ADMISSION_SOURCE), str(V15_SOURCE.parent)],
    binaries=[],
    datas=main_datas,
    hiddenimports=main_hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
main_pyz = PYZ(main_analysis.pure)
main_exe = EXE(
    main_pyz,
    main_analysis.scripts,
    [],
    exclude_binaries=True,
    name="CALCULOS_QT",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ASSETS / "favicon.ico")],
)


launcher_analysis = Analysis(
    [str(ROOT / "lanzador.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
launcher_pyz = PYZ(launcher_analysis.pure)
launcher_exe = EXE(
    launcher_pyz,
    launcher_analysis.scripts,
    [],
    exclude_binaries=True,
    name="SIGEH",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(ASSETS / "favicon.ico")],
)


distribution = COLLECT(
    launcher_exe,
    main_exe,
    launcher_analysis.binaries,
    launcher_analysis.datas,
    main_analysis.binaries,
    main_analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="SIGEH",
)
