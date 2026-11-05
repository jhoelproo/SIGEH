"""Run the existing legacy callbacks at their worker and dialog boundaries."""

import ast
import inspect
import sqlite3
import textwrap
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from admission_v15_adapter import load_v15_application_module, _HybridDatabaseProxy


def nested_callback(method, name, **closure):
    lines, start = inspect.getsourcelines(method)
    tree = ast.parse(textwrap.dedent("".join(lines)))
    node = next(
        n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name
    )
    ast.increment_lineno(node, start - 1)
    namespace = dict(method.__globals__, **closure)
    exec(
        compile(
            ast.Module(body=[node], type_ignores=[]), inspect.getfile(method), "exec"
        ),
        namespace,
    )
    return namespace[name]


def deletion_context():
    module = load_v15_application_module()
    jobs = []
    owner = SimpleNamespace(
        db=SimpleNamespace(eliminar_ars_global=Mock(return_value=3)),
        security=Mock(),
        _admin_authorized_actor={"username": "ADMIN"},
        _actor_actual=Mock(),
        _actualizar_resumen_turno_panel=Mock(),
        _ejecutar_en_segundo_plano=lambda _label, function, **callbacks: jobs.append(
            (function, callbacks)
        ),
    )
    tree = Mock()
    tree.selection.return_value = ("row",)
    tree.item.return_value = ("HUMANO/PRIMERA", 3)
    context = dict(
        self=owner,
        tree_ars=tree,
        ars_originales={"row": "PRIMERA"},
        ars_delete_state={"busy": False},
        info_ars=Mock(),
        win=Mock(winfo_exists=Mock(return_value=True)),
        cargar_ars=Mock(),
        messagebox=Mock(),
    )
    context["messagebox"].askyesno.return_value = True
    callback = nested_callback(
        module.App._abrir_configuracion_interna, "eliminar_ars_sel", **context
    )
    return callback, context, jobs


@pytest.mark.parametrize(
    "outcome", ["success", "failed", "closed_success", "closed_failed"]
)
def test_insurer_delete_stays_responsive_and_releases_busy_state(outcome):
    callback, context, jobs = deletion_context()
    owner = context["self"]
    callback()
    owner.db.eliminar_ars_global.assert_not_called()
    callback()
    assert len(jobs) == 1
    operation, handlers = jobs.pop()
    context["win"].winfo_exists.return_value = not outcome.startswith("closed")
    if outcome.endswith("failed"):
        handlers["al_error"](ValueError("No se guardó"))
    else:
        handlers["al_terminar"](operation())
        owner.db.eliminar_ars_global.assert_called_once_with("PRIMERA")
        owner.security.audit.assert_called_once()
    assert not context["ars_delete_state"]["busy"]
    if outcome == "success":
        context["cargar_ars"].assert_called_once()
        owner._actualizar_resumen_turno_panel.assert_called_once()
    if outcome == "failed":
        context["messagebox"].showerror.assert_called_once()


@pytest.mark.parametrize("selection,confirmed", [((), True), (("row",), False)])
def test_insurer_delete_requires_selection_and_confirmation(selection, confirmed):
    callback, context, jobs = deletion_context()
    context["tree_ars"].selection.return_value = selection
    context["messagebox"].askyesno.return_value = confirmed
    callback()
    assert not jobs
    context["self"].db.eliminar_ars_global.assert_not_called()


def test_current_insurer_counts_keep_canonical_identity_separate_from_display():
    module = load_v15_application_module()
    jobs = []
    owner = SimpleNamespace(
        db=SimpleNamespace(listar_ars_conteo=lambda: [("SENASA CONTRIBUTIVO", 3)]),
        _obtener_catalogo_ars=lambda: {"SENASA CONTRIBUTIVO": []},
        _invalidar_cache_ars=Mock(),
        _ejecutar_en_segundo_plano=lambda _label, function, **callbacks: jobs.append(
            (function, callbacks)
        ),
    )
    tree = Mock()
    tree.get_children.return_value = ("old",)
    tree.insert.return_value = "new"
    originals = {}
    context = dict(
        self=owner,
        tree_ars=tree,
        ars_originales=originals,
        win=Mock(winfo_exists=Mock(return_value=True)),
        info_ars=Mock(),
        messagebox=Mock(),
    )
    callback = nested_callback(
        module.App._abrir_configuracion_interna, "cargar_ars", **context
    )
    callback()
    operation, handlers = jobs.pop()
    handlers["al_terminar"](operation())
    assert originals == {"new": "SENASA CONTRIBUTIVO"}
    owner._invalidar_cache_ars.assert_called_once()
    context["win"].winfo_exists.return_value = False
    handlers["al_terminar"](operation())
    handlers["al_error"](ValueError("synthetic"))
    context["messagebox"].showerror.assert_called_once()


