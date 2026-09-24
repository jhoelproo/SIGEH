"""Actual dialog callback, stopped at the backup boundary, without central writes."""

import ast
import inspect
import textwrap
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_v15_adapter import load_v15_application_module


def dialog_callback(v15, callback_name="_aplicar_cambio", **closure):
    from admission_handoff_ui import HandoffSubmission

    closure.setdefault("aplicando", HandoffSubmission())
    source, line = inspect.getsourcelines(v15.App._dialogo_turno)
    tree = ast.parse(textwrap.dedent("".join(source)))
    callback = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == callback_name
    )
    ast.increment_lineno(callback, line - 1)
    namespace = dict(vars(v15), **closure)
    exec(
        compile(ast.Module(body=[callback], type_ignores=[]), v15.__file__, "exec"),
        namespace,
    )
    return namespace[callback_name]


@pytest.mark.parametrize(
    "moment",
    [
        "2026-09-06T19:59:00",
        "2026-09-06T20:00:00",
        "2026-09-06T20:01:00",
        "2026-09-07T00:00:00",
    ],
)
@pytest.mark.parametrize("administrative", [False, True])
@pytest.mark.parametrize("admin", [False, True])
@pytest.mark.parametrize("handover", [False, True])
def test_dialog_operation_is_explicit_and_independent_of_clock(
    moment, administrative, admin, handover
):
    v15 = load_v15_application_module()
    now = datetime.fromisoformat(moment)
    runtime = SimpleNamespace(
        state=lambda: {"active_username": "REPRESENTANTE ANTERIOR"},
        is_primary_shift_handover=lambda: handover,
    )
    backup = Mock(side_effect=RuntimeError("STOP BEFORE CENTRAL COMMAND"))
    database = SimpleNamespace(
        _runtime=runtime,
        backup_manager=SimpleNamespace(create=backup),
        perform_explicit_turn_handoff=Mock(),
    )
    window = SimpleNamespace(
        db=database,
        session_context=SimpleNamespace(
            role=v15.ROLE_ADMIN if admin else "auxiliar",
            display_name="REPRESENTANTE NUEVO",
        ),
    )
    messages = Mock(askyesno=Mock(return_value=True))
    callback = dialog_callback(
        v15,
        self=window,
        win=object(),
        fecha_base=now.date(),
        combo_turno=Mock(get=Mock(return_value="8AM_8PM")),
        normalizar_turno_desde_combo=lambda value: value,
        administrative_var=Mock(get=Mock(return_value=administrative)),
        datetime=Mock(now=Mock(return_value=now)),
        cargar_turno_config=Mock(return_value={"turn_id": 7}),
        capture_outgoing_turn_context=Mock(return_value={}),
        messagebox=messages,
        simpledialog=Mock(askstring=Mock(return_value="SYNTHETIC")),
        APP_LOG=Mock(),
    )
    callback()
    assert messages.askyesno.call_count == int(administrative and admin)
    assert backup.call_count == int(
        (administrative and admin) or (not administrative and handover)
    )
    database.perform_explicit_turn_handoff.assert_not_called()


