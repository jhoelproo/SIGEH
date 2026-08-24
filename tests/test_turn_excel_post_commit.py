from __future__ import annotations

import ctypes
import importlib
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from admission_hybrid import build_admission_order_key
from admission_v15_adapter import DEFAULT_V15_ROOT, _load_v15_modules


class _TurnDatabase:
    def __init__(self, rows=None):
        self.rows = (
            rows
            if rows is not None
            else [
                {
                    "id": 1,
                    "nombre": "Paciente de prueba",
                    "tipo_atencion": "EMERGENCIA",
                    "hoja": "GENERAL",
                    "hoja_normalizada": "GENERAL",
                    "ars_display": "HUMANO",
                }
            ]
        )

    def buscar_contexto_turno_existente(self, _turno_cfg):
        return {"turno_id": 282}

    def obtener_atenciones_para_rango_real(self, **_kwargs):
        return list(self.rows)


class _CanonicalTurnDatabase(_TurnDatabase):
    def __init__(self, rows):
        super().__init__(rows)
        self.requested_identity = None

    def get_operational_station_snapshot(self):
        return {
            "turn_id": 316,
            "operational_source_id": "source-current",
        }

    def build_turn_dataset(self, *, turn_id, operational_source_id):
        self.requested_identity = (turn_id, operational_source_id)
        return sorted(self.rows, key=build_admission_order_key)

    def obtener_atenciones_para_rango_real(self, **_kwargs):
        raise AssertionError("Excel no debe construir otra vista desde SQLite")


def _v15_module():
    _load_v15_modules(Path(DEFAULT_V15_ROOT))
    return importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")


def _turn_config():
    now = datetime.now(timezone.utc).astimezone().replace(
        microsecond=0, tzinfo=None
    )
    return {
        "representante": "USUARIO PRUEBA",
        "turno_codigo": "8AM_8AM",
        "fecha_base": now.date(),
        "inicio_real": now.strftime("%d/%m/%Y %I:%M:%S %p"),
        "inicio_real_dt": now,
    }


def test_open_canonical_excel_is_deferred_without_repeating_transition(
    tmp_path, monkeypatch
):
    v15 = _v15_module()
    canonical = tmp_path / "LISTADO DE PACIENTES EN EMERGENCIA.xlsx"
    queue = tmp_path / "excel_export_jobs.sqlite3"
    versions = tmp_path / "EXCEL_POR_TURNO"
    monkeypatch.setattr(v15, "EXCEL_PATH", str(canonical))
    monkeypatch.setattr(v15, "EXCEL_EXPORT_QUEUE_PATH", str(queue))
    monkeypatch.setattr(v15, "EXCEL_VERSIONED_DIR", str(versions))

    transition_id = "77777777-7777-4777-8777-777777777777"
    first_job = v15.enqueue_excel_export_job(transition_id, 282, _turn_config())
    duplicate_job = v15.enqueue_excel_export_job(transition_id, 282, _turn_config())
    assert duplicate_job == first_job

    real_update = v15._update_canonical_excel

    def locked_canonical(_source_file, _canonical_target):
        raise PermissionError(13, "archivo en uso")

    monkeypatch.setattr(v15, "_update_canonical_excel", locked_canonical)
    deferred = v15.process_excel_export_jobs(_TurnDatabase())
    assert deferred == {
        "completed": 0,
        "pending": 1,
        "errors": 0,
        "processed": 1,
        "skipped_empty": 0,
    }
    assert not canonical.exists()
    versioned = list(versions.glob("LISTADO_DE_PACIENTES_TURNO_282_*.xlsx"))
    assert len(versioned) == 1

    with sqlite3.connect(queue) as conn:
        status, attempts, error_code = conn.execute(
            "SELECT status,attempts,last_error_code FROM excel_export_jobs"
        ).fetchone()
    assert status == "PENDING"
    assert attempts == 1
    assert error_code == "EXCEL_EXPORT_DEFERRED_FILE_IN_USE"

    monkeypatch.setattr(v15, "_update_canonical_excel", real_update)
    retried = v15.process_excel_export_jobs(_TurnDatabase())
    assert retried == {
        "completed": 1,
        "pending": 0,
        "errors": 0,
        "processed": 1,
        "skipped_empty": 0,
    }
    assert canonical.is_file()
    assert canonical.read_bytes() == versioned[0].read_bytes()
    with sqlite3.connect(queue) as conn:
        status, attempts, total = conn.execute(
            "SELECT status,attempts,(SELECT COUNT(*) FROM excel_export_jobs) "
            "FROM excel_export_jobs"
        ).fetchone()
    assert status == "COMPLETED"
    assert attempts == 2
    assert total == 1


