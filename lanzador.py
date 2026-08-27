import sys
import os


def _fast_launch_main_before_gui_imports() -> int:
    """Abre la app con el bootstrap mínimo y su conexión central preparada."""
    from portable_launcher import main as launch_portable_main

    return launch_portable_main()


_EARLY_ARGS = list(sys.argv[1:])
if __name__ == "__main__" and _EARLY_ARGS == ["--self-test-fast-launch"]:
    raise SystemExit(0)
if __name__ == "__main__" and _EARLY_ARGS == ["--self-test"]:
    raise SystemExit(_fast_launch_main_before_gui_imports())

import json
import time
import hashlib
import shutil
import subprocess
import tempfile
import uuid
import zipfile
import requests
from pathlib import Path
from sigeh_product import APP_VERSION, PRODUCT_ID
from sigeh_visual_theme import visual_theme_tokens
from sigeh_update import get_latest_release, is_newer, resolve_release_payload
from PySide6.QtCore import Qt, QThread, Signal, QSettings
from PySide6.QtWidgets import QApplication, QDialog, QVBoxLayout, QLabel, QProgressBar, QMessageBox

MAIN_APP_NAME = "CALCULOS_QT.exe"
LAUNCHER_NAME = "SIGEH.exe"
UPDATER_NAME = "SIGEH_Updater.exe"
CONFIG_FILE = "version_config.json"
LOG_FILE = "lanzador_log.txt"
DEFAULT_VERSION = APP_VERSION
LOCAL_PREVIEW_PORT = "55432"
LOCAL_PREVIEW_USER = "preview_admin"
LOCAL_PREVIEW_DATABASE = "hospital_preview"

# ---> FUNCIÓN MAESTRA PARA RUTAS DE PYINSTALLER <---
def get_real_dir():
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def get_bundle_dir():
    """Carpeta de recursos de PyInstaller (``_internal`` en modo onedir)."""
    return getattr(sys, "_MEIPASS", get_real_dir())


def _postgres_creation_flags():
    if sys.platform.startswith("win"):
        return subprocess.CREATE_NO_WINDOW
    return 0


def _find_postgresql_bin():
    configured = os.environ.get("HOSPITAL_LOCAL_PG_BIN", "").strip()
    candidates = [configured] if configured else []

    if sys.platform.startswith("win"):
        program_files = [
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        ]
        for base_dir in program_files:
            postgres_root = os.path.join(base_dir, "PostgreSQL")
            if not os.path.isdir(postgres_root):
                continue
            versions = sorted(
                (
                    name
                    for name in os.listdir(postgres_root)
                    if os.path.isdir(os.path.join(postgres_root, name))
                ),
                reverse=True,
            )
            candidates.extend(os.path.join(postgres_root, version, "bin") for version in versions)

    for candidate in candidates:
        if candidate and os.path.isfile(os.path.join(candidate, "pg_ctl.exe")):
            return candidate
    raise RuntimeError(
        "No se encontró PostgreSQL local. Instale PostgreSQL o configure "
        "HOSPITAL_LOCAL_PG_BIN con la carpeta bin."
    )


