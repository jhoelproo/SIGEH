"""Aplica de forma externa una actualizacion completa de la distribucion onedir."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from sigeh_product import PRODUCT_ID


WAIT_SECONDS = 60
PRESERVE_NAMES = ("recibos", "reportes", "respaldos")
PRESERVE_FILES = ("lanzador_log.txt", "pdf_performance.log")
PRESERVE_RELATIVE_DIRECTORIES = (Path("_internal") / "data",)
PRESERVE_RELATIVE_FILES = (
    Path("database_url.protected"),
    Path("database_url.bundle"),
    Path("_internal") / "database_url.protected",
    Path("_internal") / "database_url.bundle",
)


def write_log(log_path: Path, message: str) -> None:
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def wait_for_process(pid: int, timeout: int = WAIT_SECONDS) -> None:
    if pid <= 0:
        return
    if sys.platform.startswith("win"):
        synchronize = 0x00100000
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if handle:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(handle, timeout * 1000)
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        return

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.25)


def merge_preserved(backup_dir: Path, install_dir: Path) -> None:
    for name in PRESERVE_NAMES:
        source = backup_dir / name
        if source.is_dir():
            shutil.copytree(source, install_dir / name, dirs_exist_ok=True)
    for name in PRESERVE_FILES:
        source = backup_dir / name
        target = install_dir / name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)
    for relative in PRESERVE_RELATIVE_DIRECTORIES:
        source = backup_dir / relative
        if source.is_dir():
            shutil.copytree(source, install_dir / relative, dirs_exist_ok=True)
    for relative in PRESERVE_RELATIVE_FILES:
        source = backup_dir / relative
        target = install_dir / relative
        if source.is_file() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def start_launcher(install_dir: Path) -> None:
    launcher = install_dir / "INICIAR_SISTEMA.exe"
    if not launcher.is_file():
        raise FileNotFoundError(f"No existe el nuevo lanzador: {launcher}")
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
        )
    subprocess.Popen(
        [str(launcher)],
        cwd=str(install_dir),
        close_fds=True,
        creationflags=creationflags,
    )


def apply_update(manifest_path: Path, wait_pid: int) -> int:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    install_dir = Path(manifest["install_dir"]).resolve()
    payload_dir = Path(manifest["payload_dir"]).resolve()
    version = str(manifest.get("version", "desconocida"))
    if str(manifest.get("product") or "") != PRODUCT_ID:
        raise ValueError("El paquete de actualización no pertenece a SIGEH.")
    parent = install_dir.parent
    log_path = parent / "actualizador.log"
    backup_dir = parent / f".{install_dir.name}-backup-{int(time.time())}"

    required = (
        payload_dir / "CALCULOS_QT.exe",
        payload_dir / "INICIAR_SISTEMA.exe",
        payload_dir / "_internal",
    )
    if not all(path.exists() for path in required):
        write_log(log_path, "Paquete rechazado: faltan archivos requeridos.")
        return 2

    write_log(log_path, f"Esperando el cierre del lanzador para instalar {version}.")
    wait_for_process(wait_pid)
    # Da tiempo a que el lanzador onefile antiguo termine despues de iniciar
    # el puente de migracion. El helper ya se ejecuta fuera de install_dir.
    time.sleep(2.0)

    old_moved = False
    new_installed = False
    try:
        if install_dir.exists():
            os.replace(install_dir, backup_dir)
            old_moved = True
        shutil.move(str(payload_dir), str(install_dir))
        new_installed = True
        if old_moved:
            merge_preserved(backup_dir, install_dir)
        write_log(
            log_path, f"Version {version} instalada; respaldo: {backup_dir.name}."
        )
        start_launcher(install_dir)
        return 0
    except Exception as exc:
        write_log(log_path, f"Fallo instalando {version}: {exc!r}")
        try:
            if new_installed and install_dir.exists():
                failed_dir = parent / f".{install_dir.name}-failed-{int(time.time())}"
                os.replace(install_dir, failed_dir)
            if old_moved and backup_dir.exists():
                os.replace(backup_dir, install_dir)
                start_launcher(install_dir)
                write_log(log_path, "Se restauro y reinicio la version anterior.")
        except Exception as rollback_error:
            write_log(log_path, f"Tambien fallo la restauracion: {rollback_error!r}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--wait-pid", required=True, type=int)
    args = parser.parse_args()
    return apply_update(args.manifest, args.wait_pid)


if __name__ == "__main__":
    raise SystemExit(main())
