from pathlib import Path

from database_config import (
    BUNDLED_DATABASE_FILE,
    CANONICAL_DATABASE_KEY,
    configured_database_keys,
    resolve_database_url,
    write_bundled_database_url,
)


def test_portable_bundle_resolves_without_env_or_local_dotenv(tmp_path: Path):
    value = "postgresql://portable-user:portable-pass@central.example/hospital"
    write_bundled_database_url(tmp_path / BUNDLED_DATABASE_FILE, value)

    assert resolve_database_url(tmp_path, environment={}) == value
    assert CANONICAL_DATABASE_KEY in configured_database_keys(tmp_path)
    assert value.encode("utf-8") not in (tmp_path / BUNDLED_DATABASE_FILE).read_bytes()


def test_process_database_url_has_priority_over_portable_bundle(tmp_path: Path):
    write_bundled_database_url(
        tmp_path / BUNDLED_DATABASE_FILE,
        "postgresql://portable-user:portable-pass@central.example/hospital",
    )

    assert resolve_database_url(
        tmp_path,
        environment={CANONICAL_DATABASE_KEY: "postgresql://managed:secret@managed.example/live"},
    ) == "postgresql://managed:secret@managed.example/live"
