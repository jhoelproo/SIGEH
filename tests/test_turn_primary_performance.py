from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sqlite3
import statistics
import sys
import threading
import time
import tkinter as tk
from types import SimpleNamespace

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "admission_source"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import facturacion_tabs as admission  # noqa: E402 - source path bootstrap
import admission_v15_adapter  # noqa: E402 - source path bootstrap


class _Entry:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def delete(self, *_args):
        self.value = ""

    def insert(self, _index, value):
        self.value = str(value or "")


def _patient_database(path: Path):
    with sqlite3.connect(path) as con:
        con.executescript(
            """
            CREATE TABLE pacientes(
              id INTEGER PRIMARY KEY,nombre TEXT,cedula TEXT,telefono TEXT,
              direccion TEXT,nacionalidad TEXT,ars TEXT,nss TEXT,
              nss_clean TEXT,estado TEXT,updated_at TEXT
            );
            CREATE INDEX idx_pacientes_nss_clean ON pacientes(nss_clean);
            CREATE TABLE paciente_identificadores(
              id INTEGER PRIMARY KEY,paciente_id INTEGER,tipo TEXT,
              valor_normalizado TEXT,activo INTEGER,conflicto INTEGER
            );
            CREATE UNIQUE INDEX uq_cedula_activa
              ON paciente_identificadores(tipo,valor_normalizado)
              WHERE tipo='CEDULA' AND activo=1 AND conflicto=0;
            INSERT INTO pacientes VALUES(
              1,'PACIENTE PRUEBA','00100000001','8095550000','CALLE 1',
              'DOMINICANA','HUMANO','123456789','123456789','ACTIVO',
              '2026-08-20 10:00:00'
            );
            INSERT INTO paciente_identificadores
              VALUES(1,1,'CEDULA','00100000001',1,0);
            """
        )


def _contrast_ratio(foreground: str, background: str) -> float:
    def luminance(color: str) -> float:
        channels = [int(color[index:index + 2], 16) / 255.0 for index in (1, 3, 5)]
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first = luminance(foreground)
    second = luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def test_three_canonical_shifts_are_available_every_day():
    expected = ("8AM_8AM", "8AM_8PM", "8PM_8AM")
    monday = date(2026, 8, 17)
    for offset in range(7):
        options = admission.available_admission_shifts(monday + timedelta(days=offset))
        assert tuple(code for code, _label in options) == expected


def test_local_patient_lookup_uses_existing_indexes_and_stays_below_200ms(tmp_path):
    path = tmp_path / "patients.db"
    _patient_database(path)
    manager = admission.DatabaseManager.__new__(admission.DatabaseManager)
    manager.db_name = str(path)

    click_times = []
    tab_times = []
    for target in (click_times, tab_times):
        for _ in range(20):
            started = time.perf_counter()
            patient = manager.buscar_paciente("001-0000000-1")
            target.append((time.perf_counter() - started) * 1000.0)
            assert patient["nombre"] == "PACIENTE PRUEBA"
    click_times.sort()
    tab_times.sort()
    assert click_times[18] <= 200
    assert tab_times[18] <= 200
    assert abs(statistics.mean(click_times) - statistics.mean(tab_times)) < 50

    with sqlite3.connect(path) as con:
        plan = " ".join(
            str(row)
            for row in con.execute(
                """EXPLAIN QUERY PLAN
                   SELECT p.* FROM pacientes p
                   JOIN paciente_identificadores i ON i.paciente_id=p.id
                   WHERE i.tipo='CEDULA' AND i.valor_normalizado=?
                     AND i.activo=1 AND i.conflicto=0 LIMIT 1""",
                ("00100000001",),
            )
        )
    assert "uq_cedula_activa" in plan
    assert "SCAN i" not in plan


def test_click_and_tab_call_the_same_autofill_pipeline(tmp_path):
    path = tmp_path / "patients.db"
    _patient_database(path)
    manager = admission.DatabaseManager.__new__(admission.DatabaseManager)
    manager.db_name = str(path)
    app = admission.App.__new__(admission.App)
    app.db = manager
    app._suspend_autocomplete = False
    app._last_autofill_identity = None
    app._last_autofill_at = 0.0
    app._autofill_cloud_pending = set()
    app._field_focus_started_at = time.perf_counter()
    app.entry_cedula = _Entry("00100000001")
    app.entry_nss = _Entry()
    app.entry_nombre = _Entry()
    app.entry_telefono = _Entry()
    app.entry_direccion = _Entry()
    app.entry_nacionalidad = _Entry()
    app.entry_ars = _Entry()
    app._actualizar_deteccion_seguro = lambda *_args: None
    app._schedule_cloud_patient_lookup = lambda *_args: None

    assert app.auto_completar(input_method="CLICK") is not None
    assert app.entry_nombre.get() == "PACIENTE PRUEBA"
    app._last_autofill_identity = None
    assert app.auto_completar(input_method="TAB") is not None
    assert app.entry_nss.get() == "123456789"


