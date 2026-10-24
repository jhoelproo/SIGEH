"""Versioned PBKDF2 verifiers with bounded, backward-compatible decoding."""

import base64
import binascii
import hashlib
import hmac
import secrets

ITERATIONS = 600_000
LEGACY_ITERATIONS = 120_000
MAX_ITERATIONS = 2_000_000
ALGORITHM = "pbkdf2_sha256"
PRIVATE_USER_FIELDS = {
    "password_hash",
    "password_salt",
    "security_answer_hash",
    "security_answer_salt",
}


def derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)


def hash_secret(password: str, *, compatible: bool = False) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    iterations = LEGACY_ITERATIONS if compatible else ITERATIONS
    digest = base64.b64encode(derive(password, salt, iterations)).decode("ascii")
    verifier = digest if compatible else f"{ALGORITHM}${iterations}${digest}"
    return verifier, base64.b64encode(salt).decode("ascii")


def _decode_hash(hashed: str) -> tuple[int, bytes]:
    parts = hashed.split("$")
    if len(parts) == 1:
        iterations, encoded = LEGACY_ITERATIONS, parts[0]
    elif len(parts) == 3 and parts[0] == ALGORITHM:
        iterations, encoded = int(parts[1]), parts[2]
    else:
        raise ValueError("Unknown verifier format")
    if not LEGACY_ITERATIONS <= iterations <= MAX_ITERATIONS:
        raise ValueError("Invalid verifier cost")
    digest = base64.b64decode(encoded, validate=True)
    if len(digest) != 32:
        raise ValueError("Invalid verifier length")
    return iterations, digest


def verify_secret(password: str, hashed: str, salt_b64: str) -> bool:
    try:
        iterations, expected = _decode_hash(hashed)
        salt = base64.b64decode(salt_b64, validate=True)
        if len(salt) != 16:
            return False
        return hmac.compare_digest(derive(password, salt, iterations), expected)
    except (AttributeError, TypeError, ValueError, binascii.Error):
        return False


def needs_rehash(hashed: str) -> bool:
    iterations, _digest = _decode_hash(hashed)
    return iterations < ITERATIONS


def public_user(row) -> dict:
    return {
        key: value for key, value in dict(row).items() if key not in PRIVATE_USER_FIELDS
    }


def offline_verifier_matches(password, salt, expected, iterations) -> bool:
    try:
        cost = int(iterations)
        if not LEGACY_ITERATIONS <= cost <= MAX_ITERATIONS:
            return False
        if len(salt) != 16 or len(expected) != 32:
            return False
        return hmac.compare_digest(derive(password, salt, cost), expected)
    except (TypeError, ValueError):
        return False
