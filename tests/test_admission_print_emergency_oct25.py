"""Three-page forms remain one duplex job, with durable failure information."""

import importlib
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QRectF, QSizeF
from PySide6.QtPrintSupport import QPrinter
from PyPDF2 import PdfReader
from PyPDF2 import PdfWriter
from tests.pdf_print_device import PdfPrintDevice

import pdf_printing as printing
from admission_pyside6.legacy_backend import LegacyAdmissionBackend


def test_windows_restores_external_dispatch_without_starting_native_job(
    tmp_path, monkeypatch
):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    source = tmp_path / "three-pages.pdf"
    source.write_bytes(b"%PDF-synthetic")
    external, native = Mock(), Mock()
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module, "_imprimir_pdf_sumatra", external)
    monkeypatch.setattr(printing, "print_pdf_native", native)
    assert module.imprimir_pdf(str(source), copias=1, duplex=True)
    external.assert_called_once_with(str(source), 1, duplex=True)
    native.assert_not_called()


def test_three_pages_do_not_reconfigure_letter_after_the_job_starts(monkeypatch):
    document, printer, painter = Mock(), Mock(), Mock()
    document.pageCount.return_value = 3
    document.pagePointSize.return_value = QSizeF(612, 792)
    printer.supportsMultipleCopies.return_value = True
    printer.printerState.return_value = QPrinter.PrinterState.Idle
    active = [False]

    def letter(_size):
        if active[0]:
            raise RuntimeError("Driver rejects paper changes in active job")
        return True

    def begin(_printer):
        active[0] = True
        return True

    printer.setPageSize.side_effect = letter
    painter.begin.side_effect = begin
    painter.end.return_value = True
    painter.isActive.return_value = False
    monkeypatch.setattr(printing, "QPainter", lambda: painter)
    render = Mock(side_effect=lambda _doc, index, _printer: (QRectF(0, 0, 1, 1), index))
    monkeypatch.setattr(printing, "render_print_page", render)
    printing.paint_document(document, printer, 1)
    assert [call.args[1] for call in painter.drawImage.call_args_list] == [0, 1, 2]
    assert printer.newPage.call_count == 2
    printer.setPageSize.assert_called_once()


def test_admission_backend_requests_duplex_and_preserves_driver_error():
    dispatch = Mock(side_effect=RuntimeError("SYNTHETIC DRIVER FAILURE"))
    backend = LegacyAdmissionBackend(
        Mock(),
        shift_provider=lambda: {},
        username_provider=lambda: "qa",
        module=SimpleNamespace(imprimir_pdf=dispatch),
    )
    with pytest.raises(RuntimeError, match="DRIVER FAILURE"):
        backend.print_document("three-pages.pdf", copies=1)
    dispatch.assert_called_once_with(
        "three-pages.pdf",
        copias=1,
        mostrar_error=False,
        duplex=True,
        propagar_error=True,
    )


def test_successful_excel_does_not_clear_an_unresolved_print_error(
    tmp_path, monkeypatch
):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    path = tmp_path / "output-state.sqlite"
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript("""
        CREATE TABLE trabajos_salida(atencion_id INTEGER PRIMARY KEY,
          excel_estado TEXT,pdf_estado TEXT,impresion_estado TEXT,ultimo_error TEXT,
          updated_at TEXT,intentos INTEGER DEFAULT 0,pdf_path TEXT,pdf_sha256 TEXT);
        INSERT INTO trabajos_salida VALUES(905,'PENDIENTE','COMPLETADO','FALLIDO',
          'SYNTHETIC DRIVER FAILURE','',1,NULL,NULL);
        """)
    manager = module.DatabaseManager.__new__(module.DatabaseManager)
    monkeypatch.setattr(manager, "_connect", lambda **_kwargs: sqlite3.connect(path))
    manager.actualizar_trabajo_salida(905, "excel", "COMPLETADO")
    with closing(sqlite3.connect(path)) as con:
        row = con.execute(
            "SELECT impresion_estado,ultimo_error FROM trabajos_salida WHERE atencion_id=905"
        ).fetchone()
        assert row == ("FALLIDO", "SYNTHETIC DRIVER FAILURE")
        assert con.execute("SELECT COUNT(*) FROM trabajos_salida").fetchone()[0] == 1
    manager.actualizar_trabajo_salida(905, "impresion", "ENVIADO_A_IMPRESORA")
    with closing(sqlite3.connect(path)) as con:
        assert (
            con.execute(
                "SELECT ultimo_error FROM trabajos_salida WHERE atencion_id=905"
            ).fetchone()[0]
            is None
        )


