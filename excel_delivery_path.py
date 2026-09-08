"""Validated short-path copies for Windows Office; originals remain untouched."""

import os
from pathlib import Path
import tempfile
import uuid

from excel_artifact import artifact_lock, copy_workbook, validate_xlsx


def prepare_excel_delivery(path, cache_directory=None):
    source = Path(path).resolve(strict=True)
    directory = Path(
        cache_directory
        or Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        / "SIGEH"
        / "Office"
    )
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / (uuid.uuid4().hex + ".xlsx")
    with artifact_lock(source):
        validate_xlsx(source)
        copy_workbook(source, target)
    return str(target)
