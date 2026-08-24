from __future__ import annotations

import importlib
import os
import statistics
import sys
import time
from pathlib import Path
from io import BytesIO

from PyPDF2 import PdfReader, PdfWriter


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    data = {
        "Fecha": "22/08/2026",
        "Hora": "08:00 AM",
        "Nombre": "PACIENTE PRUEBA",
        "Sexo": "Femenino",
        "Edad_num": 25,
        "Unidad": "Años",
        "Aseguradora (ARS)": "SIN SEGURO",
        "NSS": "",
        "Cédula": "00100000000",
        "Teléfono": "8090000000",
        "Dirección": "DIRECCION PRUEBA",
        "Nacionalidad": "DOMINICANA",
    }
    iterations = max(2, int(os.environ.get("PDF_BENCHMARK_RUNS", "20")))
    timings: list[float] = []
    for _ in range(iterations):
        started = time.perf_counter()
        path = module.crear_pdf_temporal("GENERAL", data, mostrar_error=False)
        timings.append((time.perf_counter() - started) * 1000.0)
        if path:
            os.remove(path)
    percentile_95 = statistics.quantiles(timings, n=20, method="inclusive")[18]
    print(f"PDF_RENDER_MIN_MS={min(timings):.1f}")
    print(f"PDF_RENDER_AVG_MS={statistics.mean(timings):.1f}")
    print(f"PDF_RENDER_P95_MS={percentile_95:.1f}")
    print(f"PDF_RENDER_MAX_MS={max(timings):.1f}")
    print(f"PDF_READY_P95_MS={percentile_95:.1f}")

    template_path = module.RUTA_HOJAS["GENERAL"]
    template_bytes = Path(template_path).read_bytes()
    clone_timings: list[float] = []
    for index in range(iterations):
        target = Path(module.tempfile.gettempdir()) / f"pdf-clone-benchmark-{index}.pdf"
        started = time.perf_counter()
        reader = PdfReader(BytesIO(template_bytes))
        writer = PdfWriter()
        writer.clone_document_from_reader(reader)
        with target.open("wb") as stream:
            writer.write(stream)
        clone_timings.append((time.perf_counter() - started) * 1000.0)
        target.unlink(missing_ok=True)
    clone_p95 = statistics.quantiles(clone_timings, n=20, method="inclusive")[18]
    print(f"PDF_TEMPLATE_CLONE_P95_MS={clone_p95:.1f}")

    class Database:
        def obtener_trabajo_salida(self, _attention_id):
            return {"excel_estado": "COMPLETADO", "impresion_estado": "NO_APLICA"}

        def actualizar_trabajo_salida(self, *_args, **_kwargs):
            return None

        def limpiar_error_trabajo_salida(self, _attention_id):
            return None

        def notify_detail_sheet_generated(self, _attention_id):
            return None

    class Button:
        def configure(self, **_kwargs):
            return None

    app = module.App.__new__(module.App)
    app.db = Database()
    app.app_settings = {"auto_print": False, "print_auto_hoja": False}
    app._output_lock = module.threading.Lock()
    app._output_inflight = set()
    app._output_payloads = {}
    app.boton_generar_pdf = Button()
    app.set_status = lambda *_args: None
    app._post_to_ui = lambda callback: callback()
    app._invalidar_caches_datos = lambda: None
    app._refrescar_resumen_en_vivo = lambda: None
    app._retry_excel_export_jobs = lambda: None
    app._dialogo_salida_pendiente = lambda *_args: None
    click_to_open: list[float] = []
    started = [0.0]
    original_open = module.abrir_pdf
    original_cleanup = module.programar_limpieza_pdf_temporal
    module.abrir_pdf = lambda _path, **_kwargs: click_to_open.append((time.perf_counter() - started[0]) * 1000.0) or True
    module.programar_limpieza_pdf_temporal = lambda *_args, **_kwargs: None
    try:
        for index in range(iterations):
            started[0] = time.perf_counter()
            app._procesar_salida_atencion(
                1000 + index, "GENERAL", data, {"id": 1}, abrir_pdf_final=True,
                flow_started_at=started[0],
            )
    finally:
        module.abrir_pdf = original_open
        module.programar_limpieza_pdf_temporal = original_cleanup
    click_p95 = statistics.quantiles(click_to_open, n=20, method="inclusive")[18]
    print(f"POST_COMMIT_TO_OPEN_DISPATCH_MIN_MS={min(click_to_open):.1f}")
    print(f"POST_COMMIT_TO_OPEN_DISPATCH_AVG_MS={statistics.mean(click_to_open):.1f}")
    print(f"POST_COMMIT_TO_OPEN_DISPATCH_P95_MS={click_p95:.1f}")
    print(f"POST_COMMIT_TO_OPEN_DISPATCH_MAX_MS={max(click_to_open):.1f}")


if __name__ == "__main__":
    main()
