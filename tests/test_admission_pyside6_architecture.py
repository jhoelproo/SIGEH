import os
from pathlib import Path
import tempfile
import sys
import shutil
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from admission_pyside6 import (
    AdmissionController,
    AdmissionInput,
    AdmissionRepository,
    AdmissionService,
    AdmissionStandaloneWindow,
    AdmissionWidget,
    AdmissionHistoryDialog,
    AdmissionDocumentService,
    AdmissionInternalConfigDialog,
    AdmissionPreferencesDialog,
    AdmissionReportDialog,
    AppContext,
    LegacyAdmissionBackend,
)
from integrated_admission import AdmissionModuleController


class FakeConnection:
    pass


class FakeBackend:
    def __init__(self):
        self.rows = []
        self.current_shift = {"id": 7, "representative": "PRUEBA"}
        self.document_calls = []
        self.print_states = {}
        self.preferences = {"print_auto_hoja": True, "history_page_size": 100}

    def search_patients(self, text, *, limit=80, offset=0):
        needle = "".join(str(text).split()).replace("-", "").casefold()
        return [
            row for row in self.rows
            if needle in " ".join(str(value) for value in row.values()).replace("-", "").casefold()
        ]

    def find_duplicate(self, data, shift):
        return None

    def register_attention(self, data):
        record = {"id": len(self.rows) + 1, **data}
        self.rows.append(record)
        return record

    def update_attention(self, attention_id, data):
        return {"id": attention_id, **data}

    def cancel_attention(self, attention_id, actor, reason):
        return {"id": attention_id, "actor": actor, "reason": reason}

    def detect_shift(self):
        return dict(self.current_shift)

    def open_shift(self, data):
        self.current_shift = {"id": 8, **data}
        return dict(self.current_shift)

    def change_shift(self, current_shift, new_shift):
        self.current_shift = {"id": int(current_shift.get("id", 7)) + 1, **new_shift}
        return dict(self.current_shift)

    def close_shift(self, shift_id, **metadata):
        self.current_shift = {}
        return True

    def shift_summary(self, shift_id=None):
        return {"total": len(self.rows), "emergencies": len(self.rows), "consultations": 0, "uninsured": 0}

    def list_representatives(self):
        return ["PRUEBA", "OTRO"]

    def list_history(self, **filters):
        rows = list(reversed(self.rows))
        text = str(filters.get("text") or "").replace("-", "").strip().casefold()
        if text:
            rows = [
                row for row in rows
                if text in " ".join(str(value) for value in row.values()).replace("-", "").casefold()
            ]
        if filters.get("mode") == "Sin seguro":
            rows = [row for row in rows if str(row.get("ars") or "").upper() == "SIN SEGURO"]
        offset = int(filters.get("offset") or 0)
        limit = int(filters.get("limit") or 100)
        return rows[offset:offset + limit]

    def get_attention(self, attention_id):
        return next((row for row in self.rows if int(row.get("id", 0)) == int(attention_id)), None)

    def generate_detail_sheet(self, attention_id):
        path = Path(tempfile.gettempdir()) / f"admision_test_{attention_id}.pdf"
        path.write_bytes(b"%PDF-1.4\n% test\n")
        self.document_calls.append(("generate", int(attention_id), str(path)))
        return str(path)

    def open_document(self, path):
        self.document_calls.append(("open", str(path)))
        return True

    def print_document(self, path, copies=1):
        self.document_calls.append(("print", str(path), int(copies)))
        return True

    def schedule_document_cleanup(self, path, delay=900):
        self.document_calls.append(("cleanup", str(path), int(delay)))
        return True

    def update_print_state(self, attention_id, state, **metadata):
        self.print_states[int(attention_id)] = (str(state), dict(metadata))
        return True

    def list_pending_prints(self):
        return [
            {"atencion_id": attention_id, "nombre": "PRUEBA", "impresion_estado": state[0], "intentos": 1}
            for attention_id, state in self.print_states.items()
            if state[0] in {"PENDIENTE", "FALLIDO"}
        ]

    def report_summary(self, start, end, label):
        return {
            "periodo": label,
            "total_general": len(self.rows),
            "cantidad_sin_seguro": 0,
            "cantidad_urgencias": len(self.rows),
            "cantidad_consultas": 0,
            "por_seguro": [("ARS PRUEBA", len(self.rows))],
            "por_especialidad": [("GENERAL", len(self.rows))],
        }

    def generate_report_pdf(self, summary):
        path = Path(tempfile.gettempdir()) / "admision_report_test.pdf"
        path.write_bytes(b"%PDF-1.4\n% report test\n")
        return str(path)

    def generate_report_excel(self, summary):
        path = Path(tempfile.gettempdir()) / "admision_report_test.xlsx"
        path.write_bytes(b"PK\x03\x04test")
        return str(path)

    def open_current_excel(self):
        path = Path(tempfile.gettempdir()) / "admision_current_test.xlsx"
        path.write_bytes(b"PK\x03\x04test")
        return str(path)

    def load_preferences(self):
        return dict(self.preferences)

    def save_preferences(self, values):
        self.preferences = dict(values)
        return True

    def configuration_snapshot(self):
        return {
            "ars": [("ARS PRUEBA", 3)],
            "representatives": ["PRUEBA"],
            "nss_reviews": [{"id": 1, "nss_normalizado": "0001", "estado": "PENDIENTE"}],
            "backups": [{"created_at": "2026-08-05", "reason": "PRUEBA", "status": "Válido"}],
        }