def test_empty_turn_does_not_create_or_replace_excel(tmp_path, monkeypatch):
    v15 = _v15_module()
    canonical = tmp_path / "LISTADO DE PACIENTES EN EMERGENCIA.xlsx"
    canonical.write_bytes(b"archivo historico")
    queue = tmp_path / "excel_export_jobs.sqlite3"
    versions = tmp_path / "EXCEL_POR_TURNO"
    monkeypatch.setattr(v15, "EXCEL_PATH", str(canonical))
    monkeypatch.setattr(v15, "EXCEL_EXPORT_QUEUE_PATH", str(queue))
    monkeypatch.setattr(v15, "EXCEL_VERSIONED_DIR", str(versions))

    v15.enqueue_excel_export_job(
        "88888888-8888-4888-8888-888888888888", 283, _turn_config()
    )
    result = v15.process_excel_export_jobs(_TurnDatabase(rows=[]))

    assert result == {
        "completed": 1,
        "pending": 0,
        "errors": 0,
        "processed": 1,
        "skipped_empty": 1,
    }
    assert canonical.read_bytes() == b"archivo historico"
    assert not list(versions.glob("*.xlsx"))
    with sqlite3.connect(queue) as conn:
        status, code = conn.execute(
            "SELECT status,last_error_code FROM excel_export_jobs"
        ).fetchone()
    assert status == "COMPLETED"
    assert code == "SKIPPED_EMPTY"


def test_admission_report_helpers_reject_empty_dataset(tmp_path):
    v15 = _v15_module()
    empty = v15.construir_resumen_desde_registros([], "Turno de prueba")
    assert v15.reportable_patient_count(empty) == 0
    with pytest.raises(v15.EmptyAdmissionReportError):
        v15.crear_pdf_reporte(empty, destino=str(tmp_path / "vacio.pdf"))
    with pytest.raises(v15.EmptyAdmissionReportError):
        v15.crear_excel_reporte_estadistico(empty, destino=str(tmp_path / "vacio.xlsx"))
    assert not (tmp_path / "vacio.pdf").exists()
    assert not (tmp_path / "vacio.xlsx").exists()


def test_nonempty_admission_reports_are_still_generated(tmp_path):
    v15 = _v15_module()
    summary = v15.construir_resumen_desde_registros(
        [
            {
                "id": 1,
                "nombre": "Paciente de prueba",
                "tipo_atencion": "EMERGENCIA",
                "ars_display": "HUMANO",
                "hoja_normalizada": "GENERAL",
                "fecha": "2026-08-12",
                "hora": "12:00",
                "nss": "000000001",
                "cedula": "",
            }
        ],
        "Turno de prueba",
        representante="USUARIO PRUEBA",
    )
    pdf = tmp_path / "con_datos.pdf"
    excel = tmp_path / "con_datos.xlsx"
    assert v15.reportable_patient_count(summary) == 1
    assert v15.crear_pdf_reporte(summary, destino=str(pdf)) == str(pdf)
    assert v15.crear_excel_reporte_estadistico(summary, destino=str(excel)) == str(
        excel
    )
    assert pdf.stat().st_size > 0
    assert excel.stat().st_size > 0


