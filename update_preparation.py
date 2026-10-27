"""Prefer a compatible delta, then hand a full staged tree to the existing updater."""

from collections.abc import Callable
from pathlib import Path
from zipfile import BadZipFile

import requests

from sigeh_update import DeltaInfo, UpdateError
from update_download import Progress, download_archive
from update_payload import assemble_delta, extract_distribution


def _download_delta(delta: DeltaInfo, cache: Path, progress: Progress) -> Path | None:
    try:
        return download_archive(delta.url, delta.sha256, cache, progress=progress)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code in (404, 410):
            return None
        raise


def prepare_update(
    url: str,
    checksum: str,
    root: Path,
    install: Path,
    version: str,
    *,
    delta: DeltaInfo | None = None,
    progress: Progress = lambda _done, _total: None,
    status: Callable[[str], None] = lambda _message: None,
) -> Path:
    cache, staging = root / "downloads", root / "staging"
    if delta is not None:
        status("Descargando archivos modificados…")
        archive = _download_delta(delta, cache, progress)
        if archive is not None:
            status("Verificando y preparando archivos…")
            try:
                return assemble_delta(
                    archive, install, staging, version, delta.from_version
                )
            except (UpdateError, BadZipFile, KeyError, ValueError):
                status("Se requiere el paquete completo. Descargando…")
    else:
        status("Descargando actualización completa…")
    archive = download_archive(url, checksum, cache, progress=progress)
    status("Verificando y preparando instalación…")
    return extract_distribution(archive, staging)


def format_progress(
    downloaded: int, total: int, elapsed: float, transferred: int
) -> str:
    done_mb = downloaded / (1024 * 1024)
    text = f"{done_mb:.1f} MB"
    if total > 0:
        text += f" / {total / (1024 * 1024):.1f} MB"
    speed = max(0, transferred) / max(elapsed, 0.001)
    if speed > 0:
        text += f" · {speed / 1024:.0f} KB/s"
        if total > downloaded:
            seconds = int((total - downloaded) / speed)
            text += f" · {seconds // 60}:{seconds % 60:02d} restantes"
    return text