def prepare_local_preview_database():
    """Inicia la base aislada de prueba y devuelve el entorno para la app."""
    pg_bin = _find_postgresql_bin()
    preview_root = os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "HospitalFacturacionPreview",
    )
    data_dir = os.path.join(preview_root, "PostgresData")
    log_path = os.path.join(preview_root, "postgres.log")
    os.makedirs(preview_root, exist_ok=True)

    pg_ctl = os.path.join(pg_bin, "pg_ctl.exe")
    initdb = os.path.join(pg_bin, "initdb.exe")
    createdb = os.path.join(pg_bin, "createdb.exe")
    psql = os.path.join(pg_bin, "psql.exe")
    flags = _postgres_creation_flags()

    if not os.path.isfile(os.path.join(data_dir, "PG_VERSION")):
        result = subprocess.run(
            [initdb, "-D", data_dir, "-U", LOCAL_PREVIEW_USER, "-A", "trust"],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "No se pudo crear la base local.")

    status = subprocess.run(
        [pg_ctl, "-D", data_dir, "status"],
        capture_output=True,
        text=True,
        creationflags=flags,
    )
    if status.returncode != 0:
        result = subprocess.run(
            [
                pg_ctl,
                "-D",
                data_dir,
                "-l",
                log_path,
                "-o",
                f"-p {LOCAL_PREVIEW_PORT} -h 127.0.0.1",
                "-w",
                "start",
            ],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "No se pudo iniciar la base local.")

    database_exists = subprocess.run(
        [
            psql,
            "-h",
            "127.0.0.1",
            "-p",
            LOCAL_PREVIEW_PORT,
            "-U",
            LOCAL_PREVIEW_USER,
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname='{LOCAL_PREVIEW_DATABASE}'",
        ],
        capture_output=True,
        text=True,
        creationflags=flags,
    )
    if database_exists.returncode != 0:
        raise RuntimeError(database_exists.stderr.strip() or "No se pudo comprobar la base local.")
    if database_exists.stdout.strip() != "1":
        result = subprocess.run(
            [
                createdb,
                "-h",
                "127.0.0.1",
                "-p",
                LOCAL_PREVIEW_PORT,
                "-U",
                LOCAL_PREVIEW_USER,
                LOCAL_PREVIEW_DATABASE,
            ],
            capture_output=True,
            text=True,
            creationflags=flags,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "No se pudo crear la base local.")

    environment = os.environ.copy()
    environment["DATABASE_URL"] = (
        f"postgresql://{LOCAL_PREVIEW_USER}@127.0.0.1:"
        f"{LOCAL_PREVIEW_PORT}/{LOCAL_PREVIEW_DATABASE}"
    )
    admission_data = os.path.join(
        os.environ.get("PROGRAMDATA", os.environ.get("LOCALAPPDATA", preview_root)),
        "Hospital",
        "SIGEH",
    )
    environment["EMERGENCIAS_DATA_DIR"] = admission_data
    environment["ADMISSION_DB_PATH"] = os.path.join(
        admission_data,
        "pacientes.db",
    )
    environment["HOSPITAL_OFFLINE"] = "1"
    return environment


def write_launcher_log(message: str):
    """Guarda diagnósticos sin detener el inicio con ventanas de OK."""
    try:
        log_path = os.path.join(get_real_dir(), LOG_FILE)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except Exception:
        pass


def _show_launch_error(message: str):
    """Muestra un error real sin crear una QApplication en el camino normal."""
    if sys.platform.startswith("win"):
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None,
                str(message),
                "Sistema Hospitalario",
                0x10,
            )
            return
        except Exception:
            pass


def launch_main_app_immediately() -> int:
    """Abre la aplicación mediante el bootstrap portátil canónico."""
    return _fast_launch_main_before_gui_imports()


def run_update_check_ui() -> int:
    """Valida y aplica actualizaciones antes de abrir la aplicación principal."""
    app = QApplication.instance() or QApplication(sys.argv)
    launcher = LauncherDialog()
    launcher.show()
    return int(app.exec())


def version_tuple(version: str):
    """Convierte '1.1.7' en (1, 1, 7) para comparar correctamente versiones."""
    try:
        parts = tuple(int(part) for part in str(version).strip().split("."))
        return parts + (0,) * max(0, 3 - len(parts))
    except Exception:
        return (0, 0, 0)


def is_remote_newer(remote_version: str, local_version: str) -> bool:
    return version_tuple(remote_version) > version_tuple(local_version)


def get_local_version():
    config_paths = [
        os.path.join(get_real_dir(), CONFIG_FILE),
        os.path.join(get_bundle_dir(), CONFIG_FILE),
    ]
    for config_path in dict.fromkeys(config_paths):
        if not os.path.exists(config_path):
            continue
        try:
            with open(config_path, 'r', encoding="utf-8") as f:
                config = json.load(f)
                if str(config.get("product") or "") != PRODUCT_ID:
                    continue
                return config.get("version", DEFAULT_VERSION)
        except Exception as e:
            write_launcher_log(f"No se pudo leer version_config.json: {e}")
    return DEFAULT_VERSION


