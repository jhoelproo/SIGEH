"""Application resource and operational data locations."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP_VENDOR = "Hospital"
APP_NAME = "SIGEH"


def source_or_executable_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_root() -> Path:
    """Persistent storage for this distribution only.

    Resources may be extracted into ``sys._MEIPASS`` by PyInstaller and are not
    a writable data location.  The onedir distribution, on the other hand,
    owns a stable ``_internal/data`` directory.  Development uses the same
    shape beneath this checkout so it never silently opens a historical
    AppData or legacy installation database.
    """
    override = os.environ.get("EMERGENCIAS_DATA_DIR", "").strip()
    if override:
        root = Path(override).expanduser().resolve()
    else:
        root = source_or_executable_dir() / "_internal" / "data"
    root.mkdir(parents=True, exist_ok=True)
    return root


def legacy_candidates() -> list[Path]:
    base = source_or_executable_dir()
    candidates = [base]
    if getattr(sys, "frozen", False):
        candidates.append(base.parent)
    result = []
    for candidate in candidates:
        if candidate not in result:
            result.append(candidate)
    return result


def migrate_legacy_files(file_names: tuple[str, ...]) -> list[tuple[Path, Path]]:
    """Explicit legacy import helper; normal startup never calls this routine."""
    destination_root = data_root()
    copied: list[tuple[Path, Path]] = []
    for name in file_names:
        if not isinstance(name, str) or not name or Path(name).name != name or "/" in name or "\\" in name:
            raise ValueError(f"Nombre de archivo heredado invalido: {name!r}")
        destination = destination_root / name
        if destination.exists():
            continue
        for candidate in legacy_candidates():
            source = candidate / name
            if not source.exists() or source.resolve() == destination.resolve():
                continue
            temp = destination.with_suffix(destination.suffix + ".importing")
            temp.unlink(missing_ok=True)
            try:
                shutil.copy2(source, temp)
                os.replace(temp, destination)
            finally:
                temp.unlink(missing_ok=True)
            copied.append((source, destination))
            break
    return copied


def harden_windows_acl(path: str | os.PathLike[str]) -> bool:
    """Repair inheritance, then restrict the data root with inheritable entries."""
    if os.name != "nt":
        return True
    identity_result = subprocess.run(
        ["whoami"], check=False, capture_output=True, text=True
    )
    identity = identity_result.stdout.strip() if identity_result.returncode == 0 else ""
    if not identity:
        return False
    target = str(Path(path).resolve())
    repair = subprocess.run(
        ["icacls", target, "/inheritance:e", "/T", "/C", "/Q"],
        check=False,
        capture_output=True,
        text=True,
    )
    if repair.returncode != 0:
        return False
    command = [
        "icacls",
        target,
        "/inheritance:r",
        "/grant:r",
        f"{identity}:(OI)(CI)M",
        "*S-1-5-18:(OI)(CI)F",
        "*S-1-5-32-544:(OI)(CI)F",
        "/Q",
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    return False
