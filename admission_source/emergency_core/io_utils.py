"""Reliable local file IO helpers."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


class ConfigError(RuntimeError):
    """Raised when a configuration file cannot be read or validated."""


def atomic_write_bytes(path: str | os.PathLike[str], payload: bytes) -> None:
    """Write bytes in the target directory and atomically replace the file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def atomic_write_json(path: str | os.PathLike[str], value: Any) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    atomic_write_bytes(path, payload)


def load_json_file(
    path: str | os.PathLike[str],
    *,
    default: Any = None,
    validator: Callable[[Any], bool] | None = None,
) -> Any:
    target = Path(path)
    if not target.exists():
        return default
    try:
        with target.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"No se pudo leer {target.name}: {exc}") from exc
    if validator is not None and not validator(value):
        raise ConfigError(f"El contenido de {target.name} no es valido")
    return value
