"""Resumable, content-addressed update transfers outside the GUI thread."""

from collections.abc import Callable
from pathlib import Path
import re

import requests

from sigeh_update import UpdateError, sha256_file, verify_archive

Progress = Callable[[int, int], None]
CHUNK_SIZE = 256 * 1024


def _response_size(response, offset: int) -> tuple[int, int]:
    if response.status_code == 200:
        return 0, int(response.headers.get("Content-Length", 0))
    match = re.fullmatch(
        r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
    )
    if response.status_code != 206 or not match:
        raise UpdateError("El servidor no devolvió un rango de descarga válido.")
    start, end, total = map(int, match.groups())
    if start != offset or not start <= end < total:
        raise UpdateError("El rango de descarga no coincide con el archivo local.")
    if end != total - 1:
        raise UpdateError("El servidor devolvió un rango de descarga incompleto.")
    return start, total


def _transfer(url: str, partial: Path, progress: Progress, get) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    with get(url, headers=headers, stream=True, timeout=(15, 60)) as response:
        response.raise_for_status()
        start, total = _response_size(response, offset)
        downloaded = start
        progress(downloaded, total)
        with partial.open("ab" if start else "wb") as stream:
            for chunk in response.iter_content(CHUNK_SIZE):
                if chunk:
                    stream.write(chunk)
                    downloaded += len(chunk)
                    progress(downloaded, total)
        if total and downloaded != total:
            raise UpdateError(f"Descarga incompleta: {downloaded} de {total} bytes.")


def _cached_archive(cache: Path, checksum: str) -> Path | None:
    for suffix in (".zip", ".part"):
        candidate = cache / f"{checksum}{suffix}"
        if candidate.is_file() and sha256_file(candidate) == checksum:
            final = cache / f"{checksum}.zip"
            if candidate != final:
                candidate.replace(final)
            return final
    return None


def download_archive(
    url: str,
    checksum: str,
    cache: Path,
    *,
    progress: Progress = lambda _done, _total: None,
    get=None,
) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise UpdateError("No existe un SHA-256 SIGEH válido para el paquete.")
    cache.mkdir(parents=True, exist_ok=True)
    cached = _cached_archive(cache, checksum)
    if cached:
        progress(cached.stat().st_size, cached.stat().st_size)
        return cached
    partial = cache / f"{checksum}.part"
    # A complete but invalid local partial cannot be resumed after HTTP 416.
    try:
        _transfer(url, partial, progress, get or requests.get)
    except requests.HTTPError as exc:
        if exc.response is None or exc.response.status_code != 416:
            raise
        partial.unlink(missing_ok=True)
        _transfer(url, partial, progress, get or requests.get)
    try:
        verify_archive(partial, checksum)
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    final = cache / f"{checksum}.zip"
    partial.replace(final)
    return final
