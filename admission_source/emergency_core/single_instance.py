"""Cross-session Windows guard for the Admission desktop application."""

from __future__ import annotations

import ctypes
import os


ADMISSION_MUTEX_NAME = r"Global\HospitalProvincialAdmissionApp"
ERROR_ALREADY_EXISTS = 183


class SingleInstanceGuard:
    """Hold a named Windows mutex for the lifetime of the application."""

    def __init__(self, name: str = ADMISSION_MUTEX_NAME):
        self.name = name
        self._handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        if self._handle:
            return True

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_bool

        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "No se pudo crear el bloqueo de Admisión")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle = handle
        return True

    def release(self) -> None:
        if os.name == "nt" and self._handle:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle.restype = ctypes.c_bool
            kernel32.CloseHandle(self._handle)
        self._handle = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("Admisión ya está abierta")
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.release()
