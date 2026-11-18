"""Entrega durable de admisiones al consultorio de ginecología."""

import json
import os
import re
import uuid
from datetime import datetime
from email.message import Message
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener


class SinRedireccion(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def habilitada():
    return bool(
        os.environ.get("GINECO_PROYECTO")
        and os.environ.get("GINECO_CLAVE_PUBLICA")
        and os.environ.get("GINECO_TOKEN_ADMISION")
    )


SOLO_IMPRIMIR = "Solo imprimir"
SOLO_ENVIAR = "Solo enviar"
ENVIAR_E_IMPRIMIR = "Enviar e imprimir"
MODOS = (SOLO_IMPRIMIR, SOLO_ENVIAR, ENVIAR_E_IMPRIMIR)
PREFERENCIA = "gineco_modo_salida"
PREFERENCIA_ENVIO = "gineco_envio_activo"


def modo_configurado(preferencias):
    modo = preferencias.get(PREFERENCIA)
    return modo if modo in MODOS else SOLO_IMPRIMIR


def envio_activo(preferencias):
    return preferencias.get(PREFERENCIA_ENVIO) is True


def modo_efectivo(preferencias):
    return (
        modo_configurado(preferencias) if envio_activo(preferencias) else SOLO_IMPRIMIR
    )


def requiere_envio(modo):
    return modo in (SOLO_ENVIAR, ENVIAR_E_IMPRIMIR)


def preparar_bandeja(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS gineco_envio(
        visita INTEGER PRIMARY KEY REFERENCES atenciones(id) ON DELETE CASCADE,
        sobre TEXT, modo TEXT NOT NULL)""")
    columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(gineco_envio)")}
    if "modo" not in columnas:
        conn.execute(
            "ALTER TABLE gineco_envio ADD COLUMN modo TEXT NOT NULL DEFAULT 'Solo enviar'"
        )


def hora_de_llegada(datos):
    hora, _, periodo = datos["Hora"].strip().upper().partition(" ")
    formato = "%d/%m/%Y %I:%M" if periodo else "%d/%m/%Y %H:%M"
    llegada = datetime.strptime(f"{datos['Fecha']} {hora}", formato)
    if periodo == "PM":
        llegada = llegada.replace(hour=llegada.hour + 12)
    return llegada.isoformat()


def encolar(conn, visita, hoja, datos):
    if hoja != "GINECOLOGIA":
        return
    modo = modo_configurado(datos)
    sobre = crear_sobre(visita, datos) if requiere_envio(modo) else None
    conn.execute(
        "INSERT INTO gineco_envio(visita,sobre,modo) VALUES (?,?,?)",
        (visita, sobre, modo),
    )


def crear_sobre(visita, datos):
    campos = {
        clave: str(datos.get(origen) or "").strip()
        for clave, origen in (
            ("nombre", "Nombre"),
            ("cedula", "Cédula"),
            ("ars", "Aseguradora (ARS)"),
            ("telefono", "Teléfono"),
            ("direccion", "Dirección"),
            ("nacionalidad", "Nacionalidad"),
        )
    }
    nss = str(datos.get("NSS") or "").strip()
    campos["nss"] = nss if nss.isdecimal() else ""
    if datos.get("Unidad") == "Años":
        campos["edad"] = str(datos.get("Edad_num", ""))
    sobre = dict(
        id=str(uuid.uuid4()),
        visita=visita,
        llegada=hora_de_llegada(datos),
        prioridad="",
        campos=campos,
    )
    return json.dumps(sobre, ensure_ascii=False)


def enviar(sobre):
    proyecto = os.environ.get("GINECO_PROYECTO", "")
    clave = os.environ.get("GINECO_CLAVE_PUBLICA", "")
    token = os.environ.get("GINECO_TOKEN_ADMISION", "")
    if not (
        re.fullmatch("[a-z0-9]{20}", proyecto)
        and clave.startswith("sb_publishable_")
        and token
    ):
        raise ValueError("Falta configurar el enlace con Ginecología.")
    payload = json.loads(sobre)
    payload.get("campos", {}).pop("motivo", None)
    payload["prioridad"] = ""
    peticion = Request(
        f"https://{proyecto}.supabase.co/rest/v1/rpc/enviar_derivacion",
        data=json.dumps(payload, ensure_ascii=False).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "apikey": clave,
            "x-gineco-estacion": token,
        },
    )
    with build_opener(SinRedireccion()).open(peticion, timeout=8) as respuesta:
        if respuesta.status not in (200, 204):
            raise HTTPError(
                peticion.full_url, respuesta.status, "Envío rechazado", Message(), None
            )


def entregar_pendientes(conn, entregar=enviar, *, puede_entregar=None):
    filas = conn.execute(
        """SELECT g.visita,g.sobre FROM gineco_envio g JOIN atenciones a ON a.id=g.visita
           WHERE g.sobre IS NOT NULL AND a.estado='ACTIVA' ORDER BY g.visita LIMIT 50"""
    ).fetchall()
    entregadas = 0
    for visita, sobre in filas:
        if puede_entregar is not None and not puede_entregar():
            break
        entregar(sobre)
        conn.execute("UPDATE gineco_envio SET sobre=NULL WHERE visita=?", (visita,))
        conn.commit()
        entregadas += 1
    return entregadas
