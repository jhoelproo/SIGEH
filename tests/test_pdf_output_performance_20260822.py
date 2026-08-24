from __future__ import annotations

import importlib
import threading
import time
from pathlib import Path


class _Button:
    def __init__(self) -> None:
        self.states: list[object] = []

    def configure(self, **kwargs) -> None:
        self.states.append(kwargs.get("state"))


class _OutputDatabase:
    def __init__(self, *, print_state: str = "NO_APLICA") -> None:
        self.job = {
            "excel_estado": "PENDIENTE",
            "pdf_estado": "NO_APLICA",
            "impresion_estado": print_state,
        }
        self.events: list[tuple[str, str]] = []

    def obtener_trabajo_salida(self, _attention_id):
        return dict(self.job)

    def actualizar_trabajo_salida(self, _attention_id, stage, state, **_kwargs):
        self.events.append((stage, state))
        self.job[f"{stage}_estado"] = state

    def limpiar_error_trabajo_salida(self, _attention_id):
        self.events.append(("clear", "ok"))

    def notify_detail_sheet_generated(self, _attention_id):
        self.events.append(("notify", "ok"))


def _make_app(module, database):
    app = module.App.__new__(module.App)
    app.db = database
    app.app_settings = {
        "auto_print": False,
        "print_auto_hoja": False,
        "print_copies_hoja": 1,
    }
    app._output_lock = threading.Lock()
    app._output_inflight = set()
    app._output_payloads = {}
    app.boton_generar_pdf = _Button()
    app.statuses: list[str] = []
    app.set_status = lambda message, _kind: app.statuses.append(message)
    app._post_to_ui = lambda callback: callback()
    app._invalidar_caches_datos = lambda: None
    app._refrescar_resumen_en_vivo = lambda: None
    app._retry_excel_export_jobs = lambda: None
    app._dialogo_salida_pendiente = lambda *_args: None
    return app


def _render_stub(tmp_path: Path, events: list[str]):
    counter = {"value": 0}

    def render(_sheet, _snapshot, mostrar_error=False):
        del mostrar_error
        counter["value"] += 1
        path = tmp_path / f"attention_{counter['value']}.pdf"
        path.write_bytes(b"%PDF-1.4\noutput-test")
        events.append("render")
        return str(path)

    return render, counter


def test_immediate_open_precedes_slow_excel_and_renders_once(tmp_path: Path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    database = _OutputDatabase()
    app = _make_app(module, database)
    events: list[str] = []
    render, counter = _render_stub(tmp_path, events)
    monkeypatch.setattr(module, "crear_pdf_temporal", render)
    monkeypatch.setattr(module, "abrir_pdf", lambda path, **_kwargs: events.append(f"open:{Path(path).name}") or True)
    monkeypatch.setattr(module, "programar_limpieza_pdf_temporal", lambda path, **_kwargs: events.append(f"cleanup:{Path(path).name}"))

    def slow_excel(_db, _turn):
        events.append("excel-start")
        time.sleep(0.05)
        events.append("excel-done")

    monkeypatch.setattr(module, "reconstruir_excel_turno", slow_excel)
    app._procesar_salida_atencion(41, "GENERAL", {"Nombre": "PRUEBA"}, {"id": 1}, abrir_pdf_final=True)

    assert counter["value"] == 1
    assert events.index("render") < events.index("open:attention_1.pdf") < events.index("excel-start")
    assert events.count("cleanup:attention_1.pdf") == 1
    assert database.job["excel_estado"] == "COMPLETADO"


def test_open_and_print_share_the_single_rendered_file(tmp_path: Path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    database = _OutputDatabase(print_state="PENDIENTE")
    app = _make_app(module, database)
    events: list[str] = []
    render, counter = _render_stub(tmp_path, events)
    monkeypatch.setattr(module, "crear_pdf_temporal", render)
    monkeypatch.setattr(module, "abrir_pdf", lambda path, **_kwargs: events.append(f"open:{Path(path).name}") or True)
    monkeypatch.setattr(module, "imprimir_pdf", lambda path, **_kwargs: events.append(f"print:{Path(path).name}") or True)
    monkeypatch.setattr(module, "programar_limpieza_pdf_temporal", lambda path, **_kwargs: events.append(f"cleanup:{Path(path).name}"))
    monkeypatch.setattr(module, "reconstruir_excel_turno", lambda *_args: events.append("excel"))

    app._procesar_salida_atencion(42, "GENERAL", {"Nombre": "PRUEBA"}, {"id": 1}, abrir_pdf_final=True)

    assert counter["value"] == 1
    assert "open:attention_1.pdf" in events
    assert "print:attention_1.pdf" in events
    assert events.index("open:attention_1.pdf") < events.index("print:attention_1.pdf") < events.index("excel")
    assert events.count("cleanup:attention_1.pdf") == 1


def test_pdf_render_failure_preserves_the_saved_attention_and_reports_pdf_error(tmp_path: Path, monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    database = _OutputDatabase()
    app = _make_app(module, database)
    monkeypatch.setattr(module, "crear_pdf_temporal", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "reconstruir_excel_turno", lambda *_args: None)

    app._procesar_salida_atencion(43, "GENERAL", {"Nombre": "PRUEBA"}, {"id": 1}, abrir_pdf_final=True)

    assert database.job["pdf_estado"] == "FALLIDO"
    assert database.job["excel_estado"] == "COMPLETADO"
    assert any("fue guardada" in status for status in app.statuses)


def test_duplicate_immediate_output_is_suppressed(tmp_path: Path):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    app = _make_app(module, _OutputDatabase())
    started: list[dict] = []
    app._start_worker = lambda _target, **kwargs: started.append(kwargs) or object()

    assert app._iniciar_salida_atencion(44, "GENERAL", {"Nombre": "PRUEBA"}, {}, True)
    assert not app._iniciar_salida_atencion(44, "GENERAL", {"Nombre": "PRUEBA"}, {}, True)
    assert len(started) == 1
    app._release_output_inflight(44)


def test_windows_open_dispatch_returns_without_waiting_for_the_viewer(monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    dispatched: list[str] = []
    monkeypatch.setattr(module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(module.os, "startfile", lambda path: dispatched.append(path), raising=False)

    assert module.abrir_pdf("C:/temp/attention.pdf")
    assert dispatched == ["C:/temp/attention.pdf"]


def test_real_template_render_produces_a_nonempty_pdf(tmp_path: Path):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    source = module.crear_pdf_temporal(
        "GENERAL",
        {
            "Fecha": "22/08/2026",
            "Hora": "08:00 AM",
            "Nombre": "PACIENTE DE PRUEBA",
            "Sexo": "Femenino",
            "Edad_num": 25,
            "Unidad": "Años",
            "Aseguradora (ARS)": "SIN SEGURO",
            "NSS": "",
            "Cédula": "00100000000",
            "Teléfono": "8090000000",
            "Dirección": "DIRECCION DE PRUEBA",
            "Nacionalidad": "DOMINICANA",
        },
        mostrar_error=False,
    )
    assert source is not None
    rendered = Path(source)
    try:
        assert rendered.stat().st_size > 0
        assert rendered.read_bytes().startswith(b"%PDF")
    finally:
        rendered.unlink(missing_ok=True)
