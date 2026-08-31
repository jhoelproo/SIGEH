"""Release guard and deterministic packaging for the SIGEH update channel."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

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
FORBIDDEN_RELEASE_SUFFIXES = (".log", ".tmp")


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


def prepare_release(
    dist_dir: Path,
    updater_exe: Path,
    output_dir: Path,
    *,
    version: str = APP_VERSION,
) -> dict:
    dist_dir = Path(dist_dir).resolve()
    updater_exe = Path(updater_exe).resolve()
    output_dir = Path(output_dir).resolve()
    if not updater_exe.is_file():
        raise FileNotFoundError(f"No existe updater ONEFILE: {updater_exe}")
    shutil.copy2(updater_exe, dist_dir / "SIGEH_Updater.exe")
    write_dist_version_metadata(dist_dir, version)
    validate_dist(dist_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    asset_name = f"SIGEH-{version}-windows-x64.zip"
    archive_path = output_dir / asset_name
    with ZipFile(
        archive_path, "w", compression=ZIP_DEFLATED, compresslevel=6
    ) as archive:
        for path in sorted(dist_dir.rglob("*")):
            if path.is_file():
                archive.write(path, Path("SIGEH") / path.relative_to(dist_dir))
    checksum = sha256_file(archive_path)
    checksum_path = output_dir / f"{asset_name}.sha256"
    checksum_path.write_text(f"{checksum} *{asset_name}\n", encoding="ascii")
    manifest = {
        "product": PRODUCT_ID,
        "version": version,
        "asset": asset_name,
        "sha256": checksum,
        "entrypoint": "SIGEH.exe",
        "updater": "SIGEH_Updater.exe",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "minimum_supported_version": "1.0.0",
    }
    manifest_path = output_dir / f"SIGEH-{version}-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "archive": archive_path,
        "checksum": checksum_path,
        "manifest": manifest_path,
        "sha256": checksum,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", required=True, type=Path)
    parser.add_argument("--updater", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", default=APP_VERSION)
    args = parser.parse_args()
    result = prepare_release(args.dist, args.updater, args.output, version=args.version)
    print(json.dumps({key: str(value) for key, value in result.items()}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
