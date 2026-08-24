"""Verified SQLite backups with manifests and retention."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .io_utils import ConfigError, atomic_write_json, load_json_file


class BackupError(RuntimeError):
    """Raised when backup creation or validation fails."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_member_name(value: object) -> str:
    """Return a manifest member name only when it is a plain file name."""
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise BackupError("El manifiesto contiene un nombre de archivo invalido")
    if Path(value).name != value or "/" in value or "\\" in value:
        raise BackupError("El manifiesto intenta acceder fuera del respaldo")
    return value


class BackupManager:
    def __init__(
        self,
        db_path: str | os.PathLike[str],
        backup_root: str | os.PathLike[str],
        related_paths: Iterable[str | os.PathLike[str]] = (),
        retention_days: int = 4,
    ) -> None:
        self.db_path = Path(db_path)
        self.backup_root = Path(backup_root)
        self.related_paths = tuple(Path(path) for path in related_paths)
        self.retention_days = max(1, int(retention_days))
        self.backup_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def check_database(path: str | os.PathLike[str]) -> None:
        try:
            with closing(sqlite3.connect(path)) as conn:
                row = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.Error as exc:
            raise BackupError(f"No se pudo abrir el respaldo: {exc}") from exc
        if not row or row[0] != "ok":
            raise BackupError(f"El respaldo no supero integrity_check: {row}")

    def create(self, reason: str, *, label: str | None = None) -> Path:
        if not self.db_path.exists():
            raise BackupError(f"No existe la base de datos: {self.db_path}")
        existing_related = [path for path in self.related_paths if path.exists() and path.is_file()]
        names = [self.db_path.name, *(path.name for path in existing_related)]
        if len(names) != len(set(names)):
            raise BackupError("Dos archivos del respaldo tienen el mismo nombre")
        safe_reason = "".join(ch if ch.isalnum() else "_" for ch in reason.strip()).strip("_") or "manual"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        folder = self.backup_root / f"{stamp}_{safe_reason}"
        folder.mkdir(parents=False, exist_ok=False)
        db_copy = folder / self.db_path.name
        try:
            with closing(sqlite3.connect(self.db_path)) as source, closing(sqlite3.connect(db_copy)) as target:
                source.backup(target)
            self.check_database(db_copy)

            files = []
            for source_path in existing_related:
                target_path = folder / source_path.name
                shutil.copy2(source_path, target_path)
                files.append(
                    {
                        "name": target_path.name,
                        "bytes": target_path.stat().st_size,
                        "sha256": _sha256(target_path),
                    }
                )
            files.insert(
                0,
                {
                    "name": db_copy.name,
                    "bytes": db_copy.stat().st_size,
                    "sha256": _sha256(db_copy),
                },
            )
            manifest = {
                "format": 1,
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "reason": reason,
                "label": label or "",
                "database": self.db_path.name,
                "files": files,
            }
            atomic_write_json(folder / "manifest.json", manifest)
            self.verify(folder)
            self.prune()
            return folder
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def ensure_daily(self) -> Path | None:
        self.prune()
        today = datetime.now().date().isoformat()
        for folder in self.list_backups():
            try:
                manifest = self._manifest(folder)
            except BackupError:
                continue
            if manifest.get("reason") == "respaldo_diario" and str(manifest.get("created_at", "")).startswith(today):
                return None
        return self.create("respaldo_diario")

    def _manifest(self, folder: Path) -> dict:
        try:
            value = load_json_file(folder / "manifest.json", default={})
        except ConfigError as exc:
            raise BackupError(f"No se pudo leer el manifiesto: {exc}") from exc
        return value if isinstance(value, dict) else {}

    def list_backups(self) -> list[Path]:
        return sorted(
            (path for path in self.backup_root.iterdir() if path.is_dir() and (path / "manifest.json").exists()),
            reverse=True,
        )

    def verify(self, folder: str | os.PathLike[str]) -> dict:
        backup_dir = Path(folder)
        manifest = self._manifest(backup_dir)
        files = manifest.get("files")
        if manifest.get("format") != 1 or not isinstance(files, list) or not files:
            raise BackupError("El manifiesto del respaldo no es valido")
        database_name = _safe_member_name(manifest.get("database"))
        seen: set[str] = set()
        for item in files:
            if not isinstance(item, dict):
                raise BackupError("El manifiesto contiene una entrada de archivo invalida")
            name = _safe_member_name(item.get("name"))
            if name in seen:
                raise BackupError(f"El manifiesto repite el archivo {name}")
            seen.add(name)
            path = backup_dir / name
            try:
                expected_bytes = int(item["bytes"])
                expected_hash = str(item["sha256"])
            except (KeyError, TypeError, ValueError) as exc:
                raise BackupError(f"Los metadatos de {name} no son validos") from exc
            if expected_bytes < 0 or len(expected_hash) != 64:
                raise BackupError(f"Los metadatos de {name} no son validos")
            if path.resolve().parent != backup_dir.resolve():
                raise BackupError(f"El archivo {name} apunta fuera del respaldo")
            if not path.is_file() or path.stat().st_size != expected_bytes:
                raise BackupError(f"Falta o cambio el archivo {name}")
            if _sha256(path) != expected_hash:
                raise BackupError(f"El hash no coincide para {name}")
        if database_name not in seen:
            raise BackupError("La base de datos no figura en la lista de archivos")
        self.check_database(backup_dir / database_name)
        return manifest

    def restore_database(self, folder: str | os.PathLike[str]) -> Path:
        backup_dir = Path(folder)
        manifest = self.verify(backup_dir)
        source = backup_dir / _safe_member_name(manifest["database"])
        temp = self.db_path.with_suffix(self.db_path.suffix + ".restore.tmp")
        temp.unlink(missing_ok=True)
        try:
            # Stage the selected copy before creating the safety backup. Creating
            # that backup also prunes retention and may remove an old source.
            shutil.copy2(source, temp)
            self.check_database(temp)
            safety = self.create("antes_de_restaurar")
            if self.db_path.exists():
                with closing(sqlite3.connect(self.db_path)) as conn:
                    conn.execute("PRAGMA wal_checkpoint(FULL)")
            for suffix in ("-wal", "-shm"):
                Path(str(self.db_path) + suffix).unlink(missing_ok=True)
            os.replace(temp, self.db_path)
            self.check_database(self.db_path)
            return safety
        finally:
            temp.unlink(missing_ok=True)

    def prune(self) -> int:
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        removed = 0
        inspected = set()
        for folder in self.list_backups():
            inspected.add(folder.resolve())
            try:
                created = datetime.fromisoformat(str(self._manifest(folder).get("created_at", "")))
            except (BackupError, ValueError):
                created = datetime.fromtimestamp(folder.stat().st_mtime)
            if created < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += int(not folder.exists())
        # Limpia también respaldos antiguos de formatos previos que no tenían manifiesto.
        for path in self.backup_root.iterdir():
            if path.resolve() in inspected:
                continue
            try:
                stamps = re.findall(r"(20\d{6})_(\d{6})", path.name)
                modified = (
                    datetime.strptime("_".join(stamps[-1]), "%Y%m%d_%H%M%S")
                    if stamps
                    else datetime.fromtimestamp(path.stat().st_mtime)
                )
            except OSError:
                continue
            if modified >= cutoff:
                continue
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed += int(not path.exists())
        return removed
