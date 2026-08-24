"""Local administrative authorization for high-risk actions."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
from datetime import datetime, timedelta
from pathlib import Path

from .io_utils import ConfigError, atomic_write_json, load_json_file

_AUDIT_LOCK = threading.RLock()


class SecurityError(RuntimeError):
    """Raised for invalid security configuration or locked access."""


class AdminSecurity:
    ITERATIONS = 310_000
    MAX_FAILURES = 5
    LOCK_MINUTES = 5

    def __init__(self, config_path: str | os.PathLike[str], audit_path: str | os.PathLike[str]) -> None:
        self.config_path = Path(config_path)
        self.audit_path = Path(audit_path)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        try:
            value = load_json_file(self.config_path, default={})
        except ConfigError as exc:
            raise SecurityError("La configuracion administrativa esta danada") from exc
        if not isinstance(value, dict):
            raise SecurityError("La configuracion administrativa esta danada")
        return value

    def is_configured(self) -> bool:
        data = self._load()
        if not data:
            return False
        self._validate_config(data)
        return True

    @staticmethod
    def _validate_config(data: dict) -> None:
        try:
            salt = bytes.fromhex(str(data["salt"]))
            password_hash = bytes.fromhex(str(data["password_hash"]))
            iterations = int(data["iterations"])
            failed_attempts = int(data.get("failed_attempts", 0))
            locked_until = str(data.get("locked_until") or "")
        except (KeyError, TypeError, ValueError) as exc:
            raise SecurityError("La configuracion administrativa esta danada") from exc
        if data.get("format") != 1 or len(salt) != 32 or len(password_hash) != 32:
            raise SecurityError("La configuracion administrativa esta danada")
        if not 100_000 <= iterations <= 5_000_000 or not 0 <= failed_attempts < AdminSecurity.MAX_FAILURES:
            raise SecurityError("La configuracion administrativa esta danada")
        if locked_until:
            try:
                datetime.fromisoformat(locked_until)
            except ValueError as exc:
                raise SecurityError("La configuracion administrativa esta danada") from exc

    @staticmethod
    def validate_pin(pin: str) -> None:
        if not pin.isdigit() or not 6 <= len(pin) <= 64:
            raise SecurityError("El PIN administrativo debe tener entre 6 y 64 digitos")
        if len(set(pin)) == 1 or pin in {"123456", "654321", "000000"}:
            raise SecurityError("El PIN administrativo es demasiado predecible")

    def setup(self, pin: str, actor: str = "") -> None:
        if self._load():
            raise SecurityError("La autorizacion administrativa ya esta configurada")
        self.validate_pin(pin)
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, self.ITERATIONS)
        atomic_write_json(
            self.config_path,
            {
                "format": 1,
                "salt": salt.hex(),
                "password_hash": digest.hex(),
                "iterations": self.ITERATIONS,
                "failed_attempts": 0,
                "locked_until": "",
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        self.audit("ADMIN_PIN_CONFIGURED", actor=actor, success=True)

    def verify(self, pin: str, *, actor: str = "", action: str = "ADMIN_AUTH") -> bool:
        data = self._load()
        if not data:
            raise SecurityError("La autorizacion administrativa no esta configurada")
        self._validate_config(data)
        locked_until = str(data.get("locked_until") or "")
        if locked_until:
            try:
                lock_dt = datetime.fromisoformat(locked_until)
            except ValueError:
                lock_dt = datetime.min
            if datetime.now() < lock_dt:
                self.audit(action, actor=actor, success=False, detail="locked")
                raise SecurityError(f"Acceso bloqueado hasta {lock_dt.strftime('%H:%M')}")

        salt = bytes.fromhex(data["salt"])
        expected = bytes.fromhex(data["password_hash"])
        iterations = int(data.get("iterations") or self.ITERATIONS)
        actual = hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations)
        ok = hmac.compare_digest(actual, expected)
        if ok:
            data["failed_attempts"] = 0
            data["locked_until"] = ""
        else:
            failures = int(data.get("failed_attempts") or 0) + 1
            data["failed_attempts"] = failures
            if failures >= self.MAX_FAILURES:
                data["failed_attempts"] = 0
                data["locked_until"] = (datetime.now() + timedelta(minutes=self.LOCK_MINUTES)).isoformat(
                    timespec="seconds"
                )
        atomic_write_json(self.config_path, data)
        self.audit(action, actor=actor, success=ok)
        return ok

    def audit(self, event: str, *, actor: str = "", success: bool, detail: str = "") -> None:
        with _AUDIT_LOCK:
            previous = ""
            if self.audit_path.exists():
                try:
                    with self.audit_path.open("rb") as stream:
                        lines = [line for line in stream.read().splitlines() if line.strip()]
                    if lines:
                        last_record = json.loads(lines[-1].decode("utf-8"))
                        if not isinstance(last_record, dict) or not isinstance(last_record.get("hash"), str):
                            raise ValueError("invalid audit record")
                        previous = last_record["hash"]
                except (OSError, ValueError, json.JSONDecodeError):
                    previous = "CORRUPT_CHAIN"
            record = {
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "event": event,
                "actor": actor,
                "success": bool(success),
                "detail": detail,
                "workstation": socket.gethostname(),
                "previous_hash": previous,
            }
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            record["hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

    def verify_audit_chain(self) -> int:
        """Validate the append-only audit hash chain and return its record count."""
        if not self.audit_path.exists():
            return 0
        previous = ""
        count = 0
        with _AUDIT_LOCK, self.audit_path.open("r", encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    stored_hash = record.pop("hash")
                except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
                    raise SecurityError(f"La auditoria esta danada en la linea {line_number}") from exc
                if not isinstance(record, dict) or record.get("previous_hash") != previous:
                    raise SecurityError(f"La cadena de auditoria se rompio en la linea {line_number}")
                canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                calculated = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                if not isinstance(stored_hash, str) or not hmac.compare_digest(stored_hash, calculated):
                    raise SecurityError(f"El hash de auditoria no coincide en la linea {line_number}")
                previous = stored_hash
                count += 1
        return count
