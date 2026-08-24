"""Minimal authenticated client for the running Billing application."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from multiprocessing import AuthenticationError
from multiprocessing.connection import Client
import os
import re
from typing import Mapping


AUTH_PIPE_ENV = "HOSPITAL_AUTH_PIPE"
AUTH_KEY_ENV = "HOSPITAL_AUTH_PIPE_KEY"


class MainAppGatewayError(RuntimeError):
    pass


def _clean(value: object, maximum: int) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", str(value or "").strip())[:maximum]


@dataclass(frozen=True)
class MainAppRepresentative:
    username: str
    full_name: str
    role: str
    user_id: str = ""


class MainAppGateway:
    def __init__(self, address: str, authkey: bytes):
        self.address = str(address or "")
        self.authkey = bytes(authkey or b"")

    @classmethod
    def from_environment(
        cls,
        env: Mapping[str, str] | None = None,
    ) -> "MainAppGateway":
        values = os.environ if env is None else env
        address = str(values.get(AUTH_PIPE_ENV, "") or "").strip()
        encoded_key = str(values.get(AUTH_KEY_ENV, "") or "").strip()
        if not address or not encoded_key:
            return cls("", b"")
        try:
            key = base64.urlsafe_b64decode(encoded_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError):
            return cls("", b"")
        return cls(address, key)

    @property
    def available(self) -> bool:
        return bool(self.address and len(self.authkey) >= 16)

    def _request(self, payload: dict) -> dict:
        if not self.available:
            raise MainAppGatewayError(
                "Abre Admisión desde Facturación para utilizar esta función."
            )
        try:
            with Client(
                self.address,
                family="AF_PIPE",
                authkey=self.authkey,
            ) as connection:
                connection.send(payload)
                response = connection.recv()
        except (OSError, EOFError, AuthenticationError) as exc:
            raise MainAppGatewayError(
                "No se pudo validar con Facturación. Verifica que la aplicación principal siga abierta."
            ) from exc
        if not isinstance(response, dict):
            raise MainAppGatewayError("Facturación devolvió una respuesta no válida.")
        if not response.get("ok"):
            raise MainAppGatewayError(
                _clean(response.get("message"), 240)
                or "Facturación no autorizó el cambio."
            )
        return response

    def list_representatives(self) -> list[MainAppRepresentative]:
        response = self._request({"action": "list_representatives"})
        representatives = []
        for raw in response.get("representatives") or []:
            if not isinstance(raw, dict):
                continue
            username = _clean(raw.get("username"), 80)
            full_name = _clean(raw.get("full_name"), 160)
            role = _clean(raw.get("role"), 80).casefold()
            user_id = _clean(raw.get("user_id", raw.get("id")), 80)
            if username and full_name:
                representatives.append(
                    MainAppRepresentative(username, full_name, role, user_id)
                )
        return representatives

    def session_status(self) -> MainAppRepresentative:
        response = self._request({"action": "session_status"})
        representative = MainAppRepresentative(
            _clean(response.get("username"), 80),
            _clean(response.get("full_name"), 160),
            _clean(response.get("role"), 80).casefold(),
            _clean(response.get("user_id", response.get("id")), 80),
        )
        if not representative.username:
            raise MainAppGatewayError("La sesión principal ya no está disponible.")
        return representative

    def authorize_shift_change(
        self,
        *,
        username: str,
        password: str,
        target_username: str,
    ) -> tuple[str, MainAppRepresentative]:
        payload = {
            "action": "authorize_shift_change",
            "username": _clean(username, 80),
            "password": str(password or "")[:512],
            "target_username": _clean(target_username, 80),
        }
        try:
            response = self._request(payload)
        finally:
            payload["password"] = ""
        raw = response.get("representative") or {}
        representative = MainAppRepresentative(
            _clean(raw.get("username"), 80),
            _clean(raw.get("full_name"), 160),
            _clean(raw.get("role"), 80).casefold(),
            _clean(raw.get("user_id", raw.get("id")), 80),
        )
        if not representative.username or not representative.full_name:
            raise MainAppGatewayError("El representante autorizado no es válido.")
        return _clean(response.get("authorized_by"), 80), representative
