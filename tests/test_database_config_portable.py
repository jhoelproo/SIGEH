from pathlib import Path

from database_config import (
    BUNDLED_DATABASE_FILE,
    CANONICAL_DATABASE_KEY,
    INVALID_DATABASE_KEYS,
    SEALED_DATABASE_FILE,
    configured_database_keys,
    describe_database_configuration,
    install_database_url_for_child,
    read_bundled_database_url,
    read_protected_env,
    read_sealed_database_url,
    resolve_database_url,
    write_bundled_database_url,
    write_sealed_database_url,
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

    assert (
        resolve_database_url(
            tmp_path,
            environment={
                CANONICAL_DATABASE_KEY: "postgresql://managed:secret@managed.example/live"
            },
        )
        == "postgresql://managed:secret@managed.example/live"
    )


def test_database_configuration_description_is_safe_and_identifies_source(
    tmp_path: Path,
):
    value = (
        "postgresql://postgres.abcdefghijklmnopqrst:portable-pass@"
        "aws-0-us-east-2.pooler.supabase.com:6543/postgres?sslmode=require"
    )
    write_bundled_database_url(tmp_path / BUNDLED_DATABASE_FILE, value)

    description = describe_database_configuration(tmp_path, environment={})

    assert description == {
        "config_source": "portable_bundle",
        "project_ref_redacted": "abcd…qrst",
        "host_redacted": "aws-…com",
        "port": 6543,
        "database": "postgres",
        "ssl_mode": "require",
        "credentials_present": True,
    }
    assert "portable-pass" not in repr(description)


def test_database_configuration_description_reports_missing_without_guessing(
    tmp_path: Path,
):
    assert describe_database_configuration(tmp_path, environment={}) == {
        "config_source": "missing",
        "project_ref_redacted": "",
        "host_redacted": "",
        "port": None,
        "database": "",
        "ssl_mode": "",
        "credentials_present": False,
    }


def test_dpapi_and_local_env_are_supported_without_changing_priority(tmp_path: Path):
    sealed_value = "postgresql://sealed:secret@central.example/hospital"
    write_sealed_database_url(tmp_path / SEALED_DATABASE_FILE, sealed_value)

    assert read_sealed_database_url(tmp_path / SEALED_DATABASE_FILE) == sealed_value
    assert resolve_database_url(tmp_path, environment={}) == sealed_value
    assert describe_database_configuration(tmp_path, environment={})[
        "config_source"
    ] == ("dpapi_protected")

    (tmp_path / SEALED_DATABASE_FILE).unlink()
    (tmp_path / ".env").write_text(
        "# private\nDATABASE_URL='postgresql://local:secret@local.example/hospital'\n",
        encoding="utf-8",
    )
    assert read_protected_env(tmp_path / ".env")[CANONICAL_DATABASE_KEY].startswith(
        "postgresql://local:"
    )
    assert describe_database_configuration(tmp_path, environment={})[
        "config_source"
    ] == ("local_env_file")


def test_child_environment_removes_misspelled_database_keys(tmp_path: Path):
    environment = {
        CANONICAL_DATABASE_KEY: "postgresql://managed:secret@managed.example/live",
        **{key: "must-go" for key in INVALID_DATABASE_KEYS},
    }

    assert install_database_url_for_child(environment, base_dir=tmp_path)
    assert all(key not in environment for key in INVALID_DATABASE_KEYS)


def test_tampered_or_malformed_local_configuration_is_not_accepted(tmp_path: Path):
    bundle = tmp_path / BUNDLED_DATABASE_FILE
    write_bundled_database_url(bundle, "postgresql://user:pass@central/hospital")
    payload = bytearray(bundle.read_bytes())
    payload[-1] ^= 1
    bundle.write_bytes(payload)
    (tmp_path / SEALED_DATABASE_FILE).write_text("not-base64", encoding="ascii")

    assert read_bundled_database_url(bundle) == ""
    assert read_sealed_database_url(tmp_path / SEALED_DATABASE_FILE) == ""
    assert resolve_database_url(tmp_path, environment={}) == ""