def build_stack():
    connection = FakeConnection()
    context = AppContext(
        connection_factory=lambda: connection,
        user={"id": 3, "username": "tester", "role": "admin", "permissions": {"admission.cancel"}},
        session_id="session-existing",
        device_id="device-existing",
        current_shift={"id": 7},
    )
    repository = AdmissionRepository(FakeBackend())
    service = AdmissionService(context, repository)
    controller = AdmissionController(service)
    return connection, context, repository, service, controller


def valid_input():
    return AdmissionInput(
        name="PACIENTE DE PRUEBA",
        age=30,
        cedula="001-0000000-1",
        phone="8095550101",
        ars="ARS PRUEBA",
        nss="0001234567",
    )


def test_context_reuses_identity_and_connection_provider():
    connection, context, *_ = build_stack()
    assert context.connection() is connection
    assert context.username == "tester"
    assert context.role == "administrador"
    assert context.session_id == "session-existing"


def test_repository_service_controller_contracts_emit_once():
    _, _, repository, service, controller = build_stack()
    emitted = []
    controller.attention_created.connect(emitted.append)
    result = controller.register(valid_input())
    assert result.ok
    assert result.data["id"] == 1
    assert len(emitted) == 1
    assert repository.search_patients("PRUEBA")


def test_widget_and_wrapper_use_single_qapplication():
    app = QApplication.instance() or QApplication([])
    _, context, _, _, controller = build_stack()
    widget = AdmissionWidget(context, controller)
    window = AdmissionStandaloneWindow(context, controller)
    assert QApplication.instance() is app
    assert window.centralWidget().__class__ is AdmissionWidget
    widget.close()
    window.close()


def test_phase3_form_accepts_writing_and_tab_navigation():
    app = QApplication.instance() or QApplication([])
    _, context, _, _, controller = build_stack()
    window = AdmissionStandaloneWindow(context, controller)
    window.resize(1600, 900)
    window.show()
    app.processEvents()
    widget = window.centralWidget()
    widget.name_edit.setFocus()
    QTest.keyClicks(widget.name_edit, "PACIENTE")
    assert widget.name_edit.text() == "PACIENTE"
    QTest.keyClick(widget.name_edit, Qt.Key_Tab)
    assert QApplication.focusWidget() in {widget.age_spin, widget.age_spin.lineEdit()}
    assert window.rect().contains(widget.register_button.geometry().bottomRight())
    window.resize(1920, 1080)
    app.processEvents()
    assert widget.width() > 1500
    window.close()


