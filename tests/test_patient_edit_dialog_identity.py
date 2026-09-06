"""Exercise actual editor callbacks with isolated patient storage."""

import ast
import inspect
import sqlite3
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as v15
from tests.test_patient_edit_all_roles_v110 import (
    _create_local_patient_database,
    _database_manager_for,
)


def editor_callback(name, **closure):
    """Compile a nested callback unmodified, retaining coverage source lines."""
    owner = next(
        value
        for value in vars(v15).values()
        if isinstance(value, type) and "_abrir_edicion_paciente" in vars(value)
    )
    source, line = inspect.getsourcelines(owner._abrir_edicion_paciente)
    tree = ast.parse(textwrap.dedent("".join(source)))
    callback = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    ast.increment_lineno(callback, line - 1)
    namespace = dict(vars(v15), **closure)
    exec(
        compile(ast.Module(body=[callback], type_ignores=[]), v15.__file__, "exec"),
        namespace,
    )
    return namespace[name]


def load_form(state):
    return editor_callback(
        "_llenar_formulario_paciente",
        original_identidad=state,
        campos={},
        estado_var=Mock(),
        btn_eliminar=Mock(),
        self=SimpleNamespace(set_status=Mock()),
    )


@pytest.mark.parametrize(
    ("record", "original", "expected"),
    [
        ({"id": 73, "paciente_id": 73}, "A:73", "P:73"),
        ({"id": 10, "paciente_id": 1}, "A:10", "P:1"),
        ({"id": 10}, "A:10", "A:10"),
    ],
)
def test_loaded_master_keeps_patient_identity(record, original, expected):
    state = {}
    load_form(state)(record, original)
    assert state["valor"] == expected


@pytest.mark.parametrize("collision", [False, True])
def test_save_master_without_attention_does_not_use_another_attention(
    tmp_path, collision
):
    path = tmp_path / "patients.db"
    _create_local_patient_database(path)
    with sqlite3.connect(path) as con:
        con.execute(
            """INSERT INTO pacientes(id,nombre,global_patient_id,server_revision)
               VALUES(73,'SINTETICO','33333333-3333-4333-8333-333333333333',1)"""
        )
        if collision:
            con.execute("UPDATE atenciones SET id=73 WHERE id=10")
        old_attentions = con.execute("SELECT * FROM atenciones").fetchall()
        other_patient = con.execute("SELECT * FROM pacientes WHERE id=1").fetchone()
    database, _audit = _database_manager_for(path)
    state = {}
    load_form(state)(database.buscar_paciente_para_edicion("P:73"), "A:73")
    fields = {
        key: Mock(get=Mock(return_value=value))
        for key, value in {
            "Nombre": "SINTETICO CORREGIDO",
            "Cédula": "",
            "Teléfono": "",
            "NSS": "",
            "Dirección": "DIRECCION NUEVA",
            "Nacionalidad": "DOMINICANA",
            "Aseguradora (ARS)": "SIN SEGURO",
        }.items()
    }
    messages = Mock()
    editor_callback(
        "guardar_edicion",
        original_identidad=state,
        campos=fields,
        self=Mock(db=database),
        estado_var=Mock(),
        messagebox=messages,
    )()
    messages.showwarning.assert_not_called()
    messages.showerror.assert_not_called()
    assert messages.showinfo.call_args.args[0] == "Guardado"
    with sqlite3.connect(path) as con:
        assert con.execute("SELECT nombre FROM pacientes WHERE id=73").fetchone() == (
            "SINTETICO CORREGIDO",
        )
        assert (
            con.execute("SELECT * FROM pacientes WHERE id=1").fetchone()
            == other_patient
        )
        assert con.execute("SELECT * FROM atenciones").fetchall() == old_attentions
        assert con.execute("SELECT COUNT(*) FROM pacientes").fetchone()[0] == 2


def test_selection_of_deleted_master_does_not_fill_form():
    fill = Mock()
    editor_callback(
        "seleccionar_resultado_paciente",
        resultados_tree=Mock(selection=Mock(return_value=("P:73",))),
        self=SimpleNamespace(
            db=Mock(buscar_paciente_para_edicion=Mock(return_value=None))
        ),
        _llenar_formulario_paciente=fill,
    )()
    fill.assert_not_called()


def test_search_with_missing_master_does_not_load_attention_snapshot():
    fill = Mock()
    status = Mock()
    editor_callback(
        "_buscar",
        resultados_tree=Mock(get_children=Mock(return_value=[])),
        self=Mock(
            db=Mock(
                buscar_pacientes_avanzado=Mock(
                    return_value=[{"id": 10, "paciente_id": 1}]
                ),
                buscar_paciente_para_edicion=Mock(return_value=None),
            )
        ),
        ident="123456",
        estado_var=status,
        messagebox=Mock(),
        _llenar_formulario_paciente=fill,
    )()
    fill.assert_not_called()
    assert "ya no está disponible" in status.set.call_args.args[0]


@pytest.mark.parametrize("selection", [(), ("I001",), ("P:73",)])
def test_selection_uses_master_row_identity(selection):
    tree = Mock(selection=Mock(return_value=selection))
    data = {"paciente_id": 73}
    database = Mock(buscar_paciente_para_edicion=Mock(return_value=data))
    fill = Mock()
    editor_callback(
        "seleccionar_resultado_paciente",
        resultados_tree=tree,
        self=SimpleNamespace(db=database),
        _llenar_formulario_paciente=fill,
    )()
    if selection == ("P:73",):
        database.buscar_paciente_para_edicion.assert_called_once_with("P:73")
        fill.assert_called_once_with(data, "P:73")
    else:
        database.buscar_paciente_para_edicion.assert_not_called()


@pytest.mark.parametrize("attention_search", [False, True])
def test_search_loads_current_master_not_historical_fields(attention_search):
    historical = {"id": 10, "paciente_id": 1, "nombre": "NOMBRE HISTORICO"}
    master = {"id": 10, "paciente_id": 1, "nombre": "NOMBRE ACTUAL"}
    tree = Mock(get_children=Mock(return_value=[]))
    database = Mock(
        buscar_pacientes_avanzado=Mock(
            return_value=[historical] if attention_search else []
        ),
        buscar_paciente_para_edicion=Mock(return_value=master),
    )
    fill = Mock()
    messages = Mock()
    editor_callback(
        "_buscar",
        resultados_tree=tree,
        self=Mock(db=database),
        ident="123456",
        estado_var=Mock(),
        messagebox=messages,
        _llenar_formulario_paciente=fill,
    )()
    messages.showerror.assert_not_called()
    assert tree.insert.call_args.kwargs["iid"] == "P:1"
    fill.assert_called_once_with(master, "P:1")
