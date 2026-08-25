"""Minimal, dependency-light bootstrap used by the public SIGEH launcher."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

from database_config import install_database_url_for_child


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def show_error(message: str) -> None:
    if sys.platform.startswith("win"):
        ctypes.windll.user32.MessageBoxW(None, str(message), "SIGEH", 0x10)


def main() -> int:
    root = portable_root()
    executable = root / "CALCULOS_QT.exe"
    if not executable.is_file():
        if "--self-test" not in sys.argv[1:]:
            show_error("No se encontró CALCULOS_QT.exe junto a SIGEH.")
        return 2
    if not (root / "_internal").is_dir():
        if "--self-test" not in sys.argv[1:]:
            show_error("La instalación de SIGEH está incompleta.")
        return 3
    environment = os.environ.copy()
    if not install_database_url_for_child(environment, base_dir=root):
        if "--self-test" not in sys.argv[1:]:
            show_error(
                "No fue posible preparar la conexión a la base de datos central."
            )
        return 5
    if "--self-test" in sys.argv[1:]:
        return 0
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
    subprocess.Popen(
        [str(executable)],
        cwd=str(root),
        env=environment,
        close_fds=True,
        creationflags=flags,
    )
    return 0
