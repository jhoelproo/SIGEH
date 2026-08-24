import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication

from admission_pyside6 import (
    AdmissionController,
    AdmissionRepository,
    AdmissionService,
    AdmissionStandaloneWindow,
    AppContext,
)


class PreviewBackend:
    def search_patients(self, *_args, **_kwargs):
        return []


def render(path: str, width: int, height: int):
    app = QApplication.instance() or QApplication([])
    context = AppContext(
        connection_factory=lambda: object(),
        user={"id": 1, "username": "USUARIO DE PRUEBA", "role": "auxiliar"},
        session_id="preview-session",
        device_id="preview-device",
        current_shift={"label": "8:00 AM - 8:00 PM", "representative": "REPRESENTANTE DE PRUEBA"},
    )
    controller = AdmissionController(
        AdmissionService(context, AdmissionRepository(PreviewBackend()))
    )
    window = AdmissionStandaloneWindow(context, controller)
    window.resize(width, height)
    window.show()
    app.processEvents()
    if not window.grab().save(path):
        raise RuntimeError(f"No se pudo guardar {path}")
    window.close()


if __name__ == "__main__":
    render(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]))
