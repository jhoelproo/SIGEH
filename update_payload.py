"""Construct complete update payloads without writing into the live installation."""

import json
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import stat
import tempfile
from zipfile import ZipFile, ZipInfo

from sigeh_product import PRODUCT_ID
from sigeh_update import UpdateError, verify_archive

DELTA_MANIFEST = "sigeh-delta.json"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
RESERVED_NAME = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", re.I)


def distribution_path(name: str) -> Path:
    if not isinstance(name, str):
        raise UpdateError("El paquete contiene un nombre de archivo inválido.")
    parts = name.split("/")
    if parts[0] != "SIGEH" or len(parts) < 2:
        raise UpdateError("El paquete contiene una ruta fuera de SIGEH.")
    for part in parts:
        invalid = not part or part in (".", "..") or part.endswith((".", " "))
        if invalid or re.search(r'[\\:\x00-\x1f<>"|?*]', part):
            raise UpdateError("El paquete contiene una ruta no permitida.")
        if RESERVED_NAME.fullmatch(part.split(".", 1)[0]):
            raise UpdateError("El paquete contiene un nombre reservado de Windows.")
    return Path(*parts[1:])


def archive_entries(archive: ZipFile, *, delta: bool = False) -> dict[str, ZipInfo]:
    entries = {}
    seen = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        if not (delta and info.filename == DELTA_MANIFEST):
            distribution_path(info.filename)
        folded = info.filename.casefold()
        if folded in seen or stat.S_ISLNK(info.external_attr >> 16):
            raise UpdateError("El ZIP contiene enlaces o archivos duplicados.")
        seen.add(folded)
        entries[info.filename] = info
    return entries


def validate_distribution(payload: Path) -> Path:
    required = ("SIGEH.exe", "CALCULOS_QT.exe", "SIGEH_Updater.exe")
    if not all((payload / name).is_file() for name in required):
        raise UpdateError("El paquete no contiene una distribución SIGEH válida.")
    if not (payload / "_internal").is_dir():
        raise UpdateError("El paquete no contiene el runtime SIGEH.")
    return payload


@contextmanager
def _payload_workspace(staging: Path):
    staging.mkdir(parents=True, exist_ok=True)
    payload = Path(tempfile.mkdtemp(prefix="payload-", dir=staging))
    try:
        yield payload
    except Exception:
        if payload.resolve().is_relative_to(staging.resolve()):
            shutil.rmtree(payload)
        raise


def extract_distribution(archive_path: Path, staging: Path) -> Path:
    with _payload_workspace(staging) as payload, ZipFile(archive_path) as archive:
        entries = archive_entries(archive)
        for name in entries:
            destination = payload / distribution_path(name)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target)
        return validate_distribution(payload)


def read_delta_manifest(archive: ZipFile, version: str, base_version: str) -> dict:
    if archive.getinfo(DELTA_MANIFEST).file_size > MAX_MANIFEST_BYTES:
        raise UpdateError("El índice incremental excede el tamaño permitido.")
    data = json.loads(archive.read(DELTA_MANIFEST))
    expected = (PRODUCT_ID, 1, version, base_version)
    if (
        not isinstance(data, dict)
        or tuple(
            data.get(key) for key in ("product", "format", "version", "from_version")
        )
        != expected
    ):
        raise UpdateError("La versión del paquete incremental no corresponde.")
    return data


def manifest_files(data: dict) -> dict[str, dict]:
    files = data.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= 20000:
        raise UpdateError("El índice incremental no contiene archivos válidos.")
    result = {}
    seen = set()
    for row in files:
        if not isinstance(row, dict):
            raise UpdateError("El índice incremental contiene un archivo inválido.")
        name = row.get("relative_path", "")
        distribution_path(name)
        if name.casefold() in seen or not _valid_file_metadata(row):
            raise UpdateError("El índice incremental contiene metadatos inválidos.")
        seen.add(name.casefold())
        result[name] = row
    return result


def _valid_file_metadata(row: dict) -> bool:
    return (
        type(row.get("size")) is int
        and row["size"] >= 0
        and isinstance(row.get("sha256"), str)
        and re.fullmatch(r"[a-f0-9]{64}", row["sha256"]) is not None
    )


def _copy_local_file(install: Path, relative: Path, target: Path, row: dict) -> None:
    source = install / relative
    if not source.resolve().is_relative_to(install.resolve()) or not source.is_file():
        raise UpdateError(
            "Falta un archivo local requerido; se necesita el paquete completo."
        )
    if source.stat().st_size != row["size"]:
        raise UpdateError("El archivo local cambió; se necesita el paquete completo.")
    shutil.copyfile(source, target)


def _materialize_files(archive, files: dict, install: Path, payload: Path) -> None:
    entries = archive_entries(archive, delta=True)
    if set(entries) - {DELTA_MANIFEST} - set(files):
        raise UpdateError("El paquete incremental contiene archivos fuera del índice.")
    for name, row in files.items():
        relative = distribution_path(name)
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if name in entries:
            if entries[name].file_size != row["size"]:
                raise UpdateError("El tamaño del archivo incremental no coincide.")
            with archive.open(name) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
        else:
            _copy_local_file(install, relative, target, row)
        try:
            verify_archive(target, row["sha256"])
        except UpdateError as exc:
            raise UpdateError(
                "Un archivo incremental o local no supera SHA-256."
            ) from exc


def assemble_delta(
    archive_path: Path, install: Path, staging: Path, version: str, base_version: str
) -> Path:
    with _payload_workspace(staging) as payload, ZipFile(archive_path) as archive:
        manifest = read_delta_manifest(archive, version, base_version)
        _materialize_files(archive, manifest_files(manifest), install, payload)
        return validate_distribution(payload)
