"""Non-blocking manual checks using the same channel as the startup launcher."""

from threading import Thread

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QPushButton

from sigeh_product import APP_VERSION
from sigeh_update import get_latest_release, is_newer, resolve_release_payload
from app_icons import APP_ICONS


class UpdateCheckWorker(QObject):
    checked = Signal(str, bool)
    failed = Signal()
    finished = Signal()

    def start(self):
        Thread(target=self.run, name="SIGEH-update-check", daemon=True).start()

    def run(self):
        try:
            release = get_latest_release(timeout=6)
            available = is_newer(release.version, APP_VERSION)
            if available:
                resolve_release_payload(release, timeout=6)
            self.checked.emit(release.version, available)
        except Exception:
            self.failed.emit()
        finally:
            self.finished.emit()


class UpdateCheckControl(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.button = QPushButton(parent)
        self.button.setObjectName("CheckUpdatesButton")
        self.button.setProperty("semanticIcon", "refresh")
        self.button.setProperty("semanticIconColor", "#FFFFFF")
        self.button.setIcon(APP_ICONS.icon("refresh", color="#FFFFFF"))
        self.button.setCursor(Qt.PointingHandCursor)
        self.button.setStyleSheet(
            "QPushButton {background:#1267B2;color:#FFFFFF;border:1px solid #398AD1;"
            "border-radius:6px;padding:0;}"
            "QPushButton:hover {background:#187ACB;}"
            "QPushButton:disabled {background:#334B62;border-color:#52677B;}"
        )
        self.button.setAccessibleName("Buscar actualizaciones")
        self.button.setToolTip("Buscar actualizaciones de SIGEH")
        self.button.setFixedSize(36, 36)
        self.button.clicked.connect(self.start)
        self.worker = None

    def start(self):
        if self.worker is not None:
            return
        self.button.setEnabled(False)
        self.button.setToolTip("Buscando actualizaciones…")
        self.worker = UpdateCheckWorker()
        self.worker.checked.connect(self._checked)
        self.worker.failed.connect(self._failed)
        self.worker.finished.connect(self._finished)
        self.worker.finished.connect(self.worker.deleteLater)
        self.worker.start()

    def _checked(self, version, available):
        if available:
            message = (
                f"SIGEH {version} está disponible.\n\n"
                "Guarda tu trabajo y cierra el sistema. Al volver a abrir SIGEH.exe, "
                "se descargará e instalará la actualización."
            )
        else:
            message = f"SIGEH {APP_VERSION} está actualizado."
        QMessageBox.information(self.parent(), "Actualizaciones de SIGEH", message)

    def _failed(self):
        QMessageBox.information(
            self.parent(),
            "Actualizaciones de SIGEH",
            "No se pudieron comprobar las actualizaciones. Puedes seguir trabajando "
            "y volver a intentarlo cuando haya conexión.",
        )

    def _finished(self):
        self.worker = None
        self.button.setEnabled(True)
        self.button.setToolTip("Buscar actualizaciones de SIGEH")
