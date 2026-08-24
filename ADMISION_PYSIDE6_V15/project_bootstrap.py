"""Localiza la fuente real de Admisión para reutilizar emergency_core.

Soporta la estructura real del proyecto HOSPITAL:
    hosp/admission_source/emergency_core

y también el layout directo:
    <raiz>/emergency_core

Este módulo solo resuelve rutas/imports. No modifica base de datos, turnos,
reportes, seguridad ni reglas funcionales de Admisión.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _parents(path: Path):
    path = path.resolve()
    yield path
    yield from path.parents


def _candidate_roots() -> list[Path]:
    here = Path(__file__).resolve().parent
    home = Path.home()
    candidates: list[Path] = []

    explicit = os.environ.get("HOSPITAL_PROJECT_ROOT", "").strip()
    if explicit:
        candidates.append(Path(explicit))

    explicit_admission = os.environ.get("HOSPITAL_ADMISSION_SOURCE", "").strip()
    if explicit_admission:
        candidates.append(Path(explicit_admission))

    candidates.extend(_parents(here))
    candidates.extend(_parents(Path.cwd()))

    candidates.extend([
        home / "OneDrive" / "Desktop" / "PROYECTOS" / "hosp",
        home / "Desktop" / "PROYECTOS" / "hosp",
        home / "OneDrive" / "Escritorio" / "PROYECTOS" / "hosp",
        home / "Escritorio" / "PROYECTOS" / "hosp",
    ])

    # Si el paquete fue copiado dentro de hosp, admission_source u otra subcarpeta.
    for parent in list(here.parents)[:6]:
        candidates.extend([
            parent,
            parent / "hosp",
            parent / "HOSPITAL",
            parent / "admission_source",
        ])

    seen = set()
    unique: list[Path] = []
    for item in candidates:
        try:
            resolved = item.expanduser().resolve()
        except Exception:
            continue
        key = os.path.normcase(str(resolved))
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _resolve_import_root(path: Path) -> Path | None:
    """Devuelve el directorio que debe estar en sys.path para importar emergency_core."""
    # Layout directo: <path>/emergency_core
    if (path / "emergency_core").is_dir():
        return path

    # Layout real del proyecto: <path>/admission_source/emergency_core
    admission_source = path / "admission_source"
    if (admission_source / "emergency_core").is_dir():
        return admission_source

    return None


def bootstrap_project_root(required: bool = True) -> Path | None:
    for candidate in _candidate_roots():
        import_root = _resolve_import_root(candidate)
        if import_root is None:
            continue

        text = str(import_root)
        if text not in sys.path:
            sys.path.insert(0, text)

        # Mantener compatibilidad con los lanzadores existentes. Para el proceso
        # de Admisión esta variable representa la raíz importable de emergency_core.
        os.environ["HOSPITAL_PROJECT_ROOT"] = text
        os.environ["HOSPITAL_ADMISSION_SOURCE"] = text
        return import_root

    if not required:
        return None

    checked = "\n".join(f"  - {p}" for p in _candidate_roots()[:16])
    raise ModuleNotFoundError(
        "No se encontró emergency_core del proyecto HOSPITAL.\n\n"
        "Se aceptan estos layouts:\n"
        "  <raiz>/emergency_core\n"
        "  <raiz>/admission_source/emergency_core\n\n"
        "Puede definir HOSPITAL_PROJECT_ROOT o HOSPITAL_ADMISSION_SOURCE.\n\n"
        "Rutas comprobadas:\n" + checked
    )


if __name__ == "__main__":
    root = bootstrap_project_root(required=False)
    if root:
        print(f"OK|{root}")
        raise SystemExit(0)
    print("ERROR|No se encontró emergency_core")
    raise SystemExit(2)
