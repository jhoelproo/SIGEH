from __future__ import annotations

import importlib
import logging
import sqlite3
import statistics
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import sqlite_write_coordinator
from admission_hybrid import OfflineAdmissionStore
from patient_directory import LocalPatientDirectory
from sqlite_write_coordinator import (
    SQLiteWriteTimeout,
    connect_local_sqlite,
    get_sqlite_write_coordinator,
    prepare_sqlite_database,
    run_with_bounded_sqlite_busy_retry,
)


def _create_write_database(path: Path) -> None:
    assert prepare_sqlite_database(path) == "WAL"
    with connect_local_sqlite(path, operation="test-schema") as con:
        con.execute("CREATE TABLE writes(id INTEGER PRIMARY KEY, source TEXT NOT NULL)")


def _record_write(path: Path, source: str) -> float:
    started = time.perf_counter()
    with connect_local_sqlite(path, operation=source) as con:
        con.execute("INSERT INTO writes(source) VALUES(?)", (source,))
    return (time.perf_counter() - started) * 1000.0


def test_existing_patient_hydration_uses_bootstrapped_schema_without_ddl(
    tmp_path: Path, caplog
):
    path = tmp_path / "admission.db"
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE app_metadata(clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY AUTOINCREMENT,nombre TEXT,cedula TEXT,
              cedula_clean TEXT,nss TEXT,nss_clean TEXT,telefono TEXT,direccion TEXT,
              nacionalidad TEXT,ars TEXT,global_patient_id TEXT,server_revision INTEGER,
              sync_state TEXT,is_deleted INTEGER,updated_at TEXT
            );
            CREATE TABLE paciente_identificadores(
              paciente_id INTEGER,tipo TEXT,valor_normalizado TEXT,activo INTEGER,conflicto INTEGER
            );
            CREATE TABLE patient_directory_state(
              state_key TEXT PRIMARY KEY,state_value TEXT NOT NULL,updated_at TEXT NOT NULL
            );
            INSERT INTO app_metadata(clave,valor)
              VALUES('admission.patient_directory_schema_version','1');
            """
        )
    caplog.set_level(logging.INFO, logger="hospital.admission.sqlite")
    directory = LocalPatientDirectory(path)
    patient_id = str(uuid.uuid4())
    hydrated = directory.hydrate(
        {
            "global_patient_id": patient_id,
            "nombre": "PACIENTE DE PRUEBA",
            "cedula": "00100000000",
            "nss": "123456789",
            "server_revision": 1,
        }
    )
    assert hydrated["global_patient_id"] == patient_id
    assert directory._initialized is True
    assert not any(
        "patient-directory-schema" in record.message for record in caplog.records
    )


def test_sync_and_attention_writes_finish_without_timeout(tmp_path: Path, caplog):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    caplog.set_level(logging.INFO, logger="hospital.admission.sqlite")
    sync_started = threading.Event()
    failures: list[BaseException] = []
    attention_elapsed: list[float] = []

    def sync_apply() -> None:
        try:
            with connect_local_sqlite(path, operation="sync-apply-batch") as con:
                con.execute("INSERT INTO writes(source) VALUES('sync')")
                sync_started.set()
                time.sleep(0.04)
        except (AssertionError, sqlite3.Error) as exc:  # assertion below preserves worker errors
            failures.append(exc)

    def attention_save() -> None:
        try:
            assert sync_started.wait(1)
            attention_elapsed.append(_record_write(path, "attention-local-save"))
        except (AssertionError, sqlite3.Error) as exc:  # assertion below preserves worker errors
            failures.append(exc)

    workers = [threading.Thread(target=sync_apply), threading.Thread(target=attention_save)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(2)

    assert failures == []
    assert attention_elapsed and attention_elapsed[0] < 250
    messages = [record.message for record in caplog.records]
    assert any("SQLITE_WRITE_WAIT_START operation=attention-local-save" in message for message in messages)
    assert any("SQLITE_WRITE_RELEASED operation=sync-apply-batch" in message for message in messages)


def test_timeout_diagnostic_identifies_the_lock_holder(tmp_path: Path, caplog):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    caplog.set_level(logging.INFO, logger="hospital.admission.sqlite")
    started = threading.Event()

    def hold_writer() -> None:
        with connect_local_sqlite(path, operation="patient-directory-schema") as con:
            con.execute("INSERT INTO writes(source) VALUES('schema')")
            started.set()
            time.sleep(0.08)

    worker = threading.Thread(target=hold_writer, name="directory-bootstrap")
    worker.start()
    assert started.wait(1)
    try:
        get_sqlite_write_coordinator(path).acquire("attention-local-save", timeout=0.02)
    except SQLiteWriteTimeout:
        pass
    else:
        raise AssertionError("The bounded writer timeout was not raised.")
    worker.join(1)

    timeout_messages = [
        record.message for record in caplog.records if "SQLITE_WRITE_TIMEOUT" in record.message
    ]
    assert len(timeout_messages) == 1
    assert "lock_holder_operation=patient-directory-schema" in timeout_messages[0]
    assert "lock_holder_thread=directory-bootstrap" in timeout_messages[0]


def test_long_transaction_watchdog_records_owner_without_interrupting_it(
    tmp_path: Path, caplog, monkeypatch
):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    caplog.set_level(logging.INFO, logger="hospital.admission.sqlite")
    monkeypatch.setattr(sqlite_write_coordinator, "SQLITE_LONG_TRANSACTION_MS", 5.0)

    with connect_local_sqlite(path, operation="sync-apply-batch") as con:
        con.execute("INSERT INTO writes(source) VALUES('slow-sync')")
        time.sleep(0.015)

    messages = [record.message for record in caplog.records]
    diagnostic = next(
        message for message in messages if "SQLITE_LONG_TRANSACTION" in message
    )
    assert "operation=sync-apply-batch" in diagnostic
    assert "connection_id=" in diagnostic
    assert "owner_stack=" in diagnostic


def test_busy_retry_is_bounded_and_reuses_the_same_callback(monkeypatch):
    monkeypatch.setattr(
        sqlite_write_coordinator,
        "SQLITE_BUSY_RETRY_DELAYS_SECONDS",
        (0.001, 0.001),
    )
    attempts = 0

    def transient_write():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise sqlite3.OperationalError("database is locked")
        return "committed"

    assert run_with_bounded_sqlite_busy_retry("output-state", transient_write) == "committed"
    assert attempts == 3


def test_external_sqlite_lock_recovers_without_duplicate_write(tmp_path: Path):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    blocker = sqlite3.connect(path)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("INSERT INTO writes(source) VALUES('blocker')")
    finished = threading.Event()
    failures: list[BaseException] = []

    def write_after_lock() -> None:
        try:
            _record_write(path, "attention-local-save")
            finished.set()
        except BaseException as exc:  # preserve the worker failure for the assertion
            failures.append(exc)

    worker = threading.Thread(target=write_after_lock)
    worker.start()
    time.sleep(0.05)
    blocker.commit()
    blocker.close()
    worker.join(2)

    assert failures == []
    assert finished.is_set()
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 2


def test_output_state_write_has_a_short_worker_timeout(tmp_path: Path):
    path = tmp_path / "admission.db"
    assert prepare_sqlite_database(path) == "WAL"
    with sqlite3.connect(path) as con:
        con.execute(
            """CREATE TABLE trabajos_salida(
                 atencion_id INTEGER PRIMARY KEY,
                 excel_estado TEXT,
                 pdf_estado TEXT,
                 impresion_estado TEXT,
                 ultimo_error TEXT,
                 pdf_path TEXT,
                 pdf_sha256 TEXT,
                 intentos INTEGER DEFAULT 0,
                 updated_at TEXT
               )"""
        )
        con.execute(
            "INSERT INTO trabajos_salida(atencion_id,pdf_estado) VALUES(1,'PENDIENTE')"
        )

    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    database = module.DatabaseManager.__new__(module.DatabaseManager)
    database.db_name = str(path)
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_writer() -> None:
        with connect_local_sqlite(path, operation="sync-apply-batch") as con:
            con.execute("INSERT OR REPLACE INTO writes(id,source) VALUES(1,'sync')")
            holder_ready.set()
            release_holder.wait(2)

    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE writes(id INTEGER PRIMARY KEY, source TEXT)")
    holder = threading.Thread(target=hold_writer, name="sync-hydration")
    holder.start()
    assert holder_ready.wait(1)
    started = time.perf_counter()
    try:
        database.actualizar_trabajo_salida(1, "pdf", "PROCESANDO")
    except SQLiteWriteTimeout:
        elapsed = time.perf_counter() - started
    else:
        raise AssertionError("The output-state write did not honor its short timeout.")
    finally:
        release_holder.set()
        holder.join(2)

    assert elapsed < 0.8
    database.actualizar_trabajo_salida(1, "pdf", "COMPLETADO")
    database.limpiar_error_trabajo_salida(1)
    with sqlite3.connect(path) as con:
        assert con.execute(
            "SELECT pdf_estado,ultimo_error FROM trabajos_salida WHERE atencion_id=1"
        ).fetchone() == ("COMPLETADO", None)


def test_writer_releases_after_exception_and_twenty_short_saves(tmp_path: Path):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    try:
        with connect_local_sqlite(path, operation="exception-write") as con:
            con.execute("INSERT INTO writes(source) VALUES('broken')")
            raise RuntimeError("forced write failure")
    except RuntimeError:
        pass

    elapsed = [_record_write(path, "attention-local-save") for _ in range(20)]
    assert max(elapsed) < 200
    assert statistics.quantiles(elapsed, n=20, method="inclusive")[18] < 200
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 20


def test_two_hundred_mixed_primary_writes_finish_without_loss_or_abandoned_lock(
    tmp_path: Path,
):
    path = tmp_path / "admission.db"
    assert prepare_sqlite_database(path) == "WAL"
    with connect_local_sqlite(path, operation="test-schema") as con:
        con.execute(
            "CREATE TABLE records(identity_key TEXT PRIMARY KEY, operation TEXT NOT NULL)"
        )
    start = threading.Barrier(5)
    failures: list[BaseException] = []
    operations = (
        "attention-local-save",
        "sync-apply-batch",
        "output-state",
        "summary-local-pending",
    )

    def write_group(worker_id: int, operation: str) -> None:
        try:
            start.wait(2)
            for item in range(50):
                with connect_local_sqlite(path, operation=operation) as con:
                    con.execute(
                        "INSERT INTO records(identity_key,operation) VALUES(?,?)",
                        (f"{worker_id}:{item}", operation),
                    )
                    if item % 17 == 0:
                        time.sleep(0.001)
        except BaseException as exc:  # preserve every worker failure
            failures.append(exc)

    workers = [
        threading.Thread(target=write_group, args=(index, operation))
        for index, operation in enumerate(operations)
    ]
    for worker in workers:
        worker.start()
    start.wait(2)
    for worker in workers:
        worker.join(10)

    assert failures == []
    assert all(not worker.is_alive() for worker in workers)
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 200
        assert con.execute(
            "SELECT COUNT(DISTINCT identity_key) FROM records"
        ).fetchone()[0] == 200
    snapshot = get_sqlite_write_coordinator(path).diagnostic_snapshot()
    assert snapshot["operation"] == ""
    assert snapshot["queue_depth"] == 0


def test_duplicate_write_is_reported_as_integrity_error_not_writer_timeout(tmp_path: Path):
    path = tmp_path / "admission.db"
    assert prepare_sqlite_database(path) == "WAL"
    with connect_local_sqlite(path, operation="test-schema") as con:
        con.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, identity_key TEXT UNIQUE)")
    with connect_local_sqlite(path, operation="attention-local-save") as con:
        con.execute("INSERT INTO records(identity_key) VALUES('existing-patient')")
    try:
        with connect_local_sqlite(path, operation="attention-local-save") as con:
            con.execute("INSERT INTO records(identity_key) VALUES('existing-patient')")
    except sqlite3.IntegrityError as exc:
        assert "UNIQUE constraint failed" in str(exc)
    else:
        raise AssertionError("The duplicate attention was not rejected.")
    with connect_local_sqlite(path, operation="attention-local-save") as con:
        con.execute("INSERT INTO records(identity_key) VALUES('next-patient')")


def test_excel_and_hydrate_workers_do_not_timeout_an_attention_save(tmp_path: Path):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    started = threading.Barrier(3)
    failures: list[BaseException] = []

    def background_write(operation: str) -> None:
        try:
            started.wait(1)
            with connect_local_sqlite(path, operation=operation) as con:
                con.execute("INSERT INTO writes(source) VALUES(?)", (operation,))
                time.sleep(0.025)
        except (AssertionError, sqlite3.Error) as exc:  # assertion below preserves worker errors
            failures.append(exc)

    workers = [
        threading.Thread(target=background_write, args=("excel-export-state",)),
        threading.Thread(target=background_write, args=("patient-directory-hydrate-batch",)),
    ]
    for worker in workers:
        worker.start()
    started.wait(1)
    attention_elapsed = _record_write(path, "attention-local-save")
    for worker in workers:
        worker.join(2)

    assert failures == []
    assert attention_elapsed < 250
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT COUNT(*) FROM writes").fetchone()[0] == 3


def test_pdf_render_starts_after_its_sqlite_metadata_write_is_released(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "admission.db"
    _create_write_database(path)
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    rendered_with_active_operation: list[str] = []

    class Database:
        def obtener_trabajo_salida(self, _attention_id):
            return {"excel_estado": "COMPLETADO", "impresion_estado": "PENDIENTE"}

        def actualizar_trabajo_salida(self, _attention_id, _stage, _state, **_kwargs):
            with connect_local_sqlite(path, operation="pdf-metadata") as con:
                con.execute("INSERT INTO writes(source) VALUES('pdf-metadata')")

        def limpiar_error_trabajo_salida(self, _attention_id):
            with connect_local_sqlite(path, operation="pdf-metadata") as con:
                con.execute("INSERT INTO writes(source) VALUES('clear-error')")

        @staticmethod
        def obtener_atencion_por_id(_attention_id):
            return {"hoja": "GENERAL", "fecha": "2026-08-22", "hora": "08:00"}

    def render(_sheet, _data, mostrar_error=False):
        del mostrar_error
        rendered_with_active_operation.append(
            get_sqlite_write_coordinator(path).active_operation
        )
        sheet = tmp_path / "sheet.pdf"
        sheet.write_bytes(b"%PDF-1.4\n")
        return str(sheet)

    monkeypatch.setattr(module, "crear_pdf_temporal", render)
    monkeypatch.setattr(module, "imprimir_pdf", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(module, "programar_limpieza_pdf_temporal", lambda *_args, **_kwargs: None)
    app = module.App.__new__(module.App)
    app.db = Database()
    app.app_settings = {"print_copies_hoja": 1}
    app._snapshot_a_datos = lambda attention: dict(attention)
    app._post_to_ui = lambda _callback: None
    app._procesar_salida_atencion(1, "GENERAL", {}, {}, abrir_pdf_final=False)

    assert rendered_with_active_operation == [""]


def test_output_worker_always_returns_control_to_ui_when_busy_error_logging_also_fails():
    """A transient replica lock must not leave the admission button disabled."""
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    posted: list[dict] = []

    class BusyDatabase:
        @staticmethod
        def obtener_trabajo_salida(_attention_id):
            return {"excel_estado": "COMPLETADO", "impresion_estado": "NO_APLICA"}

        @staticmethod
        def actualizar_trabajo_salida(*_args, **_kwargs):
            raise SQLiteWriteTimeout("database is busy")

    app = module.App.__new__(module.App)
    app.db = BusyDatabase()
    app.app_settings = {}
    app._post_to_ui = lambda callback: (callback(), posted.append({"delivered": True}))
    app._finalizar_salida_atencion = lambda result: posted.append(dict(result))
    app._release_output_inflight = lambda _attention_id: None
    app._render_immediate_attention_pdf = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        SQLiteWriteTimeout("database is busy")
    )

    app._procesar_salida_atencion(
        41,
        "GENERAL",
        {},
        {},
        abrir_pdf_final=False,
        flow_started_at=time.perf_counter(),
    )

    delivered_result = next(item for item in posted if "atencion_id" in item)
    assert delivered_result["atencion_id"] == 41
    assert delivered_result["errores"]["PDF"] == "database is busy"


def test_output_worker_reports_print_and_excel_failures_then_returns_to_ui(monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")
    job = {
        "excel_estado": "PENDIENTE",
        "pdf_estado": "PENDIENTE",
        "impresion_estado": "PENDIENTE",
    }

    class Database:
        @staticmethod
        def obtener_trabajo_salida(_attention_id):
            return dict(job)

        @staticmethod
        def actualizar_trabajo_salida(_attention_id, stage, state, **_kwargs):
            job[f"{stage}_estado"] = state

        @staticmethod
        def obtener_turno_config_atencion(_attention_id):
            return {"id": 1}

    results: list[dict] = []
    app = module.App.__new__(module.App)
    app.db = Database()
    app.app_settings = {"print_copies_hoja": 1}
    app._post_to_ui = lambda callback: callback()
    app._apply_immediate_pdf_open_result = lambda *_args: None
    app._finalizar_salida_atencion = lambda result: results.append(dict(result))
    app._release_output_inflight = lambda _attention_id: None
    app._render_immediate_attention_pdf = lambda *_args, **_kwargs: SimpleNamespace(
        attention_id=42,
        pdf_path="attention.pdf",
        should_print=True,
    )
    app._open_immediate_attention_pdf = lambda _output: True
    monkeypatch.setattr(module, "imprimir_pdf", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        module,
        "reconstruir_excel_turno",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("excel busy")),
    )
    monkeypatch.setattr(
        module, "programar_limpieza_pdf_temporal", lambda *_args, **_kwargs: None
    )

    app._procesar_salida_atencion(42, "GENERAL", {}, {"id": 1})

    assert results[-1]["errores"] == {
        "Impresión": "No fue posible enviar la hoja a la impresora.",
        "Excel": "excel busy",
    }


def test_output_state_read_and_clear_failures_preserve_worker_completion(monkeypatch):
    module = importlib.import_module("ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6")

    class Database:
        read_attempts = 0

        @classmethod
        def obtener_trabajo_salida(cls, _attention_id):
            cls.read_attempts += 1
            if cls.read_attempts == 1:
                raise sqlite3.OperationalError("database is busy")
            return {"excel_estado": "COMPLETADO", "impresion_estado": "NO_APLICA"}

        @staticmethod
        def actualizar_trabajo_salida(*_args, **_kwargs):
            return None

        @staticmethod
        def limpiar_error_trabajo_salida(_attention_id):
            raise SQLiteWriteTimeout("database is busy")

    results: list[dict] = []
    app = module.App.__new__(module.App)
    app.db = Database()
    app.app_settings = {}
    app._post_to_ui = lambda callback: callback()
    app._apply_immediate_pdf_open_result = lambda *_args: None
    app._finalizar_salida_atencion = lambda result: results.append(dict(result))
    app._release_output_inflight = lambda _attention_id: None
    app._render_immediate_attention_pdf = lambda *_args, **_kwargs: SimpleNamespace(
        attention_id=43,
        pdf_path="attention.pdf",
        should_print=False,
    )
    app._open_immediate_attention_pdf = lambda _output: True
    monkeypatch.setattr(
        module, "programar_limpieza_pdf_temporal", lambda *_args, **_kwargs: None
    )
    monkeypatch.setattr(module, "reconstruir_excel_turno", lambda *_args: 1)

    app._procesar_salida_atencion(43, "GENERAL", {}, {"id": 1})

    assert results[-1]["atencion_id"] == 43
    assert results[-1]["errores"] == {}


def test_remote_events_are_applied_in_bounded_local_batches(monkeypatch):
    connection = sqlite3.connect(":memory:")
    try:
        store = OfflineAdmissionStore(connection)
        calls: list[tuple[int, bool]] = []
        monkeypatch.setattr(store, "initialize", lambda: None)
        monkeypatch.setattr(store, "last_cloud_cursor", lambda: 121)

        def apply_batch(events, *, advance_cursor):
            calls.append((len(list(events)), advance_cursor))
            return calls[-1][0]

        monkeypatch.setattr(store, "_apply_remote_events_batch", apply_batch)
        result = store.apply_remote_events(
            [{"event_uuid": str(index)} for index in range(121)]
        )

        assert calls == [
            (20, True),
            (20, True),
            (20, True),
            (20, True),
            (20, True),
            (20, True),
            (1, True),
        ]
        assert result == 121
    finally:
        connection.close()