def test_phase10_layout_profiles_dark_theme_and_control_geometry():
    app = QApplication.instance() or QApplication([])
    _, context, _, _, controller = build_stack()
    window = AdmissionStandaloneWindow(context, controller)
    window.resize(1366, 768)
    window.show()
    widget = window.centralWidget()
    profiles = (
        ("MUY_COMPACTO", "MUY_COMPACTA", 100),
        ("COMPACTO", "COMPACTA", 110),
        ("ESTANDAR", "NORMAL", 100),
        ("AMPLIO", "COMODA", 125),
    )
    for profile, density, text_percent in profiles:
        widget.apply_layout_profile(
            SimpleNamespace(
                applied_profile=profile,
                density=density,
                text_percent=text_percent,
            )
        )
        app.processEvents()
        assert widget._layout_profile == profile
        assert widget.content_splitter.widget(1).width() >= 250
        assert widget.register_button.isVisible()
        assert widget.name_edit.isVisible()
        assert widget.nss_edit.isVisible()
        assert widget.rect().contains(widget.register_button.mapTo(widget, widget.register_button.rect().bottomRight()))
    widget.apply_theme(True)
    assert widget._is_dark is True
    assert "#111827" in widget.styleSheet()
    history = widget.open_history(False)
    app.processEvents()
    assert "#111827" in history.styleSheet()
    history.close()
    widget.apply_theme(False)
    assert "#F2F6FB" in widget.styleSheet()
    window.close()


def test_phase4_search_load_register_clear_and_no_double_submit():
    app = QApplication.instance() or QApplication([])
    _, context, repository, _, controller = build_stack()
    repository.backend.rows.append(
        {
            "id": 9,
            "name": "PACIENTE EXISTENTE",
            "nss": "0000123456",
            "cedula": "00100000001",
            "phone": "8095550101",
            "ars": "ARS PRUEBA",
            "age": 27,
        }
    )
    widget = AdmissionWidget(context, controller)
    controller.search("0000-123456")
    app.processEvents()
    assert widget.name_edit.text() == "PACIENTE EXISTENTE"
    widget.clear_form()
    payload = valid_input()
    widget.name_edit.setText(payload.name)
    widget.age_spin.setValue(payload.age)
    widget.cedula_edit.setText(payload.cedula)
    widget.phone_edit.setText(payload.phone)
    widget.ars_combo.setCurrentText(payload.ars)
    widget.nss_edit.setText(payload.nss)
    QTest.mouseClick(widget.register_button, Qt.LeftButton)
    QTest.mouseClick(widget.register_button, Qt.LeftButton)
    app.processEvents()
    assert len(repository.backend.rows) == 2
    assert widget.name_edit.text() == ""
    widget.close()


def test_phase4_edit_and_writing_survive_validation_error():
    app = QApplication.instance() or QApplication([])
    _, context, _, _, controller = build_stack()
    widget = AdmissionWidget(context, controller)
    widget.load_attention(
        {
            "id": 11,
            "name": "ANTES",
            "age": 40,
            "cedula": "00100000001",
            "phone": "8095550101",
            "ars": "ARS PRUEBA",
            "nss": "0001234567",
        },
        editing=True,
    )
    widget.name_edit.setText("DESPUÉS")
    QTest.mouseClick(widget.register_button, Qt.LeftButton)
    assert widget.editing_attention_id == 11
    widget.clear_form()
    QTest.mouseClick(widget.register_button, Qt.LeftButton)
    assert widget.register_button.isEnabled()
    widget.name_edit.setFocus()
    QTest.keyClicks(widget.name_edit, "RECUPERADO")
    assert widget.name_edit.text() == "RECUPERADO"
    widget.close()


def test_legacy_backend_maps_contract_without_database_writes():
    class LegacyDb:
        def __init__(self):
            self.saved = None

        def buscar_pacientes_avanzado(self, text, limite=80):
            return [{"id": 1, "nombre": text}]

        def buscar_atencion_en_turno(self, *args, **kwargs):
            return None

        def guardar_atencion(self, data, sheet, turno_cfg=None):
            self.saved = (data, sheet, turno_cfg)
            return 22

        def actualizar_atencion_especifica(self, attention_id, data, **kwargs):
            return attention_id

        def borrar_atencion(self, attention_id, motivo="", usuario=""):
            return bool(attention_id and motivo and usuario)

    legacy_db = LegacyDb()
    shift = {
        "id": 7,
        "fecha_inicio": __import__("datetime").datetime(2026, 8, 5, 8),
        "fecha_fin": __import__("datetime").datetime(2026, 8, 6, 8),
    }
    backend = LegacyAdmissionBackend(
        legacy_db,
        shift_provider=lambda: shift,
        username_provider=lambda: "tester",
    )
    repository = AdmissionRepository(backend)
    result = repository.register_attention(valid_input().as_mapping())
    assert result == 22
    assert legacy_db.saved[0]["Nombre"] == "PACIENTE DE PRUEBA"
    assert legacy_db.saved[1] == "GENERAL"
    assert legacy_db.saved[2] == shift
    assert repository.cancel_attention(22, "tester", "motivo válido")