def test_excel_uses_canonical_dataset_order_and_hidden_global_identity(
    tmp_path, monkeypatch
):
    v15 = _v15_module()
    canonical = tmp_path / "LISTADO DE PACIENTES EN EMERGENCIA.xlsx"
    latest = tmp_path / "LISTADO.latest.xlsx"
    state = tmp_path / "excel_export_state.json"
    queue = tmp_path / "excel_export_jobs.sqlite3"
    monkeypatch.setattr(v15, "EXCEL_PATH", str(canonical))
    monkeypatch.setattr(v15, "EXCEL_LATEST_PATH", str(latest))
    monkeypatch.setattr(v15, "EXCEL_EXPORT_STATE_PATH", str(state))
    monkeypatch.setattr(v15, "EXCEL_EXPORT_QUEUE_PATH", str(queue))
    rows = [
        {
            "id": 2,
            "global_attention_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            "nombre": "PACIENTE B",
            "hoja_normalizada": "PEDIATRIA",
            "ars_display": "HUMANO",
            "created_at_effective_utc": "2026-08-13T12:00:00.000+00:00",
            "origin_device_id": "PC-2",
            "device_local_sequence": 1,
        },
        {
            "id": 1,
            "global_attention_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "nombre": "PACIENTE A",
            "hoja_normalizada": "GENERAL",
            "ars_display": "FUTURO",
            "created_at_effective_utc": "2026-08-13T12:00:00.000+00:00",
            "origin_device_id": "PC-1",
            "device_local_sequence": 1,
        },
    ]
    database = _CanonicalTurnDatabase(rows)

    assert v15.reconstruir_excel_turno(database, _turn_config()) == 2
    assert database.requested_identity == (316, "source-current")
    workbook = load_workbook(canonical, data_only=True)
    try:
        sheet = workbook.active
        assert [sheet.cell(row=row, column=1).value for row in (6, 7)] == [1, 2]
        assert [sheet.cell(row=row, column=2).value for row in (6, 7)] == [
            "PACIENTE A",
            "PACIENTE B",
        ]
        assert [sheet.cell(row=row, column=5).value for row in (6, 7)] == [
            "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        ]
        assert sheet.column_dimensions["E"].hidden is True
        assert sheet.column_dimensions["F"].hidden is True
        assert len(str(sheet["F1"].value)) == 64
    finally:
        workbook.close()


def test_live_excel_lock_keeps_latest_and_defers_canonical_publish(
    tmp_path, monkeypatch
):
    v15 = _v15_module()
    canonical = tmp_path / "LISTADO DE PACIENTES EN EMERGENCIA.xlsx"
    canonical.write_bytes(b"version abierta")
    latest = tmp_path / "LISTADO.latest.xlsx"
    state = tmp_path / "excel_export_state.json"
    queue = tmp_path / "excel_export_jobs.sqlite3"
    versions = tmp_path / "EXCEL_POR_TURNO"
    monkeypatch.setattr(v15, "EXCEL_PATH", str(canonical))
    monkeypatch.setattr(v15, "EXCEL_LATEST_PATH", str(latest))
    monkeypatch.setattr(v15, "EXCEL_EXPORT_STATE_PATH", str(state))
    monkeypatch.setattr(v15, "EXCEL_EXPORT_QUEUE_PATH", str(queue))
    monkeypatch.setattr(v15, "EXCEL_VERSIONED_DIR", str(versions))
    database = _CanonicalTurnDatabase(
        [
            {
                "id": 1,
                "global_attention_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "nombre": "PACIENTE A",
                "hoja_normalizada": "GENERAL",
                "ars_display": "FUTURO",
                "created_at_effective_utc": "2026-08-13T12:00:00+00:00",
                "origin_device_id": "PC-1",
                "device_local_sequence": 1,
            }
        ]
    )

    monkeypatch.setattr(
        v15,
        "_update_canonical_excel",
        lambda *_args: (_ for _ in ()).throw(PermissionError(13, "archivo en uso")),
    )
    assert v15.reconstruir_excel_turno(database, _turn_config()) == 1
    assert canonical.read_bytes() == b"version abierta"
    assert latest.is_file()
    export_state = v15._read_excel_export_state()
    assert export_state["excel_status"] == "FILE_LOCKED"
    assert export_state["patient_count"] == 1
    with sqlite3.connect(queue) as connection:
        source_file, status = connection.execute(
            "SELECT source_file,status FROM excel_export_jobs"
        ).fetchone()
    assert Path(source_file) == latest
    assert status == "PENDING"


def test_turn_dialog_commits_before_excel_and_does_not_use_excel_com():
    source_path = Path(DEFAULT_V15_ROOT) / "facturacion_tabs_pyside6.py"
    source = source_path.read_text(encoding="utf-8")
    dialog_start = source.index("        def refrescar_turnos():")
    start = source.index("        def _aplicar_cambio():", dialog_start)
    end = source.index("        aplicando =", start)
    apply_block = source[start:end]

    assert "reconstruir_excel_turno(self.db, turno_cfg_nuevo)" not in apply_block
    assert apply_block.index("self.db.obtener_o_crear_turno(") < apply_block.index(
        "enqueue_excel_export_job("
    )
    assert apply_block.index("win.destroy()") < apply_block.index(
        "self._run_turn_post_commit_effects"
    )
    assert "win32com" not in source
    assert "GetActiveObject" not in source
    assert "Dispatch(" not in source
    assert "DispatchEx(" not in source
    assert "os.startfile(ruta)" in source


def test_empty_excel_dataset_is_recorded_only_once(tmp_path, monkeypatch, caplog):
    v15 = _v15_module()
    state_path = tmp_path / "excel_export_state.json"
    monkeypatch.setattr(v15, "EXCEL_EXPORT_STATE_PATH", str(state_path))
    database = _TurnDatabase(rows=[])

    with caplog.at_level(logging.INFO, logger=v15.APP_LOG.name):
        assert v15.reconstruir_excel_turno(database, _turn_config()) == 0
        assert v15.reconstruir_excel_turno(database, _turn_config()) == 0

    assert caplog.text.count("ADMISSION_EXCEL_SKIPPED_EMPTY") == 1
    assert v15._read_excel_export_state()["excel_status"] == "SKIPPED_EMPTY"


@pytest.mark.skipif(
    os.name != "nt", reason="Valida el bloqueo real de archivos de Windows"
)
def test_real_windows_file_lock_does_not_get_closed_by_exporter(tmp_path):
    v15 = _v15_module()
    source = tmp_path / "versionado.xlsx"
    canonical = tmp_path / "LISTADO DE PACIENTES EN EMERGENCIA.xlsx"
    source.write_bytes(b"nuevo")
    canonical.write_bytes(b"anterior")

    create_file = ctypes.windll.kernel32.CreateFileW
    create_file.restype = ctypes.c_void_p
    handle = create_file(
        str(canonical),
        0x80000000,  # GENERIC_READ
        0,  # sin compartir: equivalente a libro bloqueado
        None,
        3,  # OPEN_EXISTING
        0x80,  # FILE_ATTRIBUTE_NORMAL
        None,
    )
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        assert v15.excel_canonical_in_use(str(canonical)) is True
        with pytest.raises(OSError) as caught:
            v15._update_canonical_excel(str(source), str(canonical))
        assert v15._excel_file_in_use(caught.value)
        # La aplicación no cierra ni altera el handle perteneciente al usuario.
        assert ctypes.windll.kernel32.GetFileType(handle) != 0
    finally:
        assert ctypes.windll.kernel32.CloseHandle(handle)

    v15._update_canonical_excel(str(source), str(canonical))
    assert canonical.read_bytes() == b"nuevo"