def test_current_insurer_selection_uses_canonical_value():
    module = load_v15_application_module()
    tree = Mock()
    tree.selection.return_value = ("row",)
    tree.item.return_value = ("S.CONTRIBUTIVO", 3)
    selected = Mock()
    callback = nested_callback(
        module.App._abrir_configuracion_interna,
        "seleccionar_ars",
        tree_ars=tree,
        ars_originales={"row": "SENASA CONTRIBUTIVO"},
        ars_actual_var=selected,
    )
    callback()
    selected.set.assert_called_once_with("SENASA CONTRIBUTIVO")


def test_local_history_interval_matches_supported_date_formats_and_current_counts(
    tmp_path,
):
    module = load_v15_application_module()
    path = tmp_path / "history.sqlite"
    with sqlite3.connect(path) as con:
        con.executescript("""
            CREATE TABLE atenciones(id INTEGER,fecha TEXT,hora TEXT,nombre TEXT,hoja TEXT,ars TEXT,
                nss TEXT,cedula TEXT,edad_num INT,unidad TEXT,tipo_atencion TEXT,estado TEXT);
            CREATE TABLE pacientes(ars TEXT,nss TEXT);
            INSERT INTO pacientes VALUES('MAPFRE/PALIC','12345678');
        """)
        for identity, day in enumerate(
            ["02/11/2026", "2026-11-03", "04-11-2026", "2026-11-05"], 1
        ):
            con.execute(
                "INSERT INTO atenciones VALUES(?,?,'08:00','QA','GENERAL','HUMANO','12345','',20,'AÑOS','EMERGENCIA','ACTIVA')",
                (identity, day),
            )
    database = object.__new__(module.DatabaseManager)
    database._connect = lambda: sqlite3.connect(path)
    rows = database.listar_atenciones_filtradas(
        modo="Entre fechas", fecha_txt=("03/11/2026", "04/11/2026")
    )
    assert [row["id"] for row in rows] == [3, 2]
    assert [
        row["id"]
        for row in database.listar_atenciones_filtradas(
            modo="Por fecha", fecha_txt="04/11/2026"
        )
    ] == [3]
    assert database.listar_ars_conteo() == [("MAPFRE/PALIC", 1)]
    assert len(database.listar_atenciones_filtradas(modo="Todos")) == 4


def test_central_history_passes_normalized_interval_to_parameterized_sql():
    proxy = object.__new__(_HybridDatabaseProxy)
    connection = Mock()
    connection.execute.return_value.fetchall.return_value = []
    connection.__enter__ = Mock(return_value=connection)
    connection.__exit__ = Mock(return_value=False)
    runtime = SimpleNamespace(
        offline=False, host=SimpleNamespace(connection_factory=lambda: connection)
    )
    object.__setattr__(proxy, "_runtime", runtime)
    object.__setattr__(proxy, "_database", SimpleNamespace())
    object.__setattr__(proxy, "_append_central_turn_filters", Mock())
    object.__setattr__(proxy, "_pending_history_rows", lambda *_args: [])
    assert (
        proxy._legacy_projection_readthrough(
            "listar_atenciones_filtradas",
            (),
            {
                "modo": "Entre fechas",
                "fecha_txt": ("03/11/2026", "04/11/2026"),
            },
        )
        == []
    )
    sql, values = connection.execute.call_args.args
    assert "BETWEEN %s AND %s" in sql
    assert values[:2] == ("2026-11-03", "2026-11-04")
    proxy._legacy_projection_readthrough(
        "listar_atenciones_filtradas", (), {"modo": "Todos"}
    )


@pytest.mark.parametrize(
    "reset,callback", [(True, True), (False, True), (False, False)]
)
def test_busy_history_preserves_the_pending_search_without_timer_spinning(
    reset, callback
):
    module = load_v15_application_module()
    state = {"loading": True}
    completion = Mock() if callback else None
    window = Mock()
    load = nested_callback(
        module.App.abrir_historial, "cargar_pagina", page_state=state, win=window
    )
    load(reset=reset, completion=completion, cache_only=False)
    window.after.assert_not_called()
    if reset:
        assert state["pending"]["cache_only"] is False
    elif callback:
        completion.assert_called_once_with(changed=False)


@pytest.mark.parametrize("exists", [True, False])
def test_queued_history_search_resumes_once_when_the_worker_finishes(exists):
    module = load_v15_application_module()
    pending = {"cache_only": False, "completion": Mock(), "show_loading": False}
    state = {"pending": pending}
    load = Mock()
    window = Mock(winfo_exists=Mock(return_value=exists))
    window.after.side_effect = lambda _delay, callback: callback()
    callback = nested_callback(
        module.App.abrir_historial,
        "continuar_busqueda_pendiente",
        page_state=state,
        win=window,
        cargar_pagina=load,
    )
    callback()
    callback()
    assert load.call_count == int(exists)
    if exists:
        load.assert_called_once_with(reset=True, **pending)
