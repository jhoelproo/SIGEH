from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from admission_pyside6 import (
    AdmissionController,
    AdmissionHistoryDialog,
    AdmissionRepository,
    AdmissionService,
    AppContext,
)


class PreviewBackend:
    def list_history(self, **filters):
        rows = [
            {
                "id": index,
                "fecha": "05-08-2026",
                "hora": f"{8 + index:02d}:15",
                "nombre": f"PACIENTE DEMOSTRACIÓN {index}",
                "hoja": "GENERAL",
                "ars": "ARS DEMOSTRACIÓN",
                "nss": f"000000{index:04d}",
                "cedula": "00000000000",
                "edad_num": 20 + index,
                "tipo_atencion": "EMERGENCIA",
            }
            for index in range(1, 8)
        ]
        return rows[int(filters.get("offset") or 0):][: int(filters.get("limit") or 100)]


def main():
    app = QApplication(sys.argv)
    context = AppContext(
        connection_factory=lambda: object(),
        user={"username": "DEMOSTRACIÓN", "role": "auxiliar"},
        session_id="preview-session",
        device_id="preview-device",
        current_shift={"id": 1},
    )
    controller = AdmissionController(
        AdmissionService(context, AdmissionRepository(PreviewBackend()))
    )
    dialog = AdmissionHistoryDialog(controller)
    dialog.show()

    output = Path("logs/migracion_admision/fase_06_historial/historial_1280x760.png")
    output.parent.mkdir(parents=True, exist_ok=True)

    def capture():
        dialog.grab().save(str(output))
        dialog.close()
        app.quit()

    QTimer.singleShot(500, capture)
    app.exec()


if __name__ == "__main__":
    main()
