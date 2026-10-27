"""Create optional deltas alongside the backwards-compatible complete release."""

import hashlib
import json
import shutil
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from sigeh_product import PRODUCT_ID
from sigeh_update import UpdateError, sha256_file, version_tuple
from update_payload import DELTA_MANIFEST, archive_entries, distribution_path
from updater import UPDATE_PRESERVE_PATHS


def archive_version(archive: ZipFile) -> str:
    data = json.loads(archive.read("SIGEH/version_config.json"))
    if data.get("product") != PRODUCT_ID:
        raise UpdateError("El paquete base no pertenece a SIGEH.")
    version = str(data.get("version", ""))
    version_tuple(version)
    return version


def archive_file_index(archive: ZipFile) -> list[dict]:
    result = []
    for name, info in sorted(archive_entries(archive).items()):
        digest = hashlib.sha256()
        with archive.open(name) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        result.append(
            {
                "relative_path": name,
                "size": info.file_size,
                "sha256": digest.hexdigest(),
            }
        )
    return result


def _ships_preserved_default(name: str) -> bool:
    relative = distribution_path(name)
    return any(
        relative == preserved or preserved in relative.parents
        for preserved in UPDATE_PRESERVE_PATHS
    )


def create_delta(target_path: Path, base_path: Path, output: Path) -> dict:
    with ZipFile(target_path) as target, ZipFile(base_path) as base:
        version, base_version = archive_version(target), archive_version(base)
        if version_tuple(version) <= version_tuple(base_version):
            raise UpdateError("La versión incremental debe superar la versión base.")
        files = archive_file_index(target)
        previous = {row["relative_path"]: row for row in archive_file_index(base)}
        metadata = {
            "product": PRODUCT_ID,
            "format": 1,
            "version": version,
            "from_version": base_version,
            "files": files,
        }
        with ZipFile(output, "w", compression=ZIP_DEFLATED, compresslevel=6) as delta:
            delta.writestr(DELTA_MANIFEST, json.dumps(metadata, ensure_ascii=False))
            for row in files:
                name = row["relative_path"]
                if previous.get(name) != row or _ships_preserved_default(name):
                    with (
                        target.open(name) as source,
                        delta.open(name, "w") as destination,
                    ):
                        shutil.copyfileobj(source, destination)
    return {
        "from_version": base_version,
        "asset": output.name,
        "sha256": sha256_file(output),
        "size": output.stat().st_size,
    }
