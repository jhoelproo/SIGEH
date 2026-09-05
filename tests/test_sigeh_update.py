from __future__ import annotations

import hashlib
import json
import sqlite3
import urllib.error
from pathlib import Path

import pytest

from sigeh_product import APP_VERSION, GITHUB_REPOSITORY, PRODUCT_ID
from sigeh_update import (
    LATEST_RELEASE_API,
    UpdateError,
    get_latest_release,
    is_newer,
    parse_checksum,
    parse_release,
    release_asset_names,
    resolve_release_payload,
    verify_archive,
    version_tuple,
)
from updater import (
    UPDATE_PRESERVE_PATHS,
    apply_update,
    merge_preserved,
    snapshot_preserved_local_state,
    verify_preserved_local_state,
)

ROOT = Path(__file__).resolve().parents[1]


def _release(repository=GITHUB_REPOSITORY, version="1.1.2"):
    archive, checksum, manifest = release_asset_names(version)
    return {
        "tag_name": f"v{version}",
        "name": f"SIGEH {version}",
        "body": "Prueba",
        "draft": False,
        "prerelease": False,
        "html_url": f"https://github.com/{repository}/releases/tag/v{version}",
        "assets": [
            {"name": archive, "browser_download_url": f"https://download/{archive}"},
            {"name": checksum, "browser_download_url": f"https://download/{checksum}"},
            {"name": manifest, "browser_download_url": f"https://download/{manifest}"},
        ],
    }


def test_product_and_channel_are_sigeh_only():
    assert PRODUCT_ID == "SIGEH"
    assert APP_VERSION == "1.1.3"
    assert LATEST_RELEASE_API.endswith(f"/{GITHUB_REPOSITORY}/releases/latest")
    assert "Hospital-Contreras-Facturacion1" not in LATEST_RELEASE_API


def test_tracked_version_metadata_matches_compiled_version():
    metadata = json.loads((ROOT / "version_config.json").read_text(encoding="utf-8"))

    assert metadata == {"product": PRODUCT_ID, "version": APP_VERSION}


def test_release_parser_accepts_complete_sigeh_release():
    release = parse_release(_release())
    assert release.version == "1.1.2"
    assert release.archive_name == "SIGEH-1.1.2-windows-x64.zip"
    assert not is_newer(release.version, APP_VERSION)
    assert is_newer("1.1.4", APP_VERSION)
    assert is_newer(APP_VERSION, "1.1.2")
    assert not is_newer(APP_VERSION, APP_VERSION)


def test_release_parser_rejects_old_repository_and_incomplete_assets():
    with pytest.raises(UpdateError, match="no pertenece"):
        parse_release(_release("jhoelproo/Hospital-Contreras-Facturacion1"))
    incomplete = _release()
    incomplete["assets"].pop()
    with pytest.raises(UpdateError, match="incompleta"):
        parse_release(incomplete)


@pytest.mark.parametrize("value", ["1", "1.0", "v1.0.0.1", "abc", ""])
def test_invalid_versions_are_rejected(value):
    with pytest.raises(UpdateError):
        version_tuple(value)


def test_checksum_and_archive_validation(tmp_path):
    archive = tmp_path / "SIGEH-1.0.2-windows-x64.zip"
    archive.write_bytes(b"PK\x03\x04SIGEH")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert parse_checksum(f"{digest} *{archive.name}\n", archive.name) == digest
    verify_archive(archive, digest)
    with pytest.raises(UpdateError, match="SHA-256"):
        verify_archive(archive, "0" * 64)
    with pytest.raises(UpdateError, match="checksum"):
        parse_checksum("invalid", archive.name)


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


