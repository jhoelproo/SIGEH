"""Letter paper and immediate print dispatch, using synthetic patients only."""

import importlib
from pathlib import Path
from unittest.mock import Mock

import pytest
from PyPDF2 import PdfReader
from PyPDF2.generic import IndirectObject
import pdf_printing as printing


@pytest.mark.parametrize("sheet", ["GENERAL", "PEDIATRIA", "GINECOLOGIA"])
def test_generated_admission_uses_letter_on_every_page(sheet, tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    result = module.crear_pdf_temporal(
        sheet,
        {
            "Nombre": "PRUEBA PAPEL CARTA",
            "Edad_num": 25,
            "Unidad": "Años",
            "Fecha": "22/10/2026",
            "Hora": "08:00 AM",
            "Sexo": "Femenino",
            "Dirección": "DIRECCION DE PRUEBA",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "SIN SEGURO",
            "NSS": "",
            "Cédula": "",
            "Teléfono": "",
        },
        mostrar_error=False,
    )
    assert result
    for page in PdfReader(result).pages:
        assert float(page.mediabox.width) == pytest.approx(612)
        assert float(page.mediabox.height) == pytest.approx(792)
        assert "PRUEBA PAPEL CARTA" in page.extract_text() or sheet == "GENERAL"
        if sheet == "GINECOLOGIA":
            assert isinstance(page.raw_get("/Contents"), IndirectObject)


def test_gynaecology_is_readable_by_native_engine(tmp_path, monkeypatch):
    from PySide6.QtPdf import QPdfDocument
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    result = module.crear_pdf_temporal(
        "GINECOLOGIA",
        {
            "Fecha": "22/10/2026",
            "Hora": "08:00 AM",
            "Nombre": "PRUEBA PAPEL CARTA",
            "Dirección": "DIRECCION DE PRUEBA",
            "Nacionalidad": "DOMINICANA",
        },
        mostrar_error=False,
    )
    document = QPdfDocument()
    assert document.load(result) == QPdfDocument.Error.None_
    assert "PRUEBA PAPEL CARTA" in document.getAllText(0).text()
    document.close()
    assert application is not None


def test_windows_dispatch_uses_in_process_printing(tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    source = tmp_path / "synthetic.pdf"
    source.write_bytes(b"%PDF-test")
    native = Mock()
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(printing, "print_pdf_native", native)
    popen = Mock(side_effect=AssertionError("External process must not be started"))
    monkeypatch.setattr(module.subprocess, "Popen", popen)
    assert module.imprimir_pdf(str(source), copias=2)
    native.assert_called_once()
    assert Path(native.call_args.args[0]) == source
    assert native.call_args.kwargs["copies"] == 2
    popen.assert_not_called()


@pytest.mark.parametrize(
    "failure", [RuntimeError("driver failure"), ValueError("invalid PDF")]
)
def test_native_failure_does_not_print_again_through_sumatra(
    tmp_path, monkeypatch, failure
):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    source = tmp_path / "synthetic.pdf"
    source.touch()
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(printing, "print_pdf_native", Mock(side_effect=failure))
    fallback = Mock()
    monkeypatch.setattr(module, "_imprimir_pdf_sumatra", fallback)
    assert not module.imprimir_pdf(str(source))
    fallback.assert_not_called()


def test_fallback_has_one_letter_job_for_all_copies(tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    source, executable = tmp_path / "synthetic.pdf", tmp_path / "SumatraPDF.exe"
    source.touch()
    executable.touch()
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        printing,
        "print_pdf_native",
        Mock(side_effect=printing.NativePrintingUnavailable()),
    )
    monkeypatch.setattr(module, "SUMATRA_PATH_CACHE", str(executable))
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.imprimir_pdf(str(source), copias=3)
    run.assert_called_once()
    assert run.call_args.args[0] == [
        str(executable),
        "-print-to-default",
        "-print-settings",
        "paper=letter,fit,3x",
        "-silent",
        str(source),
    ]
    assert run.call_args.kwargs["timeout"] == 30


@pytest.mark.parametrize("code", [0, 1])
def test_fallback_resolves_bundled_engine_and_checks_exit(tmp_path, monkeypatch, code):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    (tmp_path / "SumatraPDF.exe").touch()
    monkeypatch.setattr(module, "SUMATRA_PATH_CACHE", None)
    monkeypatch.setattr(module, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr(
        module.subprocess, "run", Mock(return_value=Mock(returncode=code))
    )
    if code:
        with pytest.raises(RuntimeError, match="rechazó"):
            module._imprimir_pdf_sumatra("synthetic.pdf", 1)
    else:
        module._imprimir_pdf_sumatra("synthetic.pdf", 1)
        assert module.SUMATRA_PATH_CACHE == str(tmp_path / "SumatraPDF.exe")


def test_missing_fallback_engine_is_not_success(tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    monkeypatch.setattr(module, "SUMATRA_PATH_CACHE", None)
    monkeypatch.setattr(module, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(module, "PROJECT_ROOT", str(tmp_path))
    with pytest.raises(RuntimeError, match="respaldo"):
        module._imprimir_pdf_sumatra("synthetic.pdf", 1)


@pytest.mark.parametrize("system", ["Darwin", "Linux", "Other"])
def test_other_platforms_request_letter_in_one_job(tmp_path, monkeypatch, system):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    source = tmp_path / "synthetic.pdf"
    source.touch()
    monkeypatch.setattr(module.platform, "system", lambda: system)
    run = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.imprimir_pdf(str(source), copias=2) == (system != "Other")
    if system != "Other":
        run.assert_called_once()
        assert "media=Letter" in run.call_args.args[0]
        run.return_value.returncode = 1
        assert not module.imprimir_pdf(str(source))


def test_missing_pdf_reports_friendly_error_without_starting_printer(monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    warning = Mock()
    monkeypatch.setattr(module.messagebox, "showwarning", warning)
    native = Mock()
    monkeypatch.setattr(printing, "print_pdf_native", native)
    assert not module.imprimir_pdf(None, mostrar_error=True)
    warning.assert_called_once()
    native.assert_not_called()
