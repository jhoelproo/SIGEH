"""Integración local de la aplicación completa de Admisión.

El flujo normal carga la fuente Tk existente dentro del proceso principal y
Qt bombea sus eventos. La ventana nativa de Tk se adopta dentro de la página
Emergencias. El ejecutable incluido se conserva únicamente como compatibilidad
para distribuciones antiguas que todavía no contengan la fuente integrada.
"""

from __future__ import annotations

from dataclasses import dataclass
import ctypes
import importlib.util
import logging
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping


ADMISSION_EXECUTABLE_NAME = "GENERADOR DE HOJAS 4.1.exe"
ADMISSION_EXECUTABLE_ENV = "HOSPITAL_ADMISSION_APP_PATH"
ADMISSION_SOURCE_ENV = "HOSPITAL_ADMISSION_SOURCE_PATH"
ADMISSION_DATA_DIR_ENV = "EMERGENCIAS_DATA_DIR"
ADMISSION_MUTEX_NAME = r"Global\HospitalProvincialAdmissionApp"

_KNOWN_ROLES = {
    "auxiliar",
    "administrador",
    "facturador de auditoria",
    "auditoria medica y cuentas",
}

_LOGGER = logging.getLogger(__name__)


class AdmissionModuleError(RuntimeError):
    """Recoverable error while locating or opening the Admission module."""


@dataclass(frozen=True)
class AdmissionLaunchResult:
    executable: Path
    started: bool
    pid: int | None


def admission_data_dir(env: Mapping[str, str] | None = None) -> Path:
    """Return the local data directory used by the Admission application."""
    values = os.environ if env is None else env
    configured = str(values.get(ADMISSION_DATA_DIR_ENV, "") or "").strip()
    if configured:
        configured_path = Path(configured).expanduser()
        appdata_roots = [
            Path(str(values.get(name, "") or "")).expanduser()
            for name in ("APPDATA", "LOCALAPPDATA")
            if str(values.get(name, "") or "").strip()
        ]
        resolved = configured_path.resolve(strict=False)
        if not any(
            resolved == root.resolve(strict=False)
            or root.resolve(strict=False) in resolved.parents
            for root in appdata_roots
        ):
            return configured_path
        _LOGGER.warning(
            "Se ignoró una ruta heredada de AppData como fuente de Admisión."
        )

    program_data = str(values.get("PROGRAMDATA", "") or "").strip()
    if program_data:
        return (
            Path(program_data)
            / "Hospital"
            / "GeneradorHojasEmergencia"
        )

    return Path.home() / "Hospital" / "GeneradorHojasEmergencia"