def test_turn_dialog_failure_never_leaves_button_disabled(monkeypatch):
    app = admission.App.__new__(admission.App)
    app._turn_change_in_progress = False
    app._turn_change_committing = False
    states = []
    app.can_change_admission_turn = lambda: (True, "ALLOWED", "")
    app._set_turn_change_controls_enabled = states.append
    app._dialogo_turno = lambda: (_ for _ in ()).throw(RuntimeError("dialog error"))
    monkeypatch.setattr(admission.messagebox, "askyesno", lambda *_a, **_k: True)
    monkeypatch.setattr(admission.messagebox, "showerror", lambda *_a, **_k: None)
    assert app.request_change_admission_turn() == "break"
    assert app._turn_change_in_progress is False
    assert states == [False, True]


def test_turn_button_becomes_enabled_immediately_after_primary_transfer():
    class Runtime:
        offline = False
        device_id = "PC-2"

        @staticmethod
        def state():
            return {
                "role": "PRIMARY",
                "primary_device_id": "PC-2",
                "user_matches_operational": True,
            }

        @staticmethod
        def require_primary_transition():
            return True

    class Button:
        state = ""

        def configure(self, *, state):
            self.state = state

    app = admission.App.__new__(admission.App)
    app._turn_change_in_progress = False
    app._turn_change_committing = False
    app._primary_transfer_in_progress = False
    app.session_context = SimpleNamespace(role="administrador")
    app.db = SimpleNamespace(_runtime=Runtime())
    app.change_turn_button = Button()
    app._refresh_actions_menu_state = lambda: None
    app._set_turn_change_controls_enabled(True)
    assert app.change_turn_button.state == "normal"


def test_turn_entrypoints_share_one_canonical_action():
    source = Path(admission.__file__).read_text(encoding="utf-8")
    assert source.count("command=self.request_change_admission_turn") >= 2
    assert "self.root.bind('<F5>', self.request_change_admission_turn)" in source
    assert "def request_change_admission_turn" in source
    assert "command=self.request_transfer_admission_primary" in source
    assert "def request_transfer_admission_primary" in source
    assert "def reiniciar_datos_excel" in source


def test_packaged_v15_source_uses_the_same_primary_and_turn_actions():
    source_path = (
        Path(admission_v15_adapter.DEFAULT_V15_ROOT)
        / "facturacion_tabs_pyside6.py"
    )
    source = source_path.read_text(encoding="utf-8")
    assert "command=self.reiniciar_datos_excel" not in source
    assert "lambda e: self.reiniciar_datos_excel" not in source
    assert source.count("command=self.request_change_admission_turn") >= 2
    assert "self.root.bind('<F5>', self.request_change_admission_turn)" in source
    assert "def request_transfer_admission_primary" in source
    assert 'text="CONTROL DE SESIÓN PRINCIPAL"' in source
    assert "QMenu::item:disabled" in source
    assert "Los tres turnos canónicos están disponibles todos los días" in source


def test_existing_admin_pin_uses_salted_pbkdf2_and_constant_time_compare(tmp_path):
    security = admission.AdminSecurity(
        tmp_path / "security.json", tmp_path / "audit.jsonl"
    )
    security.setup("829461", actor="admin")
    stored = (tmp_path / "security.json").read_text(encoding="utf-8")
    assert "829461" not in stored
    assert "salt" in stored and "password_hash" in stored
    assert security.verify(
        "829461", actor="admin", action="TRANSFERIR_ACCESO_PRINCIPAL"
    )
    assert not security.verify(
        "829462", actor="admin", action="TRANSFERIR_ACCESO_PRINCIPAL"
    )


