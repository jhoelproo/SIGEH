"""Descubrimiento y validación de releases exclusivos de SIGEH."""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sigeh_product import APP_VERSION, GITHUB_REPOSITORY, PRODUCT_ID

LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"


class UpdateError(RuntimeError):
    """El canal remoto no pudo validarse de forma segura."""


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    name: str
    notes: str
    archive_url: str
    archive_name: str
    checksum_url: str
    manifest_url: str
    html_url: str


@dataclass(frozen=True)
class VerifiedReleasePayload:
    release: ReleaseInfo
    sha256: str


def version_tuple(value: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", str(value or "").strip().lstrip("vV"))
    if not match:
        raise UpdateError(f"Versión no válida: {value!r}")
    return tuple(int(part) for part in match.groups())


def is_newer(candidate: str, current: str = APP_VERSION) -> bool:
    return version_tuple(candidate) > version_tuple(current)


def release_asset_names(version: str) -> tuple[str, str, str]:
    normalized = ".".join(str(part) for part in version_tuple(version))
    archive = f"SIGEH-{normalized}-windows-x64.zip"
    return archive, archive + ".sha256", f"SIGEH-{normalized}-manifest.json"


def _request_bytes(url: str, *, timeout: int) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"SIGEH/{APP_VERSION}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError(f"No se pudo consultar el canal SIGEH: {exc}") from exc


def _request_json(url: str, *, timeout: int = 12) -> dict:
    try:
        value = json.loads(_request_bytes(url, timeout=timeout).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("El canal SIGEH devolvió información inválida.") from exc
    if not isinstance(value, dict):
        raise UpdateError("El canal SIGEH no devolvió un objeto válido.")
    return value


def _stable_release_version(data: dict) -> str:
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("La última publicación SIGEH no es estable.")
    version = str(data.get("tag_name") or "").strip().lstrip("vV")
    version_tuple(version)
    return version


def _validated_release_url(data: dict) -> str:
    html_url = str(data.get("html_url") or "")
    expected_prefix = f"https://github.com/{GITHUB_REPOSITORY}/releases/"
    if not html_url.startswith(expected_prefix):
        raise UpdateError(
            "La publicación no pertenece al repositorio SIGEH autorizado."
        )
    return html_url


def _release_assets(data: dict) -> dict[str, dict]:
    return {
        str(item.get("name") or ""): item
        for item in data.get("assets", ())
        if isinstance(item, dict)
    }


def _require_release_assets(assets: dict[str, dict], names: tuple[str, ...]) -> None:
    missing = [name for name in names if name not in assets]
    if missing:
        raise UpdateError("La publicación SIGEH está incompleta: " + ", ".join(missing))


def parse_release(data: dict) -> ReleaseInfo:
    version = _stable_release_version(data)
    archive_name, checksum_name, manifest_name = release_asset_names(version)
    html_url = _validated_release_url(data)
    assets = _release_assets(data)
    _require_release_assets(assets, (archive_name, checksum_name, manifest_name))
    return ReleaseInfo(
        version=version,
        name=str(data.get("name") or f"SIGEH {version}"),
        notes=str(data.get("body") or "").strip(),
        archive_url=str(assets[archive_name].get("browser_download_url") or ""),
        archive_name=archive_name,
        checksum_url=str(assets[checksum_name].get("browser_download_url") or ""),
        manifest_url=str(assets[manifest_name].get("browser_download_url") or ""),
        html_url=html_url,
    )


def get_latest_release(
    api_url: str = LATEST_RELEASE_API, timeout: int = 12
) -> ReleaseInfo:
    if api_url != LATEST_RELEASE_API:
        raise UpdateError("SIGEH rechazó un canal de actualización no autorizado.")
    return parse_release(_request_json(api_url, timeout=timeout))


def parse_checksum(text: str, expected_name: str) -> str:
    for raw_line in str(text or "").splitlines():
        match = re.fullmatch(r"([A-Fa-f0-9]{64})\s+\*?(.+)", raw_line.strip())
        if match and Path(match.group(2)).name == expected_name:
            return match.group(1).lower()
    raise UpdateError("El checksum SIGEH no contiene el paquete esperado.")


def _load_manifest(release: ReleaseInfo, timeout: int) -> dict:
    try:
        manifest = json.loads(
            _request_bytes(release.manifest_url, timeout=timeout).decode("utf-8")
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UpdateError("El manifest SIGEH no es válido.") from exc
    if not isinstance(manifest, dict):
        raise UpdateError("El manifest SIGEH no es un objeto.")
    return manifest


def _validate_manifest(manifest: dict, release: ReleaseInfo) -> str:
    if str(manifest.get("product") or "") != PRODUCT_ID:
        raise UpdateError("El paquete pertenece a otro producto.")
    if str(manifest.get("version") or "") != release.version:
        raise UpdateError("La versión del manifest no coincide con el release.")
    if str(manifest.get("asset") or "") != release.archive_name:
        raise UpdateError("El asset del manifest no coincide con el release.")
    if str(manifest.get("entrypoint") or "") != "SIGEH.exe":
        raise UpdateError("El manifest no declara el entrypoint SIGEH.exe.")
    if str(manifest.get("updater") or "") != "SIGEH_Updater.exe":
        raise UpdateError("El manifest no declara SIGEH_Updater.exe.")
    if not str(manifest.get("published_at") or "").strip():
        raise UpdateError("El manifest no declara la fecha de publicación.")
    minimum_version = str(manifest.get("minimum_supported_version") or "")
    version_tuple(minimum_version)
    manifest_checksum = str(manifest.get("sha256") or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", manifest_checksum):
        raise UpdateError("El manifest no contiene un SHA-256 válido.")
    return manifest_checksum


def resolve_release_payload(
    release: ReleaseInfo, *, timeout: int = 20
) -> VerifiedReleasePayload:
    manifest_checksum = _validate_manifest(_load_manifest(release, timeout), release)
    checksum_text = _request_bytes(release.checksum_url, timeout=timeout).decode(
        "ascii"
    )
    checksum = parse_checksum(checksum_text, release.archive_name)
    if checksum != manifest_checksum:
        raise UpdateError("Manifest y checksum SIGEH no coinciden.")
    return VerifiedReleasePayload(release=release, sha256=checksum)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def verify_archive(path: str | Path, expected_sha256: str) -> None:
    expected = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise UpdateError("No existe un SHA-256 SIGEH válido para el paquete.")
    if sha256_file(path) != expected:
        raise UpdateError("El paquete SIGEH no supera la validación SHA-256.")