def test_remote_release_manifest_and_checksum_are_cross_validated(monkeypatch):
    data = _release()
    parsed = parse_release(data)
    digest = "a" * 64
    responses = {
        LATEST_RELEASE_API: json.dumps(data).encode(),
        parsed.manifest_url: json.dumps(
            {
                "product": PRODUCT_ID,
                "version": parsed.version,
                "asset": parsed.archive_name,
                "sha256": digest,
                "entrypoint": "SIGEH.exe",
                "updater": "SIGEH_Updater.exe",
                "published_at": "2026-08-24T00:00:00Z",
                "minimum_supported_version": "1.0.0",
            }
        ).encode(),
        parsed.checksum_url: f"{digest} *{parsed.archive_name}\n".encode(),
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda request, timeout: _Response(responses[request.full_url]),
    )
    release = get_latest_release()
    payload = resolve_release_payload(release)
    assert payload.sha256 == digest
    assert payload.release.archive_name == parsed.archive_name


def test_update_channel_rejects_redirects_invalid_json_and_network_errors(monkeypatch):
    with pytest.raises(UpdateError, match="no autorizado"):
        get_latest_release("https://old.example/releases/latest")
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: _Response(b"not-json"),
    )
    with pytest.raises(UpdateError, match="información inválida"):
        get_latest_release()

    def fail(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fail)
    with pytest.raises(UpdateError, match="canal SIGEH"):
        get_latest_release()


def test_release_rejects_nonstable_and_invalid_manifest(monkeypatch):
    draft = _release()
    draft["draft"] = True
    with pytest.raises(UpdateError, match="no es estable"):
        parse_release(draft)

    release = parse_release(_release())
    checksum = "b" * 64

    def install(manifest, checksum_text=None):
        responses = {
            release.manifest_url: json.dumps(manifest).encode(),
            release.checksum_url: (
                checksum_text or f"{checksum} *{release.archive_name}\n"
            ).encode(),
        }
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda request, timeout: _Response(responses[request.full_url]),
        )

    base = {
        "product": PRODUCT_ID,
        "version": release.version,
        "asset": release.archive_name,
        "sha256": checksum,
        "entrypoint": "SIGEH.exe",
        "updater": "SIGEH_Updater.exe",
        "published_at": "2026-08-24T00:00:00Z",
        "minimum_supported_version": "1.0.0",
    }
    for field, value, message in (
        ("product", "OLD", "otro producto"),
        ("version", "9.9.9", "versión del manifest"),
        ("asset", "other.zip", "asset del manifest"),
        ("sha256", "invalid", "SHA-256 válido"),
    ):
        manifest = dict(base)
        manifest[field] = value
        install(manifest)
        with pytest.raises(UpdateError, match=message):
            resolve_release_payload(release)

    install(base, "c" * 64 + f" *{release.archive_name}\n")
    with pytest.raises(UpdateError, match="no coinciden"):
        resolve_release_payload(release)


def test_onedir_update_preserves_local_data_and_private_config(tmp_path):
    backup = tmp_path / "backup"
    install = tmp_path / "install"
    data = backup / "_internal" / "data"
    data.mkdir(parents=True)
    (data / "pacientes.db").write_bytes(b"local-history")
    private_config = backup / "_internal" / "database_url.bundle"
    private_config.write_bytes(b"private-local-config")

    merge_preserved(backup, install)

    assert (
        install / "_internal" / "data" / "pacientes.db"
    ).read_bytes() == b"local-history"
    assert (
        install / "_internal" / "database_url.bundle"
    ).read_bytes() == b"private-local-config"


