"""Validated XLSX replacement with cooperative process locks and retained evidence."""

import os
import shutil
import sys
import tempfile
import threading
import zipfile
import uuid
from xml.etree.ElementTree import ParseError
from contextlib import contextmanager
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.exceptions import InvalidFileException


_LOCKS = tuple(threading.RLock() for _ in range(64))


@contextmanager
def _process_lock(path):
    with open(str(path) + ".lock", "a+b") as stream:
        if sys.platform == "win32":
            import msvcrt

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def artifact_lock(path):
    """Lock files stay in place so waiting processes always lock the same inode."""
    target = Path(path).resolve()
    lock = _LOCKS[hash(os.path.normcase(str(target))) % len(_LOCKS)]
    with lock, _process_lock(target):
        yield target


def validate_xlsx(path):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise zipfile.BadZipFile("XLSX CRC validation failed")
    with open(path, "rb") as source:
        workbook = load_workbook(source, read_only=True)
        try:
            for sheet in workbook:
                for _ in sheet.iter_rows():
                    pass
        finally:
            workbook.close()


@contextmanager
def _temporary_xlsx(target):
    descriptor, name = tempfile.mkstemp(
        prefix=target.stem + ".", suffix=".pending.xlsx", dir=target.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    try:
        yield temporary
    finally:
        temporary.unlink(missing_ok=True)


def xlsx_is_valid(path):
    try:
        validate_xlsx(path)
        return True
    except (
        FileNotFoundError,
        zipfile.BadZipFile,
        ParseError,
        KeyError,
        ValueError,
        InvalidFileException,
        EOFError,
    ):
        return False


def _retain_previous(target, recover_corrupt=False):
    if not target.exists():
        return
    # A corrupt artifact is evidence: refuse to replace it automatically.
    if recover_corrupt and not xlsx_is_valid(target):
        evidence = target.with_name(
            target.stem + ".corrupt-" + uuid.uuid4().hex + ".xlsx"
        )
        shutil.copy2(target, evidence)
        return
    validate_xlsx(target)
    backup = target.with_name(target.stem + ".last-valid.xlsx")
    with _temporary_xlsx(backup) as temporary:
        shutil.copy2(target, temporary)
        os.replace(temporary, backup)


def _replace_validated(target, writer, recover_corrupt=False):
    with artifact_lock(target) as destination:
        with _temporary_xlsx(destination) as temporary:
            writer(temporary)
            validate_xlsx(temporary)
            with open(temporary, "r+b") as stream:
                os.fsync(stream.fileno())
            _retain_previous(destination, recover_corrupt)
            os.replace(temporary, destination)


def save_workbook(workbook, target, *, recover_corrupt=False):
    """Keep workbook ownership with the caller, including after a failed save."""
    _replace_validated(target, workbook.save, recover_corrupt)


def copy_workbook(source, target, *, recover_corrupt=False):
    """Copy bytes without reserializing official formatting or embedded images."""
    _replace_validated(
        target, lambda temporary: shutil.copy2(source, temporary), recover_corrupt
    )
