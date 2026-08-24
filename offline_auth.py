"""Caché local, ligada al dispositivo, para login de contingencia offline."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

OFFLINE_LOGIN_VALID_DAYS = 30
PBKDF2_ITERATIONS = 240_000


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_offline_auth_path() -> Path:
    local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return local / "HospitalProvincial" / "FacturacionMedica" / "offline_auth.sqlite3"


def _derive(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password or "").encode("utf-8"),
        salt,
        max(120_000, int(iterations)),
    )


class OfflineAuthCache:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or default_offline_auth_path())

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS offline_auth_cache(
                       user_id TEXT NOT NULL,
                       username TEXT NOT NULL COLLATE NOCASE,
                       full_name TEXT NOT NULL DEFAULT '',
                       role_snapshot TEXT NOT NULL,
                       password_verifier BLOB NOT NULL,
                       password_salt BLOB NOT NULL,
                       verifier_iterations INTEGER NOT NULL,
                       device_id TEXT NOT NULL,
                       last_online_auth_at TEXT NOT NULL,
                       offline_valid_until TEXT NOT NULL,
                       PRIMARY KEY(username,device_id)
                   )"""
            )

    def store_online_auth(
        self,
        user: Mapping[str, Any],
        password: str,
        device_id: str,
        *,
        now: datetime | None = None,
        valid_days: int = OFFLINE_LOGIN_VALID_DAYS,
    ) -> None:
        username = str(user.get("username") or "").strip()
        role = str(user.get("role") or "").strip()
        if not username or not role or not password or not str(device_id or "").strip():
            raise ValueError("Usuario, rol, contraseña y dispositivo son obligatorios.")
        instant = now or _utc_now()
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        salt = secrets.token_bytes(16)
        verifier = _derive(password, salt)
        valid_until = instant + timedelta(days=max(1, int(valid_days)))
        self.initialize()
        with sqlite3.connect(self.path) as con:
            con.execute(
                """INSERT INTO offline_auth_cache(
                       user_id,username,full_name,role_snapshot,password_verifier,
                       password_salt,verifier_iterations,device_id,
                       last_online_auth_at,offline_valid_until
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(username,device_id) DO UPDATE SET
                       user_id=excluded.user_id,full_name=excluded.full_name,
                       role_snapshot=excluded.role_snapshot,
                       password_verifier=excluded.password_verifier,
                       password_salt=excluded.password_salt,
                       verifier_iterations=excluded.verifier_iterations,
                       last_online_auth_at=excluded.last_online_auth_at,
                       offline_valid_until=excluded.offline_valid_until""",
                (
                    str(user.get("id", user.get("user_id", "")) or ""),
                    username,
                    str(user.get("full_name", user.get("display_name", "")) or ""),
                    role,
                    verifier,
                    salt,
                    PBKDF2_ITERATIONS,
                    str(device_id),
                    instant.isoformat(),
                    valid_until.isoformat(),
                ),
            )

    def authenticate(
        self,
        username: str,
        password: str,
        device_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        self.initialize()
        with sqlite3.connect(self.path) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                """SELECT * FROM offline_auth_cache
                   WHERE username=? AND device_id=?""",
                (str(username or "").strip(), str(device_id or "").strip()),
            ).fetchone()
        if not row:
            return None
        instant = now or _utc_now()
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        try:
            valid_until = datetime.fromisoformat(str(row["offline_valid_until"]))
        except ValueError:
            return None
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        if instant > valid_until:
            return None
        candidate = _derive(
            password,
            bytes(row["password_salt"]),
            int(row["verifier_iterations"]),
        )
        if not hmac.compare_digest(candidate, bytes(row["password_verifier"])):
            return None
        return {
            "id": str(row["user_id"]),
            "user_id": str(row["user_id"]),
            "username": str(row["username"]),
            "full_name": str(row["full_name"]),
            "role": str(row["role_snapshot"]),
            "is_active": 1,
            "_offline_login": True,
            "_offline_valid_until": str(row["offline_valid_until"]),
        }

    def list_valid_usernames(
        self, device_id: str, *, now: datetime | None = None,
    ) -> list[str]:
        self.initialize()
        instant = now or _utc_now()
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        with sqlite3.connect(self.path) as con:
            rows = con.execute(
                """SELECT username FROM offline_auth_cache
                   WHERE device_id=? AND offline_valid_until>=?
                   ORDER BY username COLLATE NOCASE""",
                (str(device_id or "").strip(), instant.isoformat()),
            ).fetchall()
        return [str(row[0]) for row in rows]

    def contains_plaintext_password(self, password: str) -> bool:
        """Diagnóstico de seguridad para pruebas; no expone el verificador."""
        if not self.path.exists() or not password:
            return False
        return str(password).encode("utf-8") in self.path.read_bytes()


__all__ = [
    "OFFLINE_LOGIN_VALID_DAYS",
    "OfflineAuthCache",
    "default_offline_auth_path",
]
