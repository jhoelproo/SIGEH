import base64
import ctypes
import hashlib
import hmac
import os
import sys
from pathlib import Path
from typing import Mapping


CANONICAL_DATABASE_KEY = "DATABASE_URL"
INVALID_DATABASE_KEYS = ("DATABASE_URS", "DATABASE_URI", "SUPABASE_DB_URL")
SEALED_DATABASE_FILE = "database_url.protected"
BUNDLED_DATABASE_FILE = "database_url.bundle"
_DPAPI_ENTROPY = b"HospitalFacturacion:DatabaseUrl:v1"
# La configuración empaquetada evita una configuración manual en cada
# estación. No sustituye las credenciales de menor privilegio ni un almacén
# de secretos administrado: evita exposición accidental en texto plano y su
# HMAC detecta alteraciones del archivo entregado.
_BUNDLE_KEY_MATERIAL = b"HospitalFacturacion:PortableDatabaseConfig:v1"
_BUNDLE_PREFIX = b"HOSPITAL_DB_BUNDLE_V1:"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def read_protected_env(path: Path) -> dict[str, str]:
    """Lee pares simples sin interpretar caracteres especiales de la URL."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def _dpapi(value: bytes, *, protect: bool) -> bytes:
    """Protege o recupera bytes con DPAPI del usuario de Windows actual."""
    if os.name != "nt":
        raise RuntimeError("La configuración protegida requiere Windows.")

    class _DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.c_uint32),
            ("pbData", ctypes.POINTER(ctypes.c_byte)),
        ]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    source = ctypes.create_string_buffer(value)
    entropy = ctypes.create_string_buffer(_DPAPI_ENTROPY)
    source_blob = _DataBlob(len(value), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    entropy_blob = _DataBlob(
        len(_DPAPI_ENTROPY), ctypes.cast(entropy, ctypes.POINTER(ctypes.c_byte))
    )
    target_blob = _DataBlob()
    if protect:
        success = crypt32.CryptProtectData(
            ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None, 0,
            ctypes.byref(target_blob),
        )
    else:
        success = crypt32.CryptUnprotectData(
            ctypes.byref(source_blob), None, ctypes.byref(entropy_blob), None, None, 0,
            ctypes.byref(target_blob),
        )
    if not success:
        raise RuntimeError("No se pudo acceder a la configuración protegida.")
    try:
        return ctypes.string_at(target_blob.pbData, target_blob.cbData)
    finally:
        kernel32.LocalFree(target_blob.pbData)


def write_sealed_database_url(path: Path, database_url: str) -> None:
    """Guarda DATABASE_URL cifrada con DPAPI; nunca la registra ni la imprime."""
    value = str(database_url or "").strip()
    if not value:
        raise ValueError("DATABASE_URL está vacía.")
    sealed = _dpapi(value.encode("utf-8"), protect=True)
    path.write_text(base64.b64encode(sealed).decode("ascii"), encoding="ascii")


def read_sealed_database_url(path: Path) -> str:
    """Devuelve DATABASE_URL cifrada localmente, o vacío si no existe."""
    if not path.is_file():
        return ""
    try:
        payload = base64.b64decode(
            path.read_text(encoding="ascii").strip(), validate=True
        )
        return _dpapi(payload, protect=False).decode("utf-8").strip()
    except Exception:
        return ""


def _bundle_stream(key: bytes, nonce: bytes, length: int) -> bytes:
    """Genera un flujo determinista para el contenedor portable autenticado."""
    chunks: list[bytes] = []
    counter = 0
    while sum(map(len, chunks)) < length:
        chunks.append(
            hashlib.sha256(key + nonce + counter.to_bytes(4, "big")).digest()
        )
        counter += 1
    return b"".join(chunks)[:length]


def _bundle_key() -> bytes:
    return hashlib.sha256(_BUNDLE_KEY_MATERIAL).digest()


def write_bundled_database_url(path: Path, database_url: str) -> None:
    """Crea configuración portable autenticada sin guardar la URL como texto."""
    value = str(database_url or "").strip()
    if not value:
        raise ValueError("DATABASE_URL está vacía.")
    key = _bundle_key()
    nonce = os.urandom(16)
    plain = value.encode("utf-8")
    stream = _bundle_stream(key, nonce, len(plain))
    encrypted = bytes(left ^ right for left, right in zip(plain, stream))
    tag = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
    payload = _BUNDLE_PREFIX + base64.urlsafe_b64encode(nonce + tag + encrypted)
    path.write_bytes(payload)


def read_bundled_database_url(path: Path) -> str:
    """Lee una configuración portable autenticada, o devuelve vacío."""
    if not path.is_file():
        return ""
    try:
        raw = path.read_bytes().strip()
        if not raw.startswith(_BUNDLE_PREFIX):
            return ""
        payload = base64.urlsafe_b64decode(raw[len(_BUNDLE_PREFIX):])
        if len(payload) <= 48:
            return ""
        nonce, tag, encrypted = payload[:16], payload[16:48], payload[48:]
        key = _bundle_key()
        expected = hmac.new(key, nonce + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            return ""
        stream = _bundle_stream(key, nonce, len(encrypted))
        return bytes(left ^ right for left, right in zip(encrypted, stream)).decode(
            "utf-8"
        ).strip()
    except Exception:
        return ""


def _bundled_config_paths(root: Path) -> tuple[Path, ...]:
    bundle_root = getattr(sys, "_MEIPASS", None)
    paths = [root / BUNDLED_DATABASE_FILE]
    if bundle_root:
        resource_path = Path(bundle_root) / BUNDLED_DATABASE_FILE
        if resource_path not in paths:
            paths.append(resource_path)
    return tuple(paths)


def resolve_database_url(
    base_dir: os.PathLike[str] | str | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    prefer_local_file: bool = True,
) -> str:
    """
    Resuelve la conexión central usando exclusivamente DATABASE_URL.

    En una distribución portable el archivo protegido junto al ejecutable es
    la fuente estable. Así, una variable heredada de otra instalación no puede
    reemplazar accidentalmente la configuración incluida.
    """
    root = Path(base_dir).resolve() if base_dir is not None else application_root()
    sealed_value = read_sealed_database_url(root / SEALED_DATABASE_FILE)
    bundled_value = next(
        (read_bundled_database_url(path) for path in _bundled_config_paths(root)
         if read_bundled_database_url(path)),
        "",
    )
    protected_values = read_protected_env(root / ".env")
    process_environment = os.environ if environment is None else environment

    local_value = str(
        protected_values.get(CANONICAL_DATABASE_KEY) or ""
    ).strip()
    inherited_value = str(
        process_environment.get(CANONICAL_DATABASE_KEY) or ""
    ).strip()
    # El entorno explícito permite la configuración central administrada.  La
    # copia incluida protege los despliegues portables y el archivo local queda
    # como contingencia; ninguno de los tres valores se registra o muestra.
    # ``prefer_local_file`` se conserva para compatibilidad de llamadas viejas,
    # pero no altera la prioridad documentada de la distribución.
    del prefer_local_file
    return inherited_value or bundled_value or sealed_value or local_value


def install_database_url_for_child(
    environment: dict[str, str],
    *,
    base_dir: os.PathLike[str] | str | None = None,
) -> str:
    value = resolve_database_url(
        base_dir,
        environment=environment,
        prefer_local_file=True,
    )
    if value:
        environment[CANONICAL_DATABASE_KEY] = value
    for invalid_key in INVALID_DATABASE_KEYS:
        environment.pop(invalid_key, None)
    return value


def configured_database_keys(
    base_dir: os.PathLike[str] | str | None = None,
) -> set[str]:
    root = Path(base_dir).resolve() if base_dir is not None else application_root()
    keys = set(read_protected_env(root / ".env"))
    if (
        read_sealed_database_url(root / SEALED_DATABASE_FILE)
        or any(read_bundled_database_url(path) for path in _bundled_config_paths(root))
    ):
        keys.add(CANONICAL_DATABASE_KEY)
    return keys