def test_phase5_turn_context_summary_and_shared_concurrency_contract():
    _, context_a, repository, service_a, controller_a = build_stack()
    context_b = AppContext(
        connection_factory=context_a.connection_factory,
        user={"username": "tester2", "role": "auxiliar"},
        session_id="session-second-device",
        device_id="device-second",
        current_shift={},
    )
    service_b = AdmissionService(context_b, repository)
    controller_b = AdmissionController(service_b)
    detected_a = controller_a.refresh_shift()
    detected_b = controller_b.refresh_shift()
    assert detected_a["id"] == detected_b["id"] == 7
    result = controller_a.change_shift(
        {"tipo_turno": "8AM_8PM", "representative": "PRUEBA"}
    )
    assert result.ok
    assert controller_b.refresh_shift()["id"] == 8
    summary = controller_a.refresh_shift_summary()
    assert summary["total"] == 0
    assert context_a.session_id == "session-existing"
    assert context_b.session_id == "session-second-device"


def _wait_history(app, dialog):
    for _ in range(100):
        app.processEvents()
        if dialog._worker and dialog._worker.isFinished():
            break
        QTest.qWait(5)
    app.processEvents()


def test_phase6_history_finishes_with_results_empty_search_and_pagination():
    app = QApplication.instance() or QApplication([])
    _, context, repository, _, controller = build_stack()
    repository.backend.rows = [
        {
            "id": index,
            "nombre": f"PACIENTE {index}",
            "cedula": f"001000{index:05d}",
            "nss": f"000{index:07d}",
            "ars": "SIN SEGURO" if index == 1 else "ARS PRUEBA",
            "fecha": "05-08-2026",
            "hora": "08:00",
            "hoja": "GENERAL",
        }
        for index in range(1, 106)
    ]
    dialog = AdmissionHistoryDialog(controller)
    _wait_history(app, dialog)
    assert dialog.table.rowCount() == 100
    assert dialog.next_button.isEnabled()
    dialog.next_page()
    _wait_history(app, dialog)
    assert dialog.table.rowCount() == 5
    dialog.search_edit.setText("NO EXISTE")
    dialog.load_page(reset=True)
    _wait_history(app, dialog)
    assert dialog.table.rowCount() == 0
    assert "No se encontraron" in dialog.status_label.text()
    dialog.close()


def test_phase6_uninsured_filter_permissions_and_error_terminal_state():
    app = QApplication.instance() or QApplication([])
    _, context, repository, _, controller = build_stack()
    repository.backend.rows = [
        {"id": 1, "nombre": "SIN PLAN", "ars": "SIN SEGURO"},
        {"id": 2, "nombre": "CON PLAN", "ars": "ARS PRUEBA"},
    ]
    dialog = AdmissionHistoryDialog(controller, uninsured=True)
    _wait_history(app, dialog)
    assert dialog.table.rowCount() == 1
    dialog.table.selectRow(0)
    assert dialog.cancel_button.isEnabled()
    assert not dialog.edit_button.isEnabled()
    dialog.close()

    repository.backend.list_history = lambda **_filters: (_ for _ in ()).throw(RuntimeError("fallo controlado"))
    error_dialog = AdmissionHistoryDialog(controller)
    _wait_history(app, error_dialog)
    assert "No fue posible" in error_dialog.status_label.text()
    assert not error_dialog.retry_button.isHidden()
    assert error_dialog.search_button.isEnabled()
    error_dialog.close()