def test_remote_primary_transfer_uses_current_admin_without_second_pin(monkeypatch):
    calls = []

    class Runtime:
        offline = False
        device_id = "PC-2"

        @staticmethod
        def state():
            return {
                "role": "SECONDARY",
                "primary_device_id": "PC-1",
                "active_username": "admin",
                "active_user_id": "USER-1",
                "turn_id": 350,
                "generation": 4,
                "operational_session_id": "SESSION-1",
                "operational_revision": 9,
            }

        @staticmethod
        def list_primary_transfer_candidates():
            return [
                {
                    "device_id": "PC-1",
                    "device_name": "Admisión 1",
                    "station_role": "PRIMARY",
                    "login_session_id": "LOGIN-1",
                },
                {
                    "device_id": "PC-2",
                    "device_name": "Admisión 2",
                    "station_role": "SECONDARY",
                    "login_session_id": "LOGIN-2",
                },
            ]

        @staticmethod
        def force_transfer_admission_primary(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                primary_device_id="PC-2",
                turn_id=350,
                generation=4,
                active_user_id="USER-1",
            )

    app = admission.App.__new__(admission.App)
    app.root = object()
    app.db = SimpleNamespace(_runtime=Runtime())
    app.session_context = SimpleNamespace(role="administrador")
    app._primary_transfer_in_progress = False
    app._solicitar_autorizacion_admin = lambda *_a, **_k: pytest.fail(
        "La transferencia no debe pedir una segunda credencial"
    )
    app._refresh_actions_menu_state = lambda: None
    app._set_turn_change_controls_enabled = lambda _enabled: None
    app.set_status = lambda *_a, **_k: None

    def execute(_message, function, al_terminar=None, al_error=None):
        try:
            result = function()
        except Exception as exc:
            al_error(exc)
        else:
            al_terminar(result)

    app._ejecutar_en_segundo_plano = execute
    monkeypatch.setattr(admission.messagebox, "showinfo", lambda *_a, **_k: None)
    monkeypatch.setattr(admission.messagebox, "showwarning", lambda *_a, **_k: None)
    monkeypatch.setattr(admission.messagebox, "showerror", lambda *_a, **_k: None)
    monkeypatch.setattr(admission.messagebox, "askyesno", lambda *_a, **_k: True)
    monkeypatch.setattr(admission.simpledialog, "askstring", lambda *_a, **_k: "Relevo técnico")
    app.request_transfer_admission_primary()
    assert calls == [
        {
            "target_device_id": "PC-2",
            "target_login_session_id": "LOGIN-2",
            "expected_operational_revision": 9,
            "reason": "Relevo técnico",
        }
    ]


def test_runtime_primary_candidate_adapter_enforces_admin_and_active_session():
    from admission_hybrid import AdmissionWriteBlocked, StationRole

    runtime = admission_v15_adapter._HybridAdmissionRuntime.__new__(
        admission_v15_adapter._HybridAdmissionRuntime
    )
    runtime.host = SimpleNamespace(
        user={"id": 7, "username": "operator", "role": "auxiliar"},
        device_id="PC-2",
        session_id="LOGIN-2",
    )
    runtime.StationRole = StationRole
    runtime.attachment = None
    runtime.session_service = SimpleNamespace()
    with pytest.raises(AdmissionWriteBlocked):
        runtime.list_primary_transfer_candidates()

    runtime.host.user = {"id": 7, "username": "admin", "role": "administrador"}
    with pytest.raises(AdmissionWriteBlocked):
        runtime.list_primary_transfer_candidates()

    runtime.attachment = SimpleNamespace(
        operational_session=SimpleNamespace(operational_session_id="OP-1")
    )
    runtime.session_service = SimpleNamespace(
        list_primary_transfer_candidates=lambda **kwargs: [kwargs]
    )
    assert runtime.list_primary_transfer_candidates() == [
        {"operational_session_id": "OP-1"}
    ]


def test_runtime_primary_transfer_adapter_adopts_the_committed_lease():
    from admission_hybrid import StationRole

    calls = []
    session = SimpleNamespace(
        operational_session_id="OP-1", operational_revision=9
    )
    changed = SimpleNamespace(
        primary_device_id="PC-2",
        primary_login_session_id="LOGIN-2",
        turn_id=350,
        generation=4,
        active_user_id="USER-1",
        lease_generation=3,
        operational_revision=10,
    )
    runtime = admission_v15_adapter._HybridAdmissionRuntime.__new__(
        admission_v15_adapter._HybridAdmissionRuntime
    )
    runtime.host = SimpleNamespace(
        user={"id": 7, "username": "admin", "role": "administrador"},
        device_id="PC-2",
        session_id="LOGIN-2",
    )
    runtime.StationRole = StationRole
    runtime.attachment = SimpleNamespace(operational_session=session)
    runtime._operational_state = None
    runtime.session_service = SimpleNamespace(
        force_transfer_admission_primary=lambda **kwargs: (
            calls.append(kwargs) or changed
        )
    )

    assert runtime.force_transfer_primary(
        target_device_id="PC-2",
        target_login_session_id="LOGIN-2",
        expected_operational_revision=None,
        reason="Mantenimiento",
    ) is changed
    assert runtime.attachment.role is StationRole.PRIMARY
    assert runtime.status_message == "Conectado · Principal · Sincronizado"
    assert runtime.offline is False and runtime.offline_lease_valid is True
    assert calls[0]["expected_operational_revision"] == 9