@pytest.mark.parametrize(
    "duplex,setting", [(True, "duplexlong"), (False, "simplex"), (None, None)]
)
def test_one_external_job_sends_all_pages_and_requested_duplex(
    tmp_path, monkeypatch, duplex, setting
):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    engine = tmp_path / "SumatraPDF.exe"
    engine.touch()
    monkeypatch.setattr(module, "SUMATRA_PATH_CACHE", str(engine))
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(module.subprocess, "run", run)
    module._imprimir_pdf_sumatra("three-pages.pdf", 2, duplex=duplex)
    run.assert_called_once()
    arguments = run.call_args.args[0]
    assert arguments[-1] == "three-pages.pdf"
    options = arguments[arguments.index("-print-settings") + 1].split(",")
    assert set(options) >= {"paper=letter", "fit", "2x"}
    assert (
        (setting in options)
        if setting is not None
        else not {"duplexlong", "simplex"}.intersection(options)
    )
    assert not any(value.startswith("pages=") for value in options)


def test_missing_external_engine_is_reported_without_starting_native_qt(
    tmp_path, monkeypatch
):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    path = tmp_path / "synthetic.pdf"
    path.touch()
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    external = Mock(side_effect=printing.ExternalPrintingUnavailable("engine absent"))
    native = Mock()
    monkeypatch.setattr(module, "_imprimir_pdf_sumatra", external)
    monkeypatch.setattr(printing, "print_pdf_native", native)
    assert not module.imprimir_pdf(str(path), duplex=True)
    native.assert_not_called()
    with pytest.raises(printing.ExternalPrintingUnavailable, match="engine absent"):
        module.imprimir_pdf(str(path), duplex=True, propagar_error=True)
    assert external.call_count == 2


def test_emergenciology_pdf_contains_the_three_original_pages(tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))
    result = module.crear_pdf_temporal(
        "GENERAL",
        {
            "Nombre": "PACIENTE QA IMPRESION",
            "Edad_num": 25,
            "Unidad": "Años",
            "Fecha": "25/10/2026",
            "Hora": "08:00 AM",
            "Sexo": "Femenino",
            "Dirección": "DIRECCION QA",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "SIN SEGURO",
            "NSS": "",
            "Cédula": "",
            "Teléfono": "",
        },
        mostrar_error=False,
    )
    assert result
    pages = PdfReader(result).pages
    assert len(pages) == 3
    assert all(
        (float(page.mediabox.width), float(page.mediabox.height)) == (612, 792)
        for page in pages
    )


def test_two_three_page_duplex_copies_keep_their_front_sides_separate(tmp_path):
    source, target = tmp_path / "source.pdf", tmp_path / "printed.pdf"
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=612, height=792)
    with source.open("wb") as stream:
        writer.write(stream)
    printer = PdfPrintDevice(target)
    printing.print_pdf_native(str(source), printer=printer, copies=2, duplex=True)
    assert printer.duplex() == QPrinter.DuplexMode.DuplexLongSide
    assert len(PdfReader(target).pages) == 7
    assert not printer.aborted


def test_failed_duplex_copy_separator_is_not_silently_skipped():
    document, printer = Mock(), Mock()
    document.pageCount.return_value = 3
    printer.duplex.return_value = QPrinter.DuplexMode.DuplexLongSide
    printer.newPage.return_value = False
    with pytest.raises(RuntimeError, match="separar"):
        printing._separate_duplex_copy(document, printer, 1)


def test_driver_refusal_of_mixed_orientation_is_reported_before_next_page():
    printer = Mock()
    printer.setPageOrientation.return_value = False
    with pytest.raises(RuntimeError, match="orientación"):
        printing._advance_page(
            printer, QSizeF(792, 612), printing.page_orientation(QSizeF(612, 792))
        )
    printer.newPage.assert_not_called()


