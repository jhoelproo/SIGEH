import sys
from pathlib import Path
from types import SimpleNamespace

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


def render(path, width, height, profile, dark=False, text_percent=100):
    app = QApplication.instance() or QApplication([])
    context = AppContext(
        connection_factory=lambda: object(),
        user={"id": 1, "username": "USUARIO DE PRUEBA", "role": "auxiliar"},
        session_id="preview-session",
        device_id="preview-device",
        current_shift={
            "id": 1,
            "label": "8:00 AM - 8:00 PM",
            "representative": "REPRESENTANTE DE PRUEBA",
        },
    )
    controller = AdmissionController(
        AdmissionService(context, AdmissionRepository(PreviewBackend()))
    )
    window = AdmissionStandaloneWindow(context, controller)
    window.resize(width, height)
    widget = window.centralWidget()
    window.show()
    app.processEvents()
    widget.apply_layout_profile(
        SimpleNamespace(
            applied_profile=profile,
            density="MUY_COMPACTA" if profile == "MUY_COMPACTO" else "NORMAL",
            text_percent=text_percent,
        )
    )
    widget.apply_theme(dark)
    app.processEvents()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not window.grab().save(str(target)):
        raise RuntimeError(f"No se pudo guardar {target}")
    window.close()


if __name__ == "__main__":
    render(
        sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4],
        bool(int(sys.argv[5])) if len(sys.argv) > 5 else False,
        int(sys.argv[6]) if len(sys.argv) > 6 else 100,
    )
