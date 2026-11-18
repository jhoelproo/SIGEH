import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from ADMISION_PYSIDE6_V15 import derivaciones_gineco as enlace


def test_arranque_real_prepara_bandeja_para_una_base_nueva_y_una_existente(
    tmp_path, monkeypatch
):
    from pathlib import Path
    from admission_v15_adapter import DEFAULT_V15_ROOT, _load_v15_modules

    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    clase = _load_v15_modules(Path(DEFAULT_V15_ROOT)).database_class
    gestor = clase(
        db_name=str(tmp_path / "prueba.db"), session_context=SimpleNamespace()
    )
    assert not gestor.modo_salida_gineco(1)
    with closing(sqlite3.connect(gestor.db_name)) as db:
        db.execute("drop table gineco_envio")
    gestor._init_db()
    assert gestor.enviar_derivaciones_pendientes() == 0


@pytest.fixture
def cola(monkeypatch):
    monkeypatch.setenv("GINECO_PROYECTO", "a" * 20)
    monkeypatch.setenv("GINECO_TOKEN_ADMISION", "estacion-ficticia")
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            create table atenciones(id integer primary key, estado text);
            insert into atenciones values(1,'ACTIVA'),(2,'ANULADA');
        """)
        enlace.preparar_bandeja(db)
        enlace.preparar_bandeja(db)
        yield db


def paciente(hora="12:30 PM", modo=enlace.SOLO_ENVIAR):
    return {
        enlace.PREFERENCIA: modo,
        "Nombre": "Nombre ficticio",
        "Fecha": "17/11/2026",
        "Hora": hora,
        "Edad_num": 0,
        "Unidad": "Años",
        "NSS": "N/S",
    }


@pytest.mark.parametrize(
    ("hora", "esperada"),
    [
        ("12:30 AM", "00:30"),
        ("12:30 PM", "12:30"),
        ("05:30 PM", "17:30"),
        ("19:30", "19:30"),
    ],
)
def test_hora_edades_y_envio_sin_identificadores(cola, hora, esperada):
    enlace.encolar(cola, 1, "GINECOLOGIA", paciente(hora))
    enlace.encolar(cola, 2, "GINECOLOGIA", paciente())
    enviados = []
    assert enlace.entregar_pendientes(cola, enviados.append) == 1
    sobre = json.loads(enviados[0])
    assert sobre["llegada"] == f"2026-11-17T{esperada}:00"
    assert sobre["visita"] == 1
    assert sobre["campos"]["edad"] == "0"
    assert sobre["campos"]["nss"] == sobre["campos"]["cedula"] == ""
    assert enlace.entregar_pendientes(cola, enviados.append) == 0


def test_fallo_reintento_y_rollback(cola):
    p = dict(
        paciente(),
        Unidad="Meses",
        NSS="001002003",
        Motivo="Consulta",
        Prioridad="Ordinaria",
    )
    enlace.encolar(cola, 1, "GINECOLOGIA", p)
    cola.commit()
    enviar = Mock(side_effect=TimeoutError)
    with pytest.raises(TimeoutError):
        enlace.entregar_pendientes(cola, enviar)
    registro = json.loads(enviar.call_args.args[0])
    assert "edad" not in registro["campos"]
    assert registro["campos"]["nss"] == p["NSS"]
    assert registro["campos"]["motivo"] == "Consulta"
    assert registro["prioridad"] == "Ordinaria"
    enviar.side_effect = None
    assert enlace.entregar_pendientes(cola, enviar) == 1
    assert enviar.call_args_list[0] == enviar.call_args_list[1]
    enlace.encolar(cola, 2, "GINECOLOGIA", p)
    cola.rollback()
    assert cola.execute("select count(*) from gineco_envio").fetchone() == (1,)


@pytest.mark.parametrize("modo", [*enlace.MODOS, None, "Desconocido"])
def test_cada_modo_persiste_aun_sin_credenciales(cola, monkeypatch, modo):
    monkeypatch.delenv("GINECO_PROYECTO")
    enlace.encolar(cola, 1, "GENERAL", paciente(modo=modo))
    assert cola.execute("SELECT COUNT(*) FROM gineco_envio").fetchone() == (0,)
    enlace.encolar(cola, 1, "GINECOLOGIA", paciente(modo=modo))
    guardado, sobre = cola.execute("SELECT modo, sobre FROM gineco_envio").fetchone()
    assert guardado == (modo if modo in enlace.MODOS else enlace.SOLO_IMPRIMIR)
    envia = modo in (enlace.SOLO_ENVIAR, enlace.ENVIAR_E_IMPRIMIR)
    assert bool(sobre) == envia
    assert enlace.entregar_pendientes(cola, Mock()) == int(envia)
    assert cola.execute("SELECT modo, sobre FROM gineco_envio").fetchone() == (
        guardado,
        None,
    )


def test_actualizacion_conserva_derivaciones_previas():
    with closing(sqlite3.connect(":memory:")) as db:
        db.execute("CREATE TABLE gineco_envio (visita INTEGER PRIMARY KEY, sobre TEXT)")
        db.executemany("INSERT INTO gineco_envio VALUES (?,?)", [(1, None), (2, "{}")])
        enlace.preparar_bandeja(db)
        enlace.preparar_bandeja(db)
        assert db.execute(
            "SELECT sobre,modo FROM gineco_envio ORDER BY visita"
        ).fetchall() == [
            (None, enlace.SOLO_ENVIAR),
            ("{}", enlace.SOLO_ENVIAR),
        ]


def test_preferencia_se_recupera_al_reabrir_admision(tmp_path, monkeypatch):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    monkeypatch.setattr(admision, "APP_SETTINGS_PATH", tmp_path / "prefs.json")
    assert (
        enlace.modo_configurado(admision.cargar_app_settings()) == enlace.SOLO_IMPRIMIR
    )
    for modo in enlace.MODOS:
        assert admision.guardar_app_settings({enlace.PREFERENCIA: modo})
        assert enlace.modo_configurado(admision.cargar_app_settings()) == modo


def test_transporte_acotado(monkeypatch):
    monkeypatch.setenv("GINECO_PROYECTO", "a" * 20)
    monkeypatch.setenv("GINECO_CLAVE_PUBLICA", "sb_publishable_control")
    monkeypatch.delenv("GINECO_TOKEN_ADMISION", raising=False)
    with pytest.raises(ValueError):
        enlace.enviar("{}")
    monkeypatch.setenv("GINECO_TOKEN_ADMISION", "ficticio")
    salida = Mock()
    respuesta = SimpleNamespace(status=204)
    from contextlib import nullcontext

    salida.open.return_value = nullcontext(respuesta)
    monkeypatch.setattr(enlace, "build_opener", lambda *args: salida)
    enlace.enviar("{}")
    peticion = salida.open.call_args.args[0]
    assert peticion.full_url.endswith(".supabase.co/rest/v1/rpc/enviar_derivacion")
    assert peticion.data == b"{}"
    assert peticion.get_header("X-gineco-estacion") == "ficticio"
    respuesta.status = 201
    with pytest.raises(HTTPError):
        enlace.enviar("{}")
    assert (
        enlace.SinRedireccion().redirect_request(None, None, None, None, None, None)
        is None
    )


def test_dos_visitas_se_entregan_con_la_conexion_real_de_sigeh(tmp_path, monkeypatch):
    from sqlite_write_coordinator import connect_local_sqlite

    monkeypatch.setenv("GINECO_PROYECTO", "a" * 20)
    monkeypatch.setenv("GINECO_TOKEN_ADMISION", "QA")
    with closing(connect_local_sqlite(tmp_path / "varias.db", operation="qa")) as db:
        db.executescript("""
            create table atenciones(id integer primary key, estado text);
            insert into atenciones values(1,'ACTIVA'),(2,'ACTIVA');
        """)
        enlace.preparar_bandeja(db)
        for visita in (1, 2):
            enlace.encolar(db, visita, "GINECOLOGIA", paciente())
        db.commit()
        enviadas = []
        assert enlace.entregar_pendientes(db, enviadas.append) == 2
        assert [json.loads(s)["visita"] for s in enviadas] == [1, 2]
        assert enlace.entregar_pendientes(db, enviadas.append) == 0


@pytest.mark.parametrize("aceptar", [True, False])
def test_confirmacion_conserva_motivo_y_prioridad_solo_al_enviar(aceptar, monkeypatch):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit, QPushButton
    from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    qt = QApplication.instance() or QApplication([])
    datos = paciente()

    def decidir():
        dialogo = qt.activeModalWidget()
        dialogo.findChild(QLineEdit).setText("Motivo ficticio")
        dialogo.findChild(QComboBox).setCurrentText("Prioritaria")
        if aceptar:
            dialogo.accept()
        else:
            dialogo.reject()

    boton = QPushButton()
    QTimer.singleShot(0, decidir)
    assert (
        App._confirmar_envio_ginecologia(
            SimpleNamespace(boton_generar_pdf=boton), datos, "Confirmar visita"
        )
        is aceptar
    )
    if aceptar:
        assert (datos["Motivo"], datos["Prioridad"]) == (
            "Motivo ficticio",
            "Prioritaria",
        )
    else:
        assert "Motivo" not in datos


@pytest.mark.parametrize("modo", [*enlace.MODOS, None])
@pytest.mark.parametrize("impresion_estado", ["NO_APLICA", "ENVIADO_A_IMPRESORA"])
def test_salida_local_o_derivada_notifica_a_facturacion(
    modo, impresion_estado, monkeypatch
):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    app = Mock()
    app.db.modo_salida_gineco.return_value = modo
    app._should_print_immediate_attention.return_value = False
    app._obtener_trabajo_salida_seguro.return_value = {
        "excel_estado": "COMPLETADO",
        "impresion_estado": impresion_estado,
    }
    app._procesar_excel_salida.return_value = ""
    app._procesar_impresion_salida.return_value = ""
    app._output_payloads = {}
    app._render_immediate_attention_pdf.return_value = SimpleNamespace(
        should_print=False, pdf_path="ficticio.pdf"
    )
    monkeypatch.setattr(admision, "programar_limpieza_pdf_temporal", Mock())
    app._post_to_ui.side_effect = lambda accion: accion()
    app._finalizar_salida_atencion.side_effect = lambda resultado: (
        admision.App._finalizar_salida_atencion(app, resultado)
    )
    admision.App._procesar_salida_atencion(
        app, 41, "GINECOLOGIA", {}, {}, abrir_pdf_final=True
    )
    if modo == enlace.SOLO_ENVIAR:
        app._render_immediate_attention_pdf.assert_not_called()
        app._procesar_impresion_salida.assert_not_called()
        app._open_immediate_attention_pdf.assert_not_called()
    else:
        app._render_immediate_attention_pdf.assert_called_once()
        assert app._render_immediate_attention_pdf.call_args.kwargs["should_print"] is (
            modo is not None and impresion_estado != "ENVIADO_A_IMPRESORA"
        )
        app._procesar_impresion_salida.assert_called_once()
        app._open_immediate_attention_pdf.assert_called_once_with(
            app._render_immediate_attention_pdf.return_value
        )
    app.db.notify_detail_sheet_generated.assert_called_once_with(41)
    app._release_output_inflight.assert_called_once_with(41)


@pytest.mark.parametrize("hoja", ["GINECOLOGIA", "GENERAL"])
def test_cancelar_la_confirmacion_deja_la_visita_sin_registrar(monkeypatch, hoja):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    app = Mock()
    app.app_settings = {enlace.PREFERENCIA: enlace.SOLO_ENVIAR}
    app._final_revalidation_ready = True
    app._begin_sheet_operational_validation.return_value = False
    app._generation_turn_config.return_value = {"turno": "QA"}
    app._validar_campos_o_alertar.return_value = paciente(), hoja
    app._buscar_duplicado_turno_actual.return_value = None
    app._confirmar_envio_ginecologia.return_value = False
    monkeypatch.setattr(enlace, "habilitada", lambda: True)
    confirmar_local = Mock(return_value=False)
    monkeypatch.setattr(admision.messagebox, "askyesno", confirmar_local)
    admision.App.generar_pdf(app)
    assert app._confirmar_envio_ginecologia.call_count == int(hoja == "GINECOLOGIA")
    assert confirmar_local.call_count == int(hoja == "GENERAL")
    app.db.guardar_atencion.assert_not_called()


def test_consulta_programada_evita_solapamiento_y_respeta_cierre():
    from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App

    app = Mock()
    app._shutdown_complete = False
    app._envio_gineco = None
    App._sincronizar_ginecologia(app)
    app._start_worker.assert_called_once_with(
        app._entregar_ginecologia, name="envio-ginecologia"
    )
    app._envio_gineco.is_alive.return_value = True
    App._sincronizar_ginecologia(app)
    assert app._start_worker.call_count == 1
    app._envio_gineco.is_alive.return_value = False
    App._sincronizar_ginecologia(app)
    assert app._start_worker.call_count == 2
    app._shutdown_complete = True
    App._sincronizar_ginecologia(app)
    assert app.root.after.call_count == 3


def test_entrega_informa_error_recuperacion_y_bandeja_vacia():
    from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App

    app = Mock()
    app._post_to_ui.side_effect = lambda accion: accion()
    app.db.enviar_derivaciones_pendientes.side_effect = [OSError(), 2, 0]
    for _ in range(3):
        App._entregar_ginecologia(app)
    assert [aviso.args[1] for aviso in app.set_status.call_args_list] == [
        "warning",
        "ok",
    ]


def test_ginecologia_electiva_conserva_la_seleccion_ordinaria(monkeypatch):
    from ADMISION_PYSIDE6_V15.facturacion_tabs_pyside6 import App

    app = Mock()
    app.var_embarazada.get.return_value = False
    app.var_sexo.get.return_value = "Femenino"
    app.var_ginecologia.get.return_value = True
    app.entry_edad.get.return_value = "24"
    app.unidad_edad.get.return_value = "Años"
    monkeypatch.setattr(enlace, "habilitada", lambda: True)
    App.actualizar_embarazada(app)
    app.check_ginecologia.pack.assert_called_once()
    assert App.determinar_hoja(app) == "GINECOLOGIA"
    app.var_sexo.get.return_value = "Masculino"
    App.actualizar_embarazada(app)
    app.var_ginecologia.set.assert_called_once_with(False)
    assert App.determinar_hoja(app) == "GENERAL"
    app.var_sexo.get.return_value = "Femenino"
    monkeypatch.setattr(enlace, "habilitada", lambda: False)
    assert App.determinar_hoja(app) == "GINECOLOGIA"
    app.var_ginecologia.get.return_value = False
    app.var_embarazada.get.return_value = True
    assert App.determinar_hoja(app) == "GINECOLOGIA"


@pytest.mark.parametrize("etapa", ["PDF", "Impresión"])
def test_fallo_local_de_hoja_conserva_el_registro_y_reporta_etapa(etapa):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    app = Mock()
    app._obtener_trabajo_salida_seguro.return_value = {"excel_estado": "COMPLETADO"}
    app._render_immediate_attention_pdf.return_value = None
    app._procesar_excel_salida.return_value = ""
    app._procesar_impresion_salida.return_value = ""
    error = "Fallo de prueba"
    if etapa == "PDF":
        app._render_immediate_attention_pdf.side_effect = RuntimeError(error)
    else:
        app._procesar_impresion_salida.return_value = error
    app._post_to_ui.side_effect = lambda action: action()

    admision.App._procesar_salida_atencion(app, 41, "GENERAL", {}, {})

    resultado = app._finalizar_salida_atencion.call_args.args[0]
    assert resultado["atencion_id"] == 41
    assert resultado["errores"] == {etapa: error}
    app.db.modo_salida_gineco.assert_not_called()


@pytest.mark.parametrize(
    ("hoja", "habilitada", "aceptada"),
    [
        ("GINECOLOGIA", True, True),
        ("GINECOLOGIA", False, False),
        ("GENERAL", True, False),
    ],
)
def test_cedula_ausente_solo_es_excepcion_si_se_deriva(
    hoja, habilitada, aceptada, monkeypatch
):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    app = Mock()
    app.app_settings = {
        "validation_allow_missing_phone": True,
        "validation_allow_missing_cedula": False,
        "validation_warn_ars_sin_seguro": False,
        enlace.PREFERENCIA: enlace.SOLO_ENVIAR if habilitada else enlace.SOLO_IMPRIMIR,
    }
    for field in ("telefono", "cedula", "nss", "direccion", "nacionalidad", "ars"):
        getattr(app, f"entry_{field}").get.return_value = ""
    app.entry_nombre.get.return_value = "Paciente de prueba"
    app.entry_edad.get.return_value = "30"
    app.unidad_edad.get.return_value = "Años"
    app.var_sexo.get.return_value = "Femenino"
    app.determinar_hoja.return_value = hoja
    monkeypatch.setattr(enlace, "habilitada", lambda: habilitada)

    resultado = admision.App._validar_campos_o_alertar(app)

    if aceptada:
        datos, seleccionada = resultado
        assert seleccionada == hoja
        assert datos["Cédula"] == datos["NSS"] == ""
    else:
        assert resultado is None
        assert app._highlight_error.call_args.args[0] is app.entry_cedula


def test_selector_de_admision_muestra_y_guarda_opciones(tmp_path, monkeypatch):
    from PySide6.QtCore import QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication, QComboBox, QPushButton, QTabWidget

    from admission_v15_adapter import (
        AdmissionV15EventBus,
        AdmissionV15Factory,
        load_v15_application_module,
    )

    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GINECO_PROYECTO", raising=False)
    module = load_v15_application_module()
    monkeypatch.setattr(module, "APP_SETTINGS_PATH", tmp_path / "preferencias.json")
    monkeypatch.setattr(module.messagebox, "showinfo", Mock())
    qt = QApplication.instance() or QApplication([])
    host = SimpleNamespace(
        connection_factory=lambda: None,
        user={
            "id": 1,
            "username": "qa",
            "full_name": "Usuario de prueba",
            "role": "administrador",
        },
        session_id="qa-gineco",
        device_id="qa-gineco",
        device_name="QA",
        current_shift={},
        configuration={},
        logger=None,
        event_bus=AdmissionV15EventBus(),
    )
    widget = AdmissionV15Factory(
        host,
        session_checker=lambda: True,
        users_provider=lambda: [dict(host.user)],
        credential_verifier=lambda *_args: None,
    ).create_widget()
    admision = widget.admission
    try:
        admision._abrir_configuracion_interna()
        ventana = admision.configuracion_interna_win
        loop = QEventLoop()
        QTimer.singleShot(600, loop.quit)
        loop.exec()
        pestanas = ventana.findChildren(QTabWidget)
        general = next(
            tab
            for tab in pestanas
            if any(tab.tabText(i) == "Preferencias" for i in range(tab.count()))
        )
        general.setCurrentIndex(
            next(
                i
                for i in range(general.count())
                if general.tabText(i) == "Preferencias"
            )
        )
        preferencias = next(
            tab
            for tab in pestanas
            if any(tab.tabText(i) == "Ginecología" for i in range(tab.count()))
        )
        indice = next(
            i
            for i in range(preferencias.count())
            if preferencias.tabText(i) == "Ginecología"
        )
        preferencias.setCurrentIndex(indice)
        qt.processEvents()
        selector = preferencias.currentWidget().findChild(QComboBox)
        assert not selector.isEditable()
        assert (
            tuple(selector.itemText(i) for i in range(selector.count())) == enlace.MODOS
        )
        assert selector.currentText() == enlace.SOLO_IMPRIMIR
        selector.setCurrentText(enlace.ENVIAR_E_IMPRIMIR)
        guardar = next(
            boton
            for boton in ventana.findChildren(QPushButton)
            if "Guardar preferencias" in boton.text()
        )
        guardar.click()
        assert (
            enlace.modo_configurado(module.cargar_app_settings())
            == enlace.ENVIAR_E_IMPRIMIR
        )
        assert (
            enlace.modo_configurado(admision.app_settings) == enlace.ENVIAR_E_IMPRIMIR
        )
        ventana.destroy()
    finally:
        widget.shutdown()
        widget.deleteLater()


@pytest.mark.parametrize("modo", enlace.MODOS)
def test_guardar_atencion_persiste_la_salida_y_el_estado_del_documento(
    modo, tmp_path, monkeypatch
):
    from datetime import date, datetime, time
    from pathlib import Path

    from admission_v15_adapter import DEFAULT_V15_ROOT, _load_v15_modules

    monkeypatch.setenv("EMERGENCIAS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("GINECO_TOKEN_ADMISION", raising=False)
    clase = _load_v15_modules(Path(DEFAULT_V15_ROOT)).database_class
    ahora = datetime.combine(date(2026, 11, 18), time(10))
    turno = {
        "fecha_base": ahora.date(),
        "turno_codigo": "8AM_8AM",
        "representante": "OPERADOR PRUEBA",
        "inicio_real_dt": ahora,
        "administrative_override": True,
    }
    sesion = SimpleNamespace(
        audit_actor="qa", role="administrador", session_id="prueba"
    )
    db = clase(db_name=str(tmp_path / "admision.db"), session_context=sesion)
    datos = dict(paciente("10:15", modo), Sexo="Femenino", Fecha="18/11/2026")
    atencion = db.guardar_atencion(datos, "GINECOLOGIA", turno)
    db = clase(db_name=db.db_name, session_context=sesion)

    assert db.modo_salida_gineco(atencion) == modo
    estado = "NO_APLICA" if modo == enlace.SOLO_ENVIAR else "PENDIENTE"
    salida = db.obtener_trabajo_salida(atencion)
    assert salida["pdf_estado"] == salida["impresion_estado"] == estado
    with closing(db._connect()) as conn:
        assert enlace.entregar_pendientes(conn, Mock()) == int(
            enlace.requiere_envio(modo)
        )
    assert db.modo_salida_gineco(atencion) == modo


@pytest.mark.parametrize(
    ("hoja", "modo", "vinculada"),
    [
        ("GINECOLOGIA", enlace.SOLO_IMPRIMIR, False),
        ("GINECOLOGIA", enlace.SOLO_ENVIAR, False),
        ("GINECOLOGIA", enlace.ENVIAR_E_IMPRIMIR, True),
        ("GENERAL", enlace.ENVIAR_E_IMPRIMIR, True),
    ],
)
def test_el_formulario_guarda_el_modo_configurado_al_confirmar(
    hoja, modo, vinculada, monkeypatch
):
    from ADMISION_PYSIDE6_V15 import facturacion_tabs_pyside6 as admision

    app = Mock()
    app.app_settings = {
        enlace.PREFERENCIA: modo,
        "validation_confirm_before_generate": True,
    }
    app._final_revalidation_ready = True
    app._begin_sheet_operational_validation.return_value = False
    app._generation_turn_config.return_value = {"turno": "QA"}
    app._validar_campos_o_alertar.return_value = paciente(modo=None), hoja
    app._validar_campos_o_alertar.return_value[0].pop(enlace.PREFERENCIA)
    app._buscar_duplicado_turno_actual.return_value = None
    app._confirmar_envio_ginecologia.return_value = True
    app.db.guardar_atencion.return_value = 41
    app.db.obtener_revision_nss_atencion.return_value = None
    confirmar_local = Mock(return_value=True)
    monkeypatch.setattr(enlace, "habilitada", lambda: vinculada)
    monkeypatch.setattr(admision.messagebox, "askyesno", confirmar_local)

    admision.App.generar_pdf(app)

    app.db.guardar_atencion.assert_called_once()
    datos = app.db.guardar_atencion.call_args.args[0]
    assert datos.get(enlace.PREFERENCIA) == (modo if hoja == "GINECOLOGIA" else None)
    app._iniciar_salida_atencion.assert_called_once()
    if hoja == "GINECOLOGIA" and enlace.requiere_envio(modo):
        assert (
            "quedará pendiente" in app._confirmar_envio_ginecologia.call_args.args[1]
        ) is (not vinculada)
    else:
        confirmar_local.assert_called_once()