def test_explicit_simplex_reaches_the_native_adapter(tmp_path):
    source, target = tmp_path / "source.pdf", tmp_path / "printed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    with source.open("wb") as stream:
        writer.write(stream)
    printer = PdfPrintDevice(target)
    printing.print_pdf_native(str(source), printer=printer, duplex=False)
    assert printer.duplex() == QPrinter.DuplexMode.DuplexNone
    assert len(PdfReader(target).pages) == 1


def test_external_timeout_is_reported_without_another_backend(tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    engine, source = tmp_path / "SumatraPDF.exe", tmp_path / "source.pdf"
    engine.touch()
    source.touch()
    monkeypatch.setattr(module, "SUMATRA_PATH_CACHE", str(engine))
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        module.subprocess,
        "run",
        Mock(side_effect=module.subprocess.TimeoutExpired("synthetic", 30)),
    )
    native = Mock()
    monkeypatch.setattr(printing, "print_pdf_native", native)
    with pytest.raises(RuntimeError, match="Revise la cola"):
        module.imprimir_pdf(str(source), duplex=True, propagar_error=True)
    native.assert_not_called()


@pytest.mark.parametrize("stage,state", [("unknown", "COMPLETADO"), ("pdf", "UNKNOWN")])
def test_output_state_rejects_unknown_columns_and_states(stage, state):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    manager = module.DatabaseManager.__new__(module.DatabaseManager)
    manager._connect = Mock()
    with pytest.raises(ValueError):
        manager.actualizar_trabajo_salida(905, stage, state)
    manager._connect.assert_not_called()


def test_output_state_records_failure_metadata_and_attempts(tmp_path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    path = tmp_path / "output.sqlite"
    with closing(sqlite3.connect(path)) as con, con:
        con.executescript("""
        CREATE TABLE trabajos_salida(atencion_id INTEGER PRIMARY KEY,
          excel_estado TEXT,pdf_estado TEXT,impresion_estado TEXT,ultimo_error TEXT,
          updated_at TEXT,intentos INTEGER DEFAULT 0,pdf_path TEXT,pdf_sha256 TEXT);
        INSERT INTO trabajos_salida VALUES(905,'COMPLETADO','COMPLETADO','PENDIENTE',NULL,'',0,NULL,NULL);
        """)
    manager = module.DatabaseManager.__new__(module.DatabaseManager)
    monkeypatch.setattr(manager, "_connect", lambda **_kwargs: sqlite3.connect(path))
    manager.actualizar_trabajo_salida(905, "impresion", "PROCESANDO")
    manager.actualizar_trabajo_salida(
        905,
        "impresion",
        "FALLIDO",
        error="SYNTHETIC",
        pdf_path="synthetic.pdf",
        pdf_sha256="abc",
        incrementar_intento=True,
    )
    with closing(sqlite3.connect(path)) as con:
        assert con.execute(
            "SELECT ultimo_error,pdf_path,pdf_sha256,intentos FROM trabajos_salida"
        ).fetchone() == ("SYNTHETIC", "synthetic.pdf", "abc", 1)


@pytest.mark.parametrize("system", ["Linux", "Darwin"])
@pytest.mark.parametrize(
    "duplex,sides", [(True, "sides=two-sided-long-edge"), (False, "sides=one-sided")]
)
def test_posix_jobs_honor_the_requested_sides(monkeypatch, system, duplex, sides):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(module.subprocess, "run", run)
    module._imprimir_pdf_posix("synthetic.pdf", 1, system, duplex)
    assert sides in run.call_args.args[0]


@pytest.mark.parametrize("path", [None, "synthetic.pdf"])
def test_history_reprint_uses_existing_attention_and_duplex(monkeypatch, path):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    window = module.App.__new__(module.App)
    create = Mock(return_value=path)
    window._crear_hoja_temporal_atencion = create
    dispatch, cleanup = Mock(return_value=True), Mock()
    monkeypatch.setattr(module, "imprimir_pdf", dispatch)
    monkeypatch.setattr(module, "programar_limpieza_pdf_temporal", cleanup)
    assert window._imprimir_hoja_temporal_atencion(905) is bool(path)
    create.assert_called_once_with(905, mostrar_error=False)
    if path:
        dispatch.assert_called_once_with(
            path, copias=1, mostrar_error=False, duplex=True
        )
        cleanup.assert_called_once_with(path, espera_segundos=90)
    else:
        dispatch.assert_not_called()
