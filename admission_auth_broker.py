"""Authenticated local bridge from Admission back to the Billing session.

The broker exposes only active staff identities and credential verification.
It never sends password hashes, database credentials, or clinical data to the
Admission process.
"""

from __future__ import annotations

import base64
from multiprocessing.connection import Client, Listener
import os
import re
import secrets
import threading
from typing import Callable


AUTH_PIPE_ENV = "HOSPITAL_AUTH_PIPE"
AUTH_KEY_ENV = "HOSPITAL_AUTH_PIPE_KEY"


def _clean(value: object, maximum: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "").strip())[:maximum]


class AdmissionAuthBrokerError(RuntimeError):
    pass


class AdmissionAuthBroker:
    """Serve a minimal request protocol over an authenticated Windows pipe."""

    def __init__(
        self,
        *,
        session_id: str,
        current_user: dict,
        users_provider: Callable[[], list[dict]],
        credential_verifier: Callable[[str, str], dict | None],
        audit_callback: Callable[[str, str], None] | None = None,
    ):
        safe_session = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:80]
        safe_session = safe_session or secrets.token_hex(12)
        self.address = rf"\\.\pipe\HospitalAdmissionAuth-{safe_session}"
        self.authkey = secrets.token_bytes(32)
        self.current_user = dict(current_user or {})
        self.users_provider = users_provider
        self.credential_verifier = credential_verifier
        self.audit_callback = audit_callback
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error = ""
        self._listener = None

    def environment(self) -> dict[str, str]:
        self.start()
        return {
            AUTH_PIPE_ENV: self.address,
            AUTH_KEY_ENV: base64.urlsafe_b64encode(self.authkey).decode("ascii"),
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = ""
        self._thread = threading.Thread(
            target=self._serve,
            name="AdmissionAuthBroker",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(3.0) or self._startup_error:
            raise AdmissionAuthBrokerError(
                "No se pudo preparar la validación local de usuarios."
            )

    def stop(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            return
        try:
            with Client(self.address, family="AF_PIPE", authkey=self.authkey) as connection:
                connection.send({"action": "shutdown"})
                connection.recv()
        except (OSError, EOFError):
            pass
        self._thread.join(timeout=2.0)

    def _serve(self) -> None:
        try:
            self._listener = Listener(
                self.address,
                family="AF_PIPE",
                authkey=self.authkey,
            )
        except Exception as exc:
            self._startup_error = type(exc).__name__
            self._ready.set()
            return
        self._ready.set()
        try:
            while True:
                try:
                    connection = self._listener.accept()
                    with connection:
                        request = connection.recv()
                        response = self._handle(request)
                        connection.send(response)
                    if isinstance(request, dict) and request.get("action") == "shutdown":
                        break
                except (EOFError, OSError):
                    continue
        finally:
            try:
                self._listener.close()
            except Exception:
                pass

    def _active_users(self) -> list[dict]:
        users = []
        representative_roles = {
            "auxiliar",
            "administrador",
            "facturador de auditoria",
        }
        for raw in self.users_provider() or []:
            if int(raw.get("is_active") or 0) != 1:
                continue
            username = _clean(raw.get("username"), 80)
            full_name = _clean(raw.get("full_name"), 160)
            role = _clean(raw.get("role"), 80).casefold()
            if username and full_name and role in representative_roles:
                users.append(
                    {"username": username, "full_name": full_name, "role": role}
                )
        return sorted(users, key=lambda item: item["full_name"].casefold())

    def _handle(self, request: object) -> dict:
        if not isinstance(request, dict):
            return {"ok": False, "message": "Solicitud no válida."}
        action = _clean(request.get("action"), 40)
        if action == "shutdown":
            return {"ok": True}
        if action == "session_status":
            return {
                "ok": True,
                "username": _clean(self.current_user.get("username"), 80),
                "full_name": _clean(self.current_user.get("full_name"), 160),
                "role": _clean(self.current_user.get("role"), 80).casefold(),
            }
        if action == "list_representatives":
            try:
                return {"ok": True, "representatives": self._active_users()}
            except Exception:
                return {
                    "ok": False,
                    "message": "No se pudo cargar la lista de representantes.",
                }
        if action != "authorize_shift_change":
            return {"ok": False, "message": "Operación no permitida."}

        username = _clean(request.get("username"), 80)
        password = str(request.get("password") or "")[:512]
        request["password"] = ""
        target_username = _clean(request.get("target_username"), 80)
        current_username = _clean(self.current_user.get("username"), 80)
        if not username or username.casefold() != current_username.casefold():
            return {
                "ok": False,
                "message": "Debes confirmar con el usuario que inició esta sesión.",
            }
        try:
            verified = self.credential_verifier(username, password)
        finally:
            password = ""
        if not verified:
            return {"ok": False, "message": "Usuario o contraseña incorrectos."}
        # La configuración ya está protegida por el PIN administrativo de
        # Admisión. Aquí se exige además la identidad real de la sesión; nunca
        # se permite asignar el turno a un usuario distinto del conectado.
        if target_username.casefold() != current_username.casefold():
            return {
                "ok": False,
                "message": (
                    "Para asignar otro representante, cierra la sesión de "
                    "Facturación e inicia con ese usuario."
                ),
            }
        target = next(
            (
                item for item in self._active_users()
                if item["username"].casefold() == target_username.casefold()
            ),
            None,
        )
        if target is None:
            return {
                "ok": False,
                "message": "El representante ya no está habilitado en Facturación.",
            }
        if self.audit_callback:
            try:
                self.audit_callback(
                    username,
                    f"Autorizó corrección de turno para {target['username']}",
                )
            except Exception:
                pass
        return {
            "ok": True,
            "authorized_by": username,
            "representative": target,
        }