@pytest.mark.parametrize("theme", ["oscuro", "claro"])
def test_actions_menu_uses_legible_theme_colors(theme):
    root = tk.Tk()
    root.withdraw()
    try:
        app = admission.App.__new__(admission.App)
        app.app_settings = {
            "theme": theme,
            "high_contrast": False,
            "accent_color": "Azul hospitalario",
        }
        app.actions_menu = tk.Menu(root, tearoff=0)
        app.actions_menu.add_command(label="Cambiar turno")
        app.actions_menu.entryconfigure("Cambiar turno", state="disabled")
        app.menu_contextual = None
        palette = app._paleta_visual_actual()
        app._configurar_colores_menu(palette, 11)
        assert str(app.actions_menu.cget("background")) == palette["card"]
        assert str(app.actions_menu.cget("foreground")) == palette["text"]
        assert str(app.actions_menu.cget("disabledforeground")) == palette["muted"]
        assert str(app.actions_menu.cget("activebackground")) == palette["selected_bg"]
        assert str(app.actions_menu.cget("activeforeground")) == palette["selected_fg"]
        assert palette["text"].lower() != palette["card"].lower()
        assert palette["muted"].lower() != palette["card"].lower()
        assert _contrast_ratio(palette["text"], palette["card"]) >= 4.5
        assert _contrast_ratio(palette["muted"], palette["card"]) >= 3.0
        assert _contrast_ratio(
            palette["selected_fg"], palette["selected_bg"]
        ) >= 3.0
        assert str(app.actions_menu.entrycget(0, "foreground")) == palette["text"]
        assert str(app.actions_menu.entrycget(0, "activeforeground")) == palette["selected_fg"]
    finally:
        root.destroy()


def test_revoked_primary_is_not_automatically_reattached():
    source = Path(admission.__file__).resolve().parents[1] / "admission_v15_adapter.py"
    text = source.read_text(encoding="utf-8")
    assert 'state.invalidated_reason == "PRIMARY_TRANSFERRED_ADMINISTRATIVELY"' in text
    assert "return state" in text


def test_secondary_shutdown_never_waits_for_sync_lock_or_network():
    runtime = admission_v15_adapter._HybridAdmissionRuntime.__new__(
        admission_v15_adapter._HybridAdmissionRuntime
    )
    runtime._shutdown_started = False
    runtime._lock = threading.RLock()
    runtime._operational_state = None
    runtime.StationRole = SimpleNamespace(SECONDARY="SECONDARY")
    runtime.attachment = SimpleNamespace(
        operational_session=SimpleNamespace(operational_session_id="OP-1"),
        role="SECONDARY",
    )
    runtime.host = SimpleNamespace(device_id="PC-2")
    detached = threading.Event()
    runtime.session_service = SimpleNamespace(
        detach_device=lambda **_kwargs: detached.set()
    )
    runtime.logger = SimpleNamespace(exception=lambda *_a, **_k: None)

    runtime._lock.acquire()
    try:
        started = time.perf_counter()
        runtime.shutdown()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
    finally:
        runtime._lock.release()

    assert elapsed_ms < 100
    assert detached.wait(timeout=1.0)


def test_primary_transfer_and_representative_correction_are_separate_flows():
    source = Path(admission.__file__).read_text(encoding="utf-8")
    assert 'text="CONTROL DE SESIÓN PRINCIPAL"' in source
    assert 'notebook.add(tab_primary, text="Sesión principal")' not in source
    assert "MAIN_APP_GATEWAY.authorize_shift_change" in source
    transfer_block = source.split(
        "def request_transfer_admission_primary", 1
    )[1].split("def request_force_primary_transfer", 1)[0]
    assert "authorize_shift_change" not in transfer_block
    assert "request_primary_transfer" in transfer_block
    coordinator = (SOURCE.parent / "primary_transfer_ui.py").read_text(encoding="utf-8")
    assert "force_transfer_admission_primary" in coordinator
    assert "authorize_shift_change" not in coordinator


@pytest.mark.parametrize(
    ("screen", "expected"),
    [
        ((1920, 1080), (1536, 864)),
        ((1600, 900), (1280, 720)),
        ((1366, 768), (1093, 614)),
    ],
)
def test_internal_configuration_uses_responsive_initial_size(screen, expected):
    assert admission.App._calcular_tamano_configuracion(*screen) == expected
