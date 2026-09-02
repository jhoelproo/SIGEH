"""Release guard and deterministic packaging for the SIGEH update channel."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from database_config import read_bundled_database_url
from sigeh_product import APP_VERSION, PRODUCT_ID

REQUIRED_DIST_ENTRIES = (
    Path("SIGEH.exe"),
    Path("SIGEH_Updater.exe"),
    Path("CALCULOS_QT.exe"),
    Path("_internal"),
    Path("version_config.json"),
    Path("_internal/version_config.json"),
)

VERSION_METADATA_PATHS = (
    Path("version_config.json"),
    Path("_internal/version_config.json"),
)
OPERATIONAL_DATABASE_SUFFIXES = (
    ".db",
    ".sqlite",
    ".sqlite3",
    ".db-wal",
    ".db-shm",
)
FORBIDDEN_RELEASE_NAMES = {
    ".env",
    "database_url.bundle",
    "database_url.protected",
}
FORBIDDEN_RELEASE_SUFFIXES = (".log", ".tmp", ".pyc", ".pyo")
FORBIDDEN_RELEASE_DIRECTORIES = {"__pycache__"}


def validate_no_operational_database(dist_dir: Path) -> None:
    packaged_databases = tuple(
        path.relative_to(dist_dir)
        for path in Path(dist_dir).rglob("*")
        if path.is_file() and path.name.lower().endswith(OPERATIONAL_DATABASE_SUFFIXES)
    )
    if packaged_databases:
        raise ValueError(
            "La distribución contiene una base operacional y podría sobrescribir "
            "el historial: " + ", ".join(map(str, packaged_databases))
        )


def validate_no_credentials_or_runtime_files(dist_dir: Path) -> None:
    forbidden = tuple(
        path.relative_to(dist_dir)
        for path in Path(dist_dir).rglob("*")
        if path.is_file()
        and (
            path.name.casefold() in FORBIDDEN_RELEASE_NAMES
            or path.name.casefold().endswith(FORBIDDEN_RELEASE_SUFFIXES)
            or any(
                parent.name.casefold() in FORBIDDEN_RELEASE_DIRECTORIES
                for parent in path.parents
                if parent != dist_dir
            )
        )
    )
    if forbidden:
        raise ValueError(
            "La distribución contiene credenciales o archivos runtime: "
            + ", ".join(map(str, forbidden))
        )


def validate_dist(dist_dir: Path) -> tuple[Path, ...]:
    dist_dir = Path(dist_dir).resolve()
    missing = tuple(
        relative
        for relative in REQUIRED_DIST_ENTRIES
        if not (dist_dir / relative).exists()
    )
    if missing:
        raise FileNotFoundError(
            "Distribución SIGEH incompleta: " + ", ".join(map(str, missing))
        )
    validate_no_operational_database(dist_dir)
    validate_no_credentials_or_runtime_files(dist_dir)
    return tuple(dist_dir / relative for relative in REQUIRED_DIST_ENTRIES)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def write_dist_version_metadata(dist_dir: Path, version: str) -> None:
    """Make launcher metadata agree with the release being packaged."""
    metadata = json.dumps(
        {"product": PRODUCT_ID, "version": version},
        ensure_ascii=False,
    )
    for relative_path in VERSION_METADATA_PATHS:
        destination = Path(dist_dir) / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(metadata + "\n", encoding="utf-8")


def _validate_internal_bundle(path: Path) -> Path:
    bundle = Path(path).resolve()
    if not bundle.is_file() or not read_bundled_database_url(bundle):
        raise ValueError("backend_bundle no es un contenedor portable SIGEH válido.")
    return bundle


def _zip_file_manifest(archive_path: Path) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    with ZipFile(archive_path) as archive:
        for info in sorted(archive.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            digest = hashlib.sha256()
            with archive.open(info) as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            files.append(
                {
                    "relative_path": info.filename,
                    "size": info.file_size,
                    "sha256": digest.hexdigest(),
                }
            )
    return files


def prepare_release(
    dist_dir: Path,
    updater_exe: Path,
    output_dir: Path,
    *,
    version: str = APP_VERSION,
    internal_deployment: bool = False,
    backend_bundle: Path | None = None,
) -> dict:
    dist_dir = Path(dist_dir).resolve()
    updater_exe = Path(updater_exe).resolve()
    output_dir = Path(output_dir).resolve()
    if backend_bundle is not None and not internal_deployment:
        raise ValueError(
            "backend_bundle requiere internal_deployment; nunca se agrega al ZIP público."
        )
    if internal_deployment and backend_bundle is None:
        raise ValueError("internal_deployment requiere backend_bundle explícito.")
    validated_bundle = (
        _validate_internal_bundle(backend_bundle)
        if backend_bundle is not None
        else None
    )
    if not updater_exe.is_file():
        raise FileNotFoundError(f"No existe updater ONEFILE: {updater_exe}")
    shutil.copy2(updater_exe, dist_dir / "SIGEH_Updater.exe")
    write_dist_version_metadata(dist_dir, version)
    validate_dist(dist_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    channel = "internal" if internal_deployment else "public"
    channel_suffix = "-internal" if internal_deployment else ""
    asset_name = f"SIGEH-{version}{channel_suffix}-windows-x64.zip"
    archive_path = output_dir / asset_name
    with ZipFile(
        archive_path, "w", compression=ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path("SIGEH") / path.relative_to(dist_dir))
        if validated_bundle is not None:
            archive.write(
                validated_bundle,
                Path("SIGEH") / "_internal" / "database_url.bundle",
            )
    checksum = sha256_file(archive_path)
    checksum_path = output_dir / f"{asset_name}.sha256"
    checksum_path.write_text(f"{checksum} *{asset_name}\n", encoding="ascii")
    packaged_at = datetime.now(timezone.utc).isoformat()
    files = _zip_file_manifest(archive_path)
    component_paths = {
        "SIGEH.exe": "SIGEH/SIGEH.exe",
        "CALCULOS_QT.exe": "SIGEH/CALCULOS_QT.exe",
        "SIGEH_Updater.exe": "SIGEH/SIGEH_Updater.exe",
    }
    hashes_by_path = {str(item["relative_path"]): item["sha256"] for item in files}
    components = {
        name: hashes_by_path[relative_path]
        for name, relative_path in component_paths.items()
    }
    manifest = {
        "product": PRODUCT_ID,
        "version": version,
        "asset": asset_name,
        "sha256": checksum,
        "entrypoint": "SIGEH.exe",
        "updater": "SIGEH_Updater.exe",
        "packaged_at": packaged_at,
        "published_at": packaged_at,
        "minimum_supported_version": "1.0.0",
        "distribution_channel": channel,
        "publishable": not internal_deployment,
        "backend_bootstrap": (
            "portable_bundle" if internal_deployment else "external_provisioning"
        ),
        "components": components,
    }
    manifest_path = output_dir / f"SIGEH-{version}{channel_suffix}-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    file_manifest = {
        "product": PRODUCT_ID,
        "version": version,
        "distribution_channel": channel,
        "archive": asset_name,
        "archive_sha256": checksum,
        "generated_at": packaged_at,
        "files": files,
    }
    file_manifest_path = (
        output_dir / f"SIGEH-{version}{channel_suffix}-files-manifest.json"
    )
    file_manifest_path.write_text(
        json.dumps(file_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "archive": archive_path,
        "checksum": checksum_path,
        "manifest": manifest_path,
        "file_manifest": file_manifest_path,
        "sha256": checksum,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--updater", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", default=APP_VERSION)
    parser.add_argument("--internal-deployment", action="store_true")
    parser.add_argument("--backend-bundle", type=Path)
    args = parser.parse_args()
    result = prepare_release(
        args.dist,
        args.updater,
        args.output,
        version=args.version,
        internal_deployment=args.internal_deployment,
        backend_bundle=args.backend_bundle,
    )
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