@pytest.mark.parametrize("role", ["admin", "auxiliar"])
def test_real_qt_dialog_operation_preview_and_permissions(monkeypatch, role, tmp_path):
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QCheckBox, QLabel, QPushButton

    v15 = load_v15_application_module()
    qt = QApplication.instance() or QApplication([])
    from PySide6.QtGui import QFont, QFontDatabase

    QFontDatabase.addApplicationFont("C:/Windows/Fonts/arial.ttf")
    qt.setFont(QFont("Arial", 10))
    dialog = v15.Toplevel()
    dialog.resize(780, 580)
    role = v15.ROLE_ADMIN if role == "admin" else "auxiliar"
    runtime = SimpleNamespace(
        state=lambda: {"active_username": "REPRESENTANTE ANTERIOR"},
        is_primary_shift_handover=lambda: True,
    )
    window = SimpleNamespace(
        db=SimpleNamespace(_runtime=runtime),
        session_context=SimpleNamespace(role=role, display_name="REPRESENTANTE NUEVO"),
        app_settings={},
        _snapshot_operacional_integrado=lambda: {"role": "PRIMARY"},
        _crear_toplevel_estable=lambda *_: dialog,
        _bind_esc_cerrar=Mock(),
        _crear_header_ventana=Mock(),
        _set_turn_change_controls_enabled=Mock(),
    )
    try:
        v15.App._dialogo_turno(window)
        dialog.show()
        qt.processEvents()
        controls = dialog.findChildren(QCheckBox)
        assert len(controls) == int(role == v15.ROLE_ADMIN)

        def labels():
            return [label.text() for label in dialog.findChildren(QLabel)]

        assert "REPRESENTANTE NUEVO" in labels()
        if controls:
            (checkbox,) = controls
            assert not checkbox.isChecked()
            checkbox.click()
            qt.processEvents()
            assert "REPRESENTANTE ANTERIOR" in labels()
            assert any("Corrección administrativa:" in text for text in labels())
            apply = next(
                button
                for button in dialog.findChildren(QPushButton)
                if button.text() == "Aplicar"
            )
            assert not checkbox.geometry().intersects(apply.geometry())
            checkbox.click()
            assert "REPRESENTANTE NUEVO" in labels()
        assert dialog.grab().save(str(tmp_path / "dialog.png"))
    finally:
        dialog.close()
        qt.processEvents()


def test_confirmed_handoff_after_nominal_end_uses_central_configuration():
    from admission_sheet_state import ConfirmedTurnConfig

    v15 = load_v15_application_module()
    now = datetime(2026, 9, 6, 20, 1)
    runtime = SimpleNamespace(
        state=lambda: {"active_username": "REPRESENTANTE ANTERIOR"},
        is_primary_shift_handover=lambda: True,
    )
    transition = SimpleNamespace(
        committed=True,
        operational_session=SimpleNamespace(turn_id=8),
        transition_id="synthetic-transition",
        old_turn_id=7,
    )
    config = ConfirmedTurnConfig(
        representante="REPRESENTANTE NUEVO",
        turno_codigo="8AM_8PM",
        fecha_base=now.date(),
        inicio_real_dt=now,
        turn_id=8,
    )
    window = Mock(
        db=Mock(
            _runtime=runtime,
            perform_explicit_turn_handoff=Mock(return_value=transition),
        ),
        session_context=SimpleNamespace(
            role="auxiliar",
            display_name="REPRESENTANTE NUEVO",
            username="new",
            session_id="new-session",
        ),
        _generation_turn_config=Mock(return_value=config),
    )
    mirror = Mock(return_value={"turn_id": 7})
    enqueue = Mock()
    schedule = Mock()
    callback = dialog_callback(
        v15,
        self=window,
        win=Mock(),
        fecha_base=now.date(),
        combo_turno=Mock(get=Mock(return_value="8AM_8PM")),
        normalizar_turno_desde_combo=lambda value: value,
        administrative_var=Mock(get=Mock(return_value=False)),
        datetime=Mock(now=Mock(return_value=now)),
        cargar_turno_config=mirror,
        capture_outgoing_turn_context=Mock(return_value={}),
        bind_outgoing_turn_transition=Mock(return_value={}),
        guardar_turno_config=Mock(return_value=True),
        guardar_representante_catalogo=Mock(),
        enqueue_excel_export_job=enqueue,
        enqueue_turn_excel_delivery=Mock(),
        schedule_turn_closure_post_commit=schedule,
        messagebox=Mock(),
        APP_LOG=Mock(),
    )
    assert callback() is True
    mirror.assert_called_once_with(permitir_vencido=True)
    window._generation_turn_config.assert_called_once()
    window.db.obtener_o_crear_turno.assert_called_once_with(
        config, administrative_override=False
    )
    enqueue.assert_called_once_with("synthetic-transition", 8, config)
    assert schedule.call_args.args[-1] == []
    assert not window.db.perform_explicit_turn_handoff.call_args.kwargs[
        "shift_metadata"
    ].get("administrative_override", False)
