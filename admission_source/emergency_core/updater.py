"""Compatibilidad del módulo de Admisión con el canal único SIGEH."""

from sigeh_product import APP_VERSION, GITHUB_REPOSITORY, PRODUCT_ID
from sigeh_update import (
    LATEST_RELEASE_API,
    ReleaseInfo,
    UpdateError,
    VerifiedReleasePayload,
    get_latest_release,
    is_newer,
    parse_checksum,
    parse_release,
    release_asset_names,
    resolve_release_payload,
    sha256_file,
    verify_archive,
    version_tuple,
)


__all__ = [
    "APP_VERSION",
    "GITHUB_REPOSITORY",
    "LATEST_RELEASE_API",
    "PRODUCT_ID",
    "ReleaseInfo",
    "UpdateError",
    "VerifiedReleasePayload",
    "get_latest_release",
    "is_newer",
    "parse_checksum",
    "parse_release",
    "release_asset_names",
    "resolve_release_payload",
    "sha256_file",
    "verify_archive",
    "version_tuple",
]
