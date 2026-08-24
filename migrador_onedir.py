"""Puente de una sola ejecucion para migrar instalaciones onefile a onedir."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import requests
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout


MANIFEST_URL = "https://gist.githubusercontent.com/jhoelproo/d05cfbcf776796f0aafe0bbbf8947eba/raw/"
UPDATER_NAME = "APLICAR_ACTUALIZACION.exe"


class MigrationWorker(QThread):
    progress = Signal(int, int)
    status = Signal(str)
    completed = Signal()
    failed = Signal(str)

    def run(self):
        try:
            self.status.emit("Consultando la nueva version...")
            manifest_response = requests.get(f"{MANIFEST_URL}?t={int(time.time())}", timeout=20)
            manifest_response.raise_for_status()
            remote = manifest_response.json()
            version = str(remote.get("version") or "").strip()
            url = str(remote.get("onedir_url") or "").strip()
            expected_sha256 = str(remote.get("onedir_sha256") or "").strip().lower()
            if not version or not url:
                raise RuntimeError("El manifiesto no contiene el paquete onedir.")

            root = tempfile.mkdtemp(prefix="hospital_migration_")
            zip_path = os.path.join(root, "HOSPITAL-update.zip")
            self.status.emit(f"Descargando Hospital {version}...")
            response = requests.get(url, stream=True, timeout=60)
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            digest = hashlib.sha256()
            with open(zip_path, "wb") as stream:
                for chunk in response.iter_content(32768):
                    if chunk:
                        stream.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        self.progress.emit(downloaded, total)

            if total and downloaded != total:
                raise IOError(f"Descarga incompleta: {downloaded} de {total} bytes.")
            if expected_sha256 and digest.hexdigest().lower() != expected_sha256:
                raise IOError("La firma SHA-256 del paquete no coincide.")
            if not zipfile.is_zipfile(zip_path):
                raise IOError("El paquete descargado no es un ZIP valido.")

            self.status.emit("Preparando la nueva instalacion...")
            extract_dir = os.path.join(root, "extraido")
            with zipfile.ZipFile(zip_path) as archive:
                extract_abs = os.path.abspath(extract_dir)
                for member in archive.infolist():
                    target = os.path.abspath(os.path.join(extract_abs, member.filename))
                    if os.path.commonpath([extract_abs, target]) != extract_abs:
                        raise IOError("El ZIP contiene una ruta no permitida.")
                archive.extractall(extract_abs)

            candidates = (os.path.join(extract_dir, "HOSPITAL"), extract_dir)
            payload_dir = next(
                (
                    path
                    for path in candidates
                    if os.path.isfile(os.path.join(path, "CALCULOS_QT.exe"))
                    and os.path.isfile(os.path.join(path, "INICIAR_SISTEMA.exe"))
                    and os.path.isfile(os.path.join(path, UPDATER_NAME))
                    and os.path.isdir(os.path.join(path, "_internal"))
                ),
                "",
            )
            if not payload_dir:
                raise IOError("El ZIP no contiene una distribucion onedir valida.")

            updater_path = os.path.join(root, UPDATER_NAME)
            shutil.copy2(os.path.join(payload_dir, UPDATER_NAME), updater_path)
            manifest_path = os.path.join(root, "update_manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as manifest_file:
                json.dump(
                    {
                        "install_dir": os.path.dirname(sys.executable),
                        "payload_dir": os.path.abspath(payload_dir),
                        "version": version,
                    },
                    manifest_file,
                )

            flags = 0
            if sys.platform.startswith("win"):
                flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
            subprocess.Popen(
                [updater_path, "--manifest", manifest_path, "--wait-pid", str(os.getpid())],
                cwd=root,
                close_fds=True,
                creationflags=flags,
            )
            self.completed.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class MigrationDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Actualizacion de Hospital")
        self.setFixedSize(470, 155)
        self.setWindowFlags(Qt.WindowTitleHint | Qt.CustomizeWindowHint)
        layout = QVBoxLayout(self)
        self.label = QLabel("Preparando la migracion a la nueva version...")
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        self.progress = QProgressBar()
        layout.addWidget(self.progress)

        self.worker = MigrationWorker()
        self.worker.status.connect(self.label.setText)
        self.worker.progress.connect(self.update_progress)
        self.worker.completed.connect(self.finish_migration)
        self.worker.failed.connect(self.show_error)
        self.worker.start()

    def update_progress(self, downloaded, total):
        if total:
            self.progress.setMaximum(total)
            self.progress.setValue(downloaded)
            self.progress.setFormat(f"{downloaded / 1048576:.1f} / {total / 1048576:.1f} MB")
        else:
            self.progress.setMaximum(0)

    def finish_migration(self):
        self.label.setText("Descarga completa. Instalando y reiniciando...")
        QApplication.processEvents()
        QApplication.instance().quit()

    def show_error(self, message):
        QMessageBox.critical(self, "No se pudo actualizar", message)
        QApplication.instance().quit()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    dialog = MigrationDialog()
    dialog.show()
    raise SystemExit(app.exec())