def save_local_version(version):
    config_path = os.path.join(get_real_dir(), CONFIG_FILE)
    with open(config_path, 'w', encoding="utf-8") as f:
        json.dump({"product": PRODUCT_ID, "version": version}, f)


class UpdateChecker(QThread):
    update_found = Signal(str, str, str)
    no_update = Signal(str)
    error_found = Signal(str)

    def run(self):
        try:
            release = get_latest_release()
            local_version = get_local_version()
            if is_newer(release.version, local_version):
                payload = resolve_release_payload(release)
                self.update_found.emit(
                    release.version, release.archive_url, payload.sha256
                )
            else:
                self.no_update.emit(
                    f"Producto: {PRODUCT_ID} | Versión Local: {local_version} | "
                    f"Versión SIGEH: {release.version} | Sin actualización pendiente."
                )
        except Exception as e:
            self.error_found.emit(f"Error general de red: {str(e)}")


class DownloadWorker(QThread):
    finished_download = Signal(str)
    progress_update = Signal(int, int)
    error_download = Signal(str)

    def __init__(self, url, expected_sha256="", version="pending"):
        super().__init__()
        self.url = url
        self.expected_sha256 = expected_sha256
        self.version = str(version or "pending")

    def run(self):
        new_zip_path = ""
        try:
            response = requests.get(self.url, stream=True, timeout=60)
            response.raise_for_status()
            total_size = int(response.headers.get('content-length', 0))

            local_root = Path(
                os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
            ) / PRODUCT_ID / "updates" / "staging" / self.version
            local_root.mkdir(parents=True, exist_ok=True)
            update_dir = tempfile.mkdtemp(prefix="download-", dir=str(local_root))
            new_zip_path = os.path.join(update_dir, "SIGEH-update.zip")

            downloaded_size = 0
            digest = hashlib.sha256()
            with open(new_zip_path, "wb") as f:
                for chunk in response.iter_content(32768):
                    if chunk:
                        f.write(chunk)
                        digest.update(chunk)
                        downloaded_size += len(chunk)
                        self.progress_update.emit(downloaded_size, total_size)

            if total_size > 0 and downloaded_size != total_size:
                raise IOError(
                    f"Descarga incompleta: {downloaded_size} de {total_size} bytes."
                )

            with open(new_zip_path, "rb") as executable_file:
                if executable_file.read(4) != b"PK\x03\x04":
                    raise IOError("El archivo descargado no es un ejecutable válido de Windows.")

            if self.expected_sha256 and digest.hexdigest().lower() != self.expected_sha256:
                raise IOError("La firma SHA-256 del paquete no coincide.")
            if not zipfile.is_zipfile(new_zip_path):
                raise IOError("El archivo descargado no es un ZIP valido.")
            self.finished_download.emit(new_zip_path)
        except Exception as e:
            if new_zip_path and os.path.exists(new_zip_path):
                try:
                    shutil.rmtree(os.path.dirname(new_zip_path), ignore_errors=True)
                except Exception:
                    pass
            self.error_download.emit(str(e))
            self.finished_download.emit("")