def admission_executable_candidates(
    *,
    app_dir: os.PathLike[str] | str | None = None,
    bundle_dir: os.PathLike[str] | str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Build the ordered list of supported executable locations."""
    values = os.environ if env is None else env
    app_root = Path(
        app_dir
        or (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
    )
    bundle_root = Path(
        bundle_dir or getattr(sys, "_MEIPASS", app_root)
    )

    candidates: list[Path] = []
    configured = str(values.get(ADMISSION_EXECUTABLE_ENV, "") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())

    candidates.extend(
        (
            bundle_root
            / "admission_module"
            / ADMISSION_EXECUTABLE_NAME,
            app_root
            / "admission_module"
            / ADMISSION_EXECUTABLE_NAME,
            app_root / ADMISSION_EXECUTABLE_NAME,
        )
    )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized not in seen:
            seen.add(normalized)
            unique.append(candidate)
    return tuple(unique)


def resolve_admission_executable(
    *,
    app_dir: os.PathLike[str] | str | None = None,
    bundle_dir: os.PathLike[str] | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    """Locate the bundled Admission executable without touching the network."""
    for candidate in admission_executable_candidates(
        app_dir=app_dir,
        bundle_dir=bundle_dir,
        env=env,
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def admission_source_candidates(
    *,
    app_dir: os.PathLike[str] | str | None = None,
    bundle_dir: os.PathLike[str] | str | None = None,
    env: Mapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Locate the vendored Tk source used by the in-process Emergency page."""
    values = os.environ if env is None else env
    app_root = Path(
        app_dir
        or (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
    )
    bundle_root = Path(bundle_dir or getattr(sys, "_MEIPASS", app_root))
    candidates: list[Path] = []
    configured = str(values.get(ADMISSION_SOURCE_ENV, "") or "").strip()
    if configured:
        candidates.append(Path(configured).expanduser())
    candidates.extend(
        (
            bundle_root / "admission_source" / "facturacion_tabs.py",
            app_root / "admission_source" / "facturacion_tabs.py",
        )
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normcase(os.path.abspath(candidate))
        if normalized not in seen:
            seen.add(normalized)
            unique.append(candidate)
    return tuple(unique)


def resolve_admission_source(
    *,
    app_dir: os.PathLike[str] | str | None = None,
    bundle_dir: os.PathLike[str] | str | None = None,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    for candidate in admission_source_candidates(
        app_dir=app_dir,
        bundle_dir=bundle_dir,
        env=env,
    ):
        if candidate.is_file():
            return candidate.resolve()
    return None


def admission_instance_running() -> bool:
    """Return whether Admission currently owns its machine-wide mutex."""
    if os.name != "nt":
        return False
    synchronize = 0x00100000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.OpenMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.OpenMutexW(synchronize, False, ADMISSION_MUTEX_NAME)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def admission_session_environment(
    user_context: Mapping[str, object] | None,
    session_id: str,
) -> dict[str, str]:
    """Build the minimal, non-secret identity contract for Admission."""
    user = user_context or {}

    def clean(value: object, maximum: int) -> str:
        return "".join(
            character for character in str(value or "").strip()
            if ord(character) >= 32 and ord(character) != 127
        )[:maximum]

    role = clean(user.get("role", ""), 80).casefold()
    if role not in _KNOWN_ROLES:
        role = "auxiliar"
    return {
        "HOSPITAL_LAUNCHED_FROM_BILLING": "1",
        "HOSPITAL_USERNAME": clean(user.get("username", ""), 80),
        "HOSPITAL_FULL_NAME": clean(user.get("full_name", ""), 160),
        "HOSPITAL_ROLE": role,
        "HOSPITAL_SESSION_ID": clean(session_id, 160),
    }


class AdmissionModuleController:
    """Integra Admisión usando la identidad actual y el mínimo privilegio."""

    def __init__(
        self,
        *,
        app_dir: os.PathLike[str] | str | None = None,
        bundle_dir: os.PathLike[str] | str | None = None,
        user_context: Mapping[str, object] | None = None,
        session_id: str = "",
        auth_broker=None,
    ):
        self.app_dir = app_dir
        self.bundle_dir = bundle_dir
        self.user_context = dict(user_context or {})
        self.session_id = str(session_id or "")
        self.auth_broker = auth_broker
        self._process: subprocess.Popen | None = None
        self._native_hwnd: int | None = None
        self._embedded_hwnd: int | None = None
        self._embedded_module = None
        self._embedded_app = None

    @property
    def executable(self) -> Path | None:
        return resolve_admission_executable(
            app_dir=self.app_dir,
            bundle_dir=self.bundle_dir,
        )

    @property
    def source(self) -> Path | None:
        return resolve_admission_source(
            app_dir=self.app_dir,
            bundle_dir=self.bundle_dir,
        )

    @property
    def is_running(self) -> bool:
        if self._embedded_app is not None:
            return True
        own_process_running = (
            self._process is not None and self._process.poll() is None
        )
        return own_process_running or admission_instance_running()

    @property
    def pid(self) -> int | None:
        if self._embedded_app is not None:
            return os.getpid()
        return (
            int(self._process.pid)
            if self._process is not None and self._process.poll() is None
            else None
        )

    @property
    def embedded(self) -> bool:
        return bool(self._embedded_hwnd)

    def load_source_module(self):
        """Carga únicamente contratos y servicios originales, sin crear Tk/App."""
        if self._embedded_module is not None:
            return self._embedded_module
        source = self.source
        if source is None:
            raise AdmissionModuleError("No se encontró la fuente integrada de Admisión.")
        child_environment = os.environ.copy()
        data_dir = admission_data_dir(child_environment)
        session_environment = admission_session_environment(
            self.user_context, self.session_id
        )
        session_environment.update(
            {
                "EMERGENCIAS_DATA_DIR": str(data_dir),
                "ADMISSION_DB_PATH": str(data_dir / "pacientes.db"),
                "HOSPITAL_OFFLINE": "1",
            }
        )
        if self.auth_broker is not None:
            session_environment.update(self.auth_broker.environment())
        previous = {key: os.environ.get(key) for key in session_environment}
        source_root = str(source.parent)
        path_inserted = source_root not in sys.path
        if path_inserted:
            sys.path.insert(0, source_root)
        try:
            os.environ.update(session_environment)
            module_name = "_hospital_native_admission_backend"
            module = sys.modules.get(module_name)
            if module is None:
                spec = importlib.util.spec_from_file_location(module_name, source)
                if spec is None or spec.loader is None:
                    raise AdmissionModuleError(
                        "No se pudo preparar el backend integrado de Admisión."
                    )
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            session_loader = getattr(module, "load_session_context", None)
            if callable(session_loader):
                module.ADMISSION_SESSION = session_loader()
            self._embedded_module = module
            return module
        except AdmissionModuleError:
            raise
        except Exception as exc:
            _LOGGER.exception("No se pudo cargar el backend nativo de Admisión")
            raise AdmissionModuleError(
                f"No se pudo cargar el backend integrado de Admisión: {exc}"
            ) from exc
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if path_inserted:
                try:
                    sys.path.remove(source_root)
                except ValueError:
                    pass

    def _find_process_window(self) -> int | None:
        """Localiza la ventana visible creada por este proceso de AdmisiÃ³n."""
        if os.name != "nt" or not self.pid:
            return None
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.EnumWindows.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.EnumWindows.restype = ctypes.c_bool
        user32.GetWindowThreadProcessId.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_uint32),
        ]
        user32.GetWindowThreadProcessId.restype = ctypes.c_uint32
        user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        user32.IsWindowVisible.restype = ctypes.c_bool
        user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        user32.GetWindow.restype = ctypes.c_void_p
        matches: list[int] = []
        enum_proc = ctypes.WINFUNCTYPE(
            ctypes.c_bool,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )

        @enum_proc
        def callback(hwnd, _lparam):
            process_id = ctypes.c_uint32()
            user32.GetWindowThreadProcessId(
                ctypes.c_void_p(hwnd),
                ctypes.byref(process_id),
            )
            if int(process_id.value) == int(self.pid):
                if user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                    owner = user32.GetWindow(
                        ctypes.c_void_p(hwnd),
                        4,  # GW_OWNER
                    )
                    if not owner:
                        matches.append(int(hwnd))
            return True

        user32.EnumWindows(callback, 0)
        return matches[0] if matches else None

    def embed_into(
        self,
        container_hwnd: int,
        width: int,
        height: int,
    ) -> bool:
        """Integra visualmente la ventana Tk dentro del área Qt principal."""
        if os.name != "nt" or not container_hwnd:
            return False
        hwnd = (
            self._embedded_hwnd
            or self._native_hwnd
            or self._find_process_window()
        )
        if not hwnd:
            return False
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gwl_style = -16
        ws_child = 0x40000000
        ws_visible = 0x10000000
        ws_caption = 0x00C00000
        ws_thickframe = 0x00040000
        ws_sysmenu = 0x00080000
        ws_minimizebox = 0x00020000
        ws_maximizebox = 0x00010000
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        get_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        set_long.restype = ctypes.c_ssize_t
        user32.SetParent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        user32.SetParent.restype = ctypes.c_void_p
        user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.ShowWindow.restype = ctypes.c_bool
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        user32.SetWindowPos.restype = ctypes.c_bool
        style = int(get_long(ctypes.c_void_p(hwnd), gwl_style))
        style &= ~(
            ws_caption
            | ws_thickframe
            | ws_sysmenu
            | ws_minimizebox
            | ws_maximizebox
        )
        style |= ws_child | ws_visible
        set_long(ctypes.c_void_p(hwnd), gwl_style, style)
        previous_parent = user32.SetParent(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(int(container_hwnd)),
        )
        if not previous_parent and ctypes.get_last_error() not in (0,):
            return False
        self._embedded_hwnd = int(hwnd)
        user32.ShowWindow(ctypes.c_void_p(hwnd), 5)
        user32.SetWindowPos(
            ctypes.c_void_p(hwnd),
            ctypes.c_void_p(0),  # HWND_TOP dentro del host Qt.
            0,
            0,
            max(1, int(width)),
            max(1, int(height)),
            0x0020 | 0x0040,  # SWP_FRAMECHANGED | SWP_SHOWWINDOW
        )
        self.resize_embedded(width, height)
        return True

    def resize_embedded(self, width: int, height: int) -> None:
        if os.name != "nt" or not self._embedded_hwnd:
            return
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.MoveWindow.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_bool,
        ]
        user32.MoveWindow.restype = ctypes.c_bool
        user32.MoveWindow(
            ctypes.c_void_p(self._embedded_hwnd),
            0,
            0,
            max(1, int(width)),
            max(1, int(height)),
            True,
        )
        user32.SetWindowPos.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint32,
        ]
        user32.SetWindowPos.restype = ctypes.c_bool
        user32.SetWindowPos(
            ctypes.c_void_p(self._embedded_hwnd),
            ctypes.c_void_p(0),
            0,
            0,
            max(1, int(width)),
            max(1, int(height)),
            0x0040,  # SWP_SHOWWINDOW
        )

    def launch(self) -> AdmissionLaunchResult:
        source = self.source
        if self._embedded_app is not None and source is not None:
            return AdmissionLaunchResult(
                executable=source,
                started=False,
                pid=os.getpid(),
            )
        if source is not None:
            return self._launch_in_process(source)

        executable = self.executable
        if executable is None:
            searched = "\n".join(
                f"• {path}"
                for path in admission_executable_candidates(
                    app_dir=self.app_dir,
                    bundle_dir=self.bundle_dir,
                )
            )
            raise AdmissionModuleError(
                "No se encontró el módulo completo de Admisión.\n\n"
                f"Ubicaciones revisadas:\n{searched}"
            )

        if self.is_running:
            return AdmissionLaunchResult(
                executable=executable,
                started=False,
                pid=self.pid,
            )

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",
                0,
            )

        try:
            child_environment = os.environ.copy()
            data_dir = admission_data_dir(child_environment)
            child_environment["EMERGENCIAS_DATA_DIR"] = str(data_dir)
            child_environment["ADMISSION_DB_PATH"] = str(
                data_dir / "pacientes.db"
            )
            child_environment["HOSPITAL_OFFLINE"] = "1"
            child_environment.update(
                admission_session_environment(
                    self.user_context,
                    self.session_id,
                )
            )
            if self.auth_broker is not None:
                child_environment.update(self.auth_broker.environment())
            self._process = subprocess.Popen(
                [str(executable)],
                cwd=str(executable.parent),
                env=child_environment,
                close_fds=True,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise AdmissionModuleError(
                f"No se pudo abrir la aplicación de Admisión:\n{exc}"
            ) from exc

        return AdmissionLaunchResult(
            executable=executable,
            started=True,
            pid=self.pid,
        )

    def _launch_in_process(self, source: Path) -> AdmissionLaunchResult:
        """Load the existing Tk application in this process without mainloop."""
        child_environment = os.environ.copy()
        data_dir = admission_data_dir(child_environment)
        session_environment = admission_session_environment(
            self.user_context,
            self.session_id,
        )
        session_environment.update(
            {
                "EMERGENCIAS_DATA_DIR": str(data_dir),
                "ADMISSION_DB_PATH": str(data_dir / "pacientes.db"),
                "HOSPITAL_OFFLINE": "1",
            }
        )
        if self.auth_broker is not None:
            session_environment.update(self.auth_broker.environment())

        previous = {
            key: os.environ.get(key)
            for key in session_environment
        }
        source_root = str(source.parent)
        path_inserted = source_root not in sys.path
        if path_inserted:
            sys.path.insert(0, source_root)
        try:
            os.environ.update(session_environment)
            module_name = "_hospital_embedded_admission"
            spec = importlib.util.spec_from_file_location(module_name, source)
            if spec is None or spec.loader is None:
                raise AdmissionModuleError(
                    "No se pudo preparar el modulo integrado de Admision."
                )
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)
            application_type = getattr(module, "App", None)
            if application_type is None:
                raise AdmissionModuleError(
                    "La fuente de Admision no expone su aplicacion principal."
                )
            embedded_app = application_type()
            root = getattr(embedded_app, "root", None)
            if root is None:
                raise AdmissionModuleError(
                    "Admision no creo una ventana integrable."
                )
            root.update_idletasks()
            root.update()
            self._embedded_module = module
            self._embedded_app = embedded_app
            tk_child_hwnd = int(root.winfo_id())
            self._native_hwnd = tk_child_hwnd
            if os.name == "nt":
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.GetAncestor.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                ]
                user32.GetAncestor.restype = ctypes.c_void_p
                top_level = user32.GetAncestor(
                    ctypes.c_void_p(tk_child_hwnd),
                    2,  # GA_ROOT: el marco nativo de nivel superior de Tk.
                )
                if top_level:
                    self._native_hwnd = int(top_level)
        except AdmissionModuleError:
            raise
        except Exception as exc:
            _LOGGER.exception("No se pudo iniciar Admision en el proceso principal")
            raise AdmissionModuleError(
                f"No se pudo iniciar Admision integrada: {exc}"
            ) from exc
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            if path_inserted:
                try:
                    sys.path.remove(source_root)
                except ValueError:
                    pass
        return AdmissionLaunchResult(
            executable=source,
            started=True,
            pid=os.getpid(),
        )

    def pump_embedded_events(self) -> bool:
        application = self._embedded_app
        root = getattr(application, "root", None)
        if root is None:
            return False
        try:
            root.update_idletasks()
            root.update()
            return True
        except Exception:
            self._embedded_app = None
            self._embedded_module = None
            self._native_hwnd = None
            self._embedded_hwnd = None
            return False

    def close(self, timeout: float = 4.0) -> bool:
        """Cierra solo la instancia de Admisión perteneciente a esta sesión."""
        embedded_app = self._embedded_app
        if embedded_app is not None:
            root = getattr(embedded_app, "root", None)
            try:
                if root is not None:
                    root.destroy()
            except Exception:
                pass
            self._embedded_app = None
            self._embedded_module = None
            self._native_hwnd = None
            self._embedded_hwnd = None
            return True
        process = self._process
        if process is None or process.poll() is not None:
            self._process = None
            return False
        try:
            if os.name == "nt":
                completed = subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    check=False,
                    capture_output=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if completed.returncode != 0 and process.poll() is None:
                    process.terminate()
            else:
                process.terminate()
            process.wait(timeout=max(0.5, float(timeout)))
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
        except OSError:
            pass
        finally:
            self._process = None
            self._native_hwnd = None
            self._embedded_hwnd = None
        return True