def test_phase7_dynamic_document_open_print_preview_and_pending_contracts():
    _, context, repository, _, controller = build_stack()
    documents = AdmissionDocumentService(
        repository,
        {"print_copies_hoja": 2, "print_auto_hoja": True, "print_behavior_hoja": "Imprimir y abrir PDF"},
    )
    controller.attach_document_service(documents)
    opened = documents.open(41)
    assert opened.ok and Path(opened.path).is_file()
    previewed = documents.preview(42)
    assert previewed.ok and previewed.action == "PREVIEW"
    printed = documents.print(43)
    assert printed.ok
    assert repository.backend.print_states[43][0] == "ENVIADO_A_IMPRESORA"
    assert ("print", printed.path, 2) in repository.backend.document_calls
    repository.backend.update_print_state(44, "PENDIENTE")
    assert documents.pending()[0]["atencion_id"] == 44
    retried = documents.retry(44)
    assert retried.ok
    for path in {opened.path, previewed.path, printed.path, retried.path}:
        Path(path).unlink(missing_ok=True)


def test_phase7_controller_register_output_is_single_and_nonpersistent():
    _, context, repository, service, _ = build_stack()
    documents = AdmissionDocumentService(
        repository,
        {"print_auto_hoja": False, "open_sheet_after_register": False},
    )
    controller = AdmissionController(service, document_service=documents)
    result = controller.register(valid_input())
    assert result.ok
    assert not repository.backend.document_calls


def test_phase8_report_preferences_excel_and_config_dialog_contracts():
    app = QApplication.instance() or QApplication([])
    _, context, repository, _, _ = build_stack()
    repository.backend.rows = [{"id": 1}]
    report = AdmissionReportDialog(repository)
    report.generate()
    assert report.summary["total_general"] == 1
    assert report.table.rowCount() >= 4
    assert report.pdf_button.isEnabled()
    preferences = AdmissionPreferencesDialog(repository)
    preferences.auto_print.setChecked(False)
    preferences.history_rows.setValue(75)
    preferences.save()
    assert repository.backend.preferences["print_auto_hoja"] is False
    assert repository.backend.preferences["history_page_size"] == 75
    configuration = AdmissionInternalConfigDialog(repository, context)
    assert configuration.tabs.count() == 4
    assert repository.open_current_excel().endswith(".xlsx")
    report.close()
    preferences.close()
    configuration.close()
    for name in ("admision_report_test.pdf", "admision_report_test.xlsx", "admision_current_test.xlsx"):
        (Path(tempfile.gettempdir()) / name).unlink(missing_ok=True)


def test_phase9_source_loader_does_not_create_tk_app_or_second_session():
    root = Path(tempfile.mkdtemp(prefix="admission_native_loader_"))
    source = root / "facturacion_tabs.py"
    source.write_text(
        "created = 0\n"
        "class Session:\n"
        "    session_id = 'shared-session'\n"
        "def load_session_context():\n"
        "    return Session()\n"
        "ADMISSION_SESSION = load_session_context()\n"
        "class App:\n"
        "    def __init__(self):\n"
        "        global created\n"
        "        created += 1\n",
        encoding="utf-8",
    )

    class SourceController(AdmissionModuleController):
        @property
        def source(self):
            return source

    sys.modules.pop("_hospital_native_admission_backend", None)
    controller = SourceController(
        app_dir=root,
        bundle_dir=root,
        user_context={"username": "tester", "role": "administrador"},
        session_id="shared-session",
    )
    module = controller.load_source_module()
    assert module.created == 0
    assert controller._embedded_app is None
    assert module.ADMISSION_SESSION.session_id == "shared-session"
    sys.modules.pop("_hospital_native_admission_backend", None)
    shutil.rmtree(root, ignore_errors=True)


def test_phase9_ten_navigation_cycles_keep_focus_writing_and_single_qapplication():
    app = QApplication.instance() or QApplication([])
    _, context, _, _, controller = build_stack()
    widget = AdmissionWidget(context, controller)
    for index in range(10):
        widget.show()
        app.processEvents()
        widget.name_edit.setFocus()
        widget.name_edit.clear()
        QTest.keyClicks(widget.name_edit, f"CICLO {index}")
        assert widget.name_edit.text() == f"CICLO {index}"
        widget.hide()
        app.processEvents()
    assert QApplication.instance() is app
    assert context.session_id == "session-existing"
    widget.close()