class LauncherDialog(QDialog):
    def __init__(self):
        super().__init__()
        self._is_dark = bool(
            QSettings("SIGEH", "Visual").value("dark_mode", False, type=bool)
        )
        tokens = visual_theme_tokens(self._is_dark)
        self.setWindowTitle("Iniciando Sistema...")
        self.setFixedSize(450, 150)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setStyleSheet(
            f"QDialog{{background-color:{tokens['window_bg']};color:{tokens['text_primary']};"
            f"border:2px solid {tokens['accent']};border-radius:8px;}}"
            f"QLabel{{color:{tokens['text_primary']};background:transparent;border:none;}}"
            f"QProgressBar{{background:{tokens['input_bg']};color:{tokens['text_primary']};"
            f"border:1px solid {tokens['border']};border-radius:4px;text-align:center;font-weight:bold;}}"
            f"QProgressBar::chunk{{background:{tokens['success']};border-radius:4px;}}"
            f"QMessageBox{{background:{tokens['window_bg']};color:{tokens['text_primary']};}}"
            f"QMessageBox QLabel{{color:{tokens['text_primary']};}}"
            f"QMessageBox QPushButton{{background:{tokens['button_primary_bg']};"
            f"color:{tokens['button_primary_text']};border:1px solid {tokens['border']};"
            "border-radius:6px;padding:6px 12px;min-width:80px;}}"
        )

        lay = QVBoxLayout(self)
        self.lbl_status = QLabel("Comprobando actualización...")
        self.lbl_status.setAlignment(Qt.AlignCenter)
        self.lbl_status.setStyleSheet(
            f"font-size:12pt;font-weight:bold;color:{tokens['accent']};border:none;"
        )
        lay.addWidget(self.lbl_status)

        self.progress = QProgressBar()
        self.progress.hide()
        lay.addWidget(self.progress)

        self.new_version_string = ""

        self.checker = UpdateChecker()
        self.checker.update_found.connect(self.start_download)
        self.checker.no_update.connect(self.handle_no_update)
        self.checker.error_found.connect(self.handle_update_check_error)
        self.checker.start()

    def handle_no_update(self, msg):
        # Ya NO muestra QMessageBox ni requiere presionar OK.
        write_launcher_log(msg)
        self.lbl_status.setText("Sin actualización pendiente. Iniciando sistema...")
        QApplication.processEvents()
        self.launch_main_app()

    def handle_update_check_error(self, err):
        # Si falla la verificación, abre la app local sin bloquear al usuario.
        write_launcher_log(f"Error verificando actualización: {err}")
        self.lbl_status.setText("No se pudo verificar actualización. Iniciando sistema...")
        QApplication.processEvents()
        self.launch_main_app()

    def start_download(self, version, url, expected_sha256):
        self.new_version_string = version
        self.lbl_status.setText(f"Descargando actualización {version}...")
        self.progress.show()

        self.downloader = DownloadWorker(url, expected_sha256, version)
        self.downloader.progress_update.connect(self.update_progress)
        self.downloader.finished_download.connect(self.apply_update)
        self.downloader.error_download.connect(self.handle_download_error)
        self.downloader.start()

    def handle_download_error(self, err):
        # No bloquea con OK. Deja registro y continúa con la versión local.
        write_launcher_log(f"Error de descarga: {err}")
        self.lbl_status.setText("No se pudo descargar actualización. Iniciando versión local...")
        QApplication.processEvents()

    def update_progress(self, downloaded, total):
        if total > 0:
            self.progress.setMaximum(total)
            self.progress.setValue(downloaded)
            mb_down = downloaded / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            self.progress.setFormat(f"{mb_down:.1f} MB / {mb_total:.1f} MB")
        else:
            self.progress.setMaximum(0)
            self.progress.setFormat(f"{(downloaded / (1024 * 1024)):.1f} MB")

    def _apply_legacy_update(self, new_exe_path):
        if not new_exe_path or not os.path.exists(new_exe_path):
            self.launch_main_app()
            return

        self.lbl_status.setText("Instalando actualización...")
        QApplication.processEvents()
        time.sleep(1.0)

        current_dir = get_real_dir()
        main_app_path = os.path.join(current_dir, MAIN_APP_NAME)
        old_app_path = main_app_path + f".{int(time.time())}.old"
        backup_created = False

        try:
            if os.path.exists(main_app_path):
                os.replace(main_app_path, old_app_path)
                backup_created = True
            os.replace(new_exe_path, main_app_path)
            try:
                save_local_version(self.new_version_string)
            except Exception as config_error:
                write_launcher_log(
                    f"La actualización se instaló, pero no se guardó su versión: {config_error}"
                )
            write_launcher_log(f"Actualización instalada: {self.new_version_string}")
            self.lbl_status.setText(f"Actualización {self.new_version_string} instalada. Iniciando...")
            QApplication.processEvents()
        except Exception as e:
            # Si falla el segundo reemplazo, restaura la versión funcional.
            if backup_created and not os.path.exists(main_app_path) and os.path.exists(old_app_path):
                try:
                    os.replace(old_app_path, main_app_path)
                    write_launcher_log("Se restauró la versión anterior tras fallar la actualización.")
                except Exception as restore_error:
                    write_launcher_log(f"No se pudo restaurar la versión anterior: {restore_error}")
            write_launcher_log(f"No se pudo reemplazar el ejecutable: {e}")
            self.lbl_status.setText("No se pudo instalar actualización. Iniciando versión local...")
            QApplication.processEvents()

        self.launch_main_app()

    def apply_update(self, new_zip_path):
        if not new_zip_path or not os.path.exists(new_zip_path):
            self.launch_main_app()
            return

        self.lbl_status.setText("Preparando actualizacion segura...")
        QApplication.processEvents()
        current_dir = os.path.abspath(get_real_dir())
        update_root = os.path.dirname(new_zip_path)
        extract_dir = os.path.join(update_root, "extraido")

        try:
            with zipfile.ZipFile(new_zip_path) as archive:
                extract_abs = os.path.abspath(extract_dir)
                for member in archive.infolist():
                    target = os.path.abspath(os.path.join(extract_abs, member.filename))
                    if os.path.commonpath([extract_abs, target]) != extract_abs:
                        raise IOError("El ZIP contiene una ruta no permitida.")
                archive.extractall(extract_abs)

            candidates = [os.path.join(extract_dir, "SIGEH"), extract_dir]
            payload_dir = next(
                (
                    path
                    for path in candidates
                    if os.path.isfile(os.path.join(path, MAIN_APP_NAME))
                    and os.path.isfile(os.path.join(path, LAUNCHER_NAME))
                    and os.path.isfile(os.path.join(path, UPDATER_NAME))
                    and os.path.isdir(os.path.join(path, "_internal"))
                ),
                "",
            )
            if not payload_dir:
                raise IOError("El ZIP no contiene una distribución SIGEH onedir válida.")

            installed_updater = os.path.join(current_dir, UPDATER_NAME)
            if not os.path.isfile(installed_updater):
                raise FileNotFoundError(f"No se encontro {UPDATER_NAME}.")
            updater_temp_dir = os.path.join(
                tempfile.gettempdir(), "SIGEH-Updater", str(uuid.uuid4())
            )
            os.makedirs(updater_temp_dir, exist_ok=True)
            temporary_updater = os.path.join(updater_temp_dir, UPDATER_NAME)
            shutil.copy2(installed_updater, temporary_updater)

            manifest_path = os.path.join(update_root, "update_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as manifest_file:
                json.dump(
                    {
                        "install_dir": current_dir,
                        "payload_dir": os.path.abspath(payload_dir),
                        "version": self.new_version_string,
                        "product": PRODUCT_ID,
                    },
                    manifest_file,
                )

            creationflags = 0
            if sys.platform.startswith("win"):
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(
                [temporary_updater, "--manifest", manifest_path, "--wait-pid", str(os.getpid())],
                cwd=updater_temp_dir,
                close_fds=True,
                creationflags=creationflags,
            )
            write_launcher_log(f"Actualizacion {self.new_version_string} entregada al instalador externo.")
            self.lbl_status.setText("Cerrando para completar la actualizacion...")
            QApplication.processEvents()
            QApplication.instance().quit()
        except Exception as exc:
            write_launcher_log(f"No se pudo preparar la actualizacion onedir: {exc}")
            self.lbl_status.setText("No se pudo instalar. Iniciando version local...")
            QApplication.processEvents()
            self.launch_main_app()

    def launch_main_app(self):
        result = launch_main_app_immediately()
        if result != 0:
            QMessageBox.critical(
                self,
                "No fue posible iniciar SIGEH",
                "La instalación no pudo preparar la aplicación principal. "
                "Revise lanzador_log.txt para conocer el detalle.",
            )
        QApplication.instance().quit()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-test"]:
        return 0
    return run_update_check_ui()


if __name__ == "__main__":
    raise SystemExit(main())
