"""Recursos institucionales compartidos por la aplicación principal."""

from __future__ import annotations

import sys
from pathlib import Path


APP_LOGO_RELATIVE_PATH = Path("assets") / "logo.jpg"


def resource_root() -> Path:
    """Devuelve la raíz de recursos, tanto en fuente como en PyInstaller."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)).resolve()


def get_app_logo_path() -> str:
    """Única ruta de logo institucional disponible durante la ejecución."""
    path = resource_root() / APP_LOGO_RELATIVE_PATH
    return str(path) if path.is_file() else ""


__all__ = ["APP_LOGO_RELATIVE_PATH", "get_app_logo_path", "resource_root"]
