"""Minimal, dependency-light bootstrap used by the public SIGEH launcher."""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from database_config import (
    describe_database_configuration,
    install_database_url_for_child,
)

LAUNCHER_LOG_NAME = "lanzador_log.txt"
_DATABASE_URI_CREDENTIALS = re.compile(r"(postgres(?:ql)?://)[^@\s]+@", re.IGNORECASE)


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def show_error(message: str) -> None:
    if sys.platform.startswith("win"):
        ctypes.windll.user32.MessageBoxW(None, str(message), "SIGEH", 0x10)


def _sanitize_log_text(value: object) -> str:
    text = str(value or "")
    return _DATABASE_URI_CREDENTIALS.sub(r"\1<redacted>@", text)


def _write_bootstrap_event(root: Path, event: str, **details: object) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **{key: _sanitize_log_text(value) for key, value in details.items()},
    }
    for boolean_key in ("credentials_present",):
        if boolean_key in details:
            payload[boolean_key] = bool(details[boolean_key])
    for numeric_key in ("port", "elapsed_ms"):
        if numeric_key in details:
            payload[numeric_key] = details[numeric_key]
    try:
        with (root / LAUNCHER_LOG_NAME).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _fail(root: Path, step: str, code: int, message: str) -> int:
    _write_bootstrap_event(
        root,
        "LAUNCH_BOOTSTRAP",
        step=step,
        status="FAIL",
        error_code=code,
    )
    if "--self-test" not in sys.argv[1:]:
        show_error(message)
    return code


def main() -> int:
    root = portable_root()
    _write_bootstrap_event(
        root, "LAUNCH_BOOTSTRAP", step="resolve_install_root", status="PASS"
    )
    executable = root / "CALCULOS_QT.exe"
    if not executable.is_file():
        return _fail(
            root,
            "resolve_main_binary",
            2,
            "No se encontró CALCULOS_QT.exe junto a SIGEH.",
        )
    _write_bootstrap_event(
        root, "LAUNCH_BOOTSTRAP", step="resolve_main_binary", status="PASS"
    )
    if not (root / "_internal").is_dir():
        return _fail(
            root, "verify_main_binary", 3, "La instalación de SIGEH está incompleta."
        )
    _write_bootstrap_event(
        root, "LAUNCH_BOOTSTRAP", step="verify_main_binary", status="PASS"
    )
    try:
        environment = os.environ.copy()
        _write_bootstrap_event(
            root, "LAUNCH_BOOTSTRAP", step="prepare_runtime", status="PASS"
        )
        description = describe_database_configuration(root, environment=environment)
        if not install_database_url_for_child(environment, base_dir=root):
            _write_bootstrap_event(
                root,
                "BACKEND_BOOTSTRAP",
                status="FAIL",
                error_code="CONFIGURATION_MISSING",
                **description,
            )
            return _fail(
                root,
                "prepare_config",
                5,
                "No fue posible preparar la conexión a la base de datos central.",
            )
        _write_bootstrap_event(
            root,
            "BACKEND_BOOTSTRAP",
            status="PASS",
            error_code="",
            **description,
        )
        _write_bootstrap_event(
            root, "CONFIG_RESOLUTION", step="prepare_config", status="PASS"
        )
        if "--self-test" in sys.argv[1:]:
            return 0
        flags = (
            subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
        )
        subprocess.Popen(
            [str(executable)],
            cwd=str(root),
            env=environment,
            close_fds=True,
            creationflags=flags,
        )
        _write_bootstrap_event(
            root, "LAUNCH_BOOTSTRAP", step="launch_main", status="PASS"
        )
        return 0
    except Exception as exc:
        _write_bootstrap_event(
            root,
            "LAUNCH_BOOTSTRAP",
            step="launch_main",
            status="FAIL",
            error_code="UNEXPECTED_BOOTSTRAP_ERROR",
            exception_type=type(exc).__name__,
            exception_message=str(exc),
            traceback=traceback.format_exc(),
        )
        if "--self-test" not in sys.argv[1:]:
            show_error(
                "La instalación no pudo preparar la aplicación principal. "
                "Revise lanzador_log.txt para conocer el detalle."
            )
        return 6
