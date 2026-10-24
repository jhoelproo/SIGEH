"""Measure real template rendering and Qt printing to PDF, never a physical queue."""

import argparse
import json
import os
import statistics
import sys
from pathlib import Path
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSize
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPrintSupport import QPrinter
from PySide6.QtWidgets import QApplication

from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import crear_pdf_temporal
from pdf_printing import print_pdf_native


def measure(output: Path, runs: int) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    application = QApplication.instance() or QApplication([])
    data = {
        "Fecha": "22/10/2026",
        "Hora": "08:00 AM",
        "Nombre": "PRUEBA PAPEL CARTA",
        "Sexo": "Femenino",
        "Edad_num": 25,
        "Unidad": "Años",
        "Aseguradora (ARS)": "SIN SEGURO",
        "NSS": "",
        "Cédula": "",
        "Teléfono": "",
        "Dirección": "DIRECCION DE PRUEBA",
        "Nacionalidad": "DOMINICANA",
    }
    results = {}
    for sheet in ("GENERAL", "PEDIATRIA", "GINECOLOGIA"):
        rendering, printing = [], []
        for iteration in range(runs):
            started = perf_counter()
            generated = crear_pdf_temporal(sheet, data, mostrar_error=False)
            if not generated:
                raise RuntimeError(f"No se pudo generar {sheet}")
            rendering.append((perf_counter() - started) * 1000)
            generated = Path(generated)
            source = output / f"{sheet}.pdf"
            source.write_bytes(generated.read_bytes())
            generated.unlink()
            started = perf_counter()
            printer = QPrinter()
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            target = output / f"{sheet}_print.pdf"
            printer.setOutputFileName(str(target))
            print_pdf_native(str(source), printer=printer)
            printing.append((perf_counter() - started) * 1000)
        for path in (source, target):
            document = QPdfDocument()
            document.load(str(path))
            for index in range(document.pageCount()):
                size = document.pagePointSize(index)
                image = document.render(
                    index, QSize(round(size.width()), round(size.height()))
                )
                if not image.save(str(path.with_suffix(f".{index + 1}.png"))):
                    raise RuntimeError("No se pudo guardar la imagen de QA.")
            document.close()
        results[sheet] = {
            "runs": runs,
            "render_mean_ms": round(statistics.mean(rendering), 1),
            "render_max_ms": round(max(rendering), 1),
            "print_to_pdf_mean_ms": round(statistics.mean(printing), 1),
            "print_to_pdf_max_ms": round(max(printing), 1),
            "physical_printer_tested": False,
        }
    assert application is not None
    (output / "benchmark.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8"
    )
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    arguments = parser.parse_args()
    if arguments.runs < 1:
        parser.error("--runs must be positive")
    print(json.dumps(measure(arguments.output, arguments.runs), indent=2))