def test_onedir_update_replaces_program_and_preserves_local_state(
    tmp_path, monkeypatch
):
    install = tmp_path / "SIGEH"
    payload = tmp_path / "payload"
    (install / "_internal" / "data").mkdir(parents=True)
    (install / "_internal" / "data" / "pacientes.db").write_bytes(b"history")
    (install / "_internal" / "database_url.bundle").write_bytes(b"private")
    (install / "old.txt").write_text("old", encoding="utf-8")
    (payload / "_internal").mkdir(parents=True)
    for executable in ("CALCULOS_QT.exe", "SIGEH.exe", "SIGEH_Updater.exe"):
        (payload / executable).write_bytes(b"new")
    manifest = tmp_path / "update.json"
    manifest.write_text(
        json.dumps(
            {
                "product": PRODUCT_ID,
                "version": "1.0.2",
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("updater.wait_for_process", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("updater.time.sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("updater.health_check_install", lambda *_args: True)
    monkeypatch.setattr("updater.update_storage_root", lambda: tmp_path / "local")
    monkeypatch.setattr("updater.start_launcher", lambda *_args, **_kwargs: None)

    assert apply_update(manifest, 0) == 0
    assert (install / "CALCULOS_QT.exe").read_bytes() == b"new"
    assert (install / "_internal" / "data" / "pacientes.db").read_bytes() == b"history"
    assert (install / "_internal" / "database_url.bundle").read_bytes() == b"private"
    assert not (install / "old.txt").exists()


def test_preserved_local_state_snapshot_detects_any_history_change(tmp_path):
    install = tmp_path / "SIGEH"
    data = install / "_internal" / "data"
    data.mkdir(parents=True)
    database = data / "pacientes.db"
    database.write_bytes(b"hospital-history")
    (data / "turnos_config.json").write_text("{}", encoding="utf-8")

    before = snapshot_preserved_local_state(install)

    assert Path("_internal/data") in UPDATE_PRESERVE_PATHS
    verify_preserved_local_state(before, install)
    database.write_bytes(b"changed-history")
    with pytest.raises(RuntimeError, match="estado local"):
        verify_preserved_local_state(before, install)


def test_v105_update_preserves_history_counts_and_distributed_ids(
    tmp_path, monkeypatch
):
    install = tmp_path / "SIGEH"
    payload = tmp_path / "payload"
    data = install / "_internal" / "data"
    data.mkdir(parents=True)
    database = data / "pacientes.db"
    connection = sqlite3.connect(database)
    try:
        connection.executescript(
            """CREATE TABLE pacientes(
                   id INTEGER PRIMARY KEY,
                   global_patient_id TEXT NOT NULL
               );
               CREATE TABLE atenciones(
                   id INTEGER PRIMARY KEY,
                   global_attention_id TEXT NOT NULL,
                   global_patient_id TEXT NOT NULL,
                   source_instance_id TEXT NOT NULL
               );
               INSERT INTO pacientes VALUES(1, 'patient-global-1');
               INSERT INTO atenciones VALUES(
                   1, 'attention-global-1', 'patient-global-1', 'hospital-source-1'
            );"""
        )
        connection.commit()
    finally:
        connection.close()
    (data / "device_id.json").write_text(
        '{"device_id":"hospital-device-1"}', encoding="utf-8"
    )
    before_hash = hashlib.sha256(database.read_bytes()).hexdigest()

    (payload / "_internal").mkdir(parents=True)
    for executable in ("CALCULOS_QT.exe", "SIGEH.exe", "SIGEH_Updater.exe"):
        (payload / executable).write_bytes(b"new")
    manifest = tmp_path / "update.json"
    manifest.write_text(
        json.dumps(
            {
                "product": PRODUCT_ID,
                "version": APP_VERSION,
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("updater.wait_for_process", lambda *_args: None)
    monkeypatch.setattr("updater.time.sleep", lambda *_args: None)
    monkeypatch.setattr("updater.health_check_install", lambda *_args: True)
    monkeypatch.setattr("updater.update_storage_root", lambda: tmp_path / "local")
    monkeypatch.setattr("updater.start_launcher", lambda *_args: None)

    assert apply_update(manifest, 0) == 0
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before_hash
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA quick_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM pacientes").fetchone() == (1,)
        assert connection.execute("SELECT COUNT(*) FROM atenciones").fetchone() == (1,)
        assert connection.execute(
            "SELECT global_attention_id, global_patient_id, source_instance_id "
            "FROM atenciones"
        ).fetchone() == (
            "attention-global-1",
            "patient-global-1",
            "hospital-source-1",
        )
    assert json.loads((data / "device_id.json").read_text(encoding="utf-8")) == {
        "device_id": "hospital-device-1"
    }
