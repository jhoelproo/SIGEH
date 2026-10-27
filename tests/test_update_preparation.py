"""Fallback is limited to incompatible deltas, never ordinary network interruptions."""

import hashlib
import json
from unittest.mock import Mock

import pytest
import requests

import update_preparation as preparation
from sigeh_update import DeltaInfo
from tests.test_incremental_update import delta_fixture, distribution
from update_payload import assemble_delta


@pytest.mark.parametrize("failure", [None, "local", "404", "410"])
def test_preparation_prefers_delta_and_falls_back_if_incompatible(
    tmp_path, monkeypatch, failure
):
    _, install, expected = delta_fixture(tmp_path)
    delta_path, full = tmp_path / "delta.zip", tmp_path / "full.zip"
    distribution(full, "1.3.5", extra={"_internal/new.dll": b"new"})
    delta = DeltaInfo(
        "1.3.4",
        "delta",
        hashlib.sha256(delta_path.read_bytes()).hexdigest(),
        delta_path.stat().st_size,
    )
    if failure == "local":
        (install / "_internal/runtime.dll").unlink()

    def download(url, *_args, **_kwargs):
        if url == "delta" and failure in ("404", "410"):
            response = requests.Response()
            response.status_code = int(failure)
            raise requests.HTTPError(response=response)
        return delta_path if url == "delta" else full

    mocked = Mock(side_effect=download)
    monkeypatch.setattr(preparation, "download_archive", mocked)
    result = preparation.prepare_update(
        "full", "checksum", tmp_path / "updates", install, "1.3.5", delta=delta
    )
    assert all(
        (result / name).read_bytes() == value for name, value in expected.items()
    )
    assert mocked.call_count == (2 if failure else 1)


@pytest.mark.parametrize("status", [None, 403, 500])
def test_network_failure_does_not_start_large_fallback_download(
    tmp_path, monkeypatch, status
):
    if status is None:
        error = requests.ConnectionError("offline")
    else:
        response = requests.Response()
        response.status_code = status
        error = requests.HTTPError(response=response)
    mocked = Mock(side_effect=error)
    monkeypatch.setattr(preparation, "download_archive", mocked)
    with pytest.raises(requests.RequestException):
        preparation.prepare_update(
            "full",
            "a" * 64,
            tmp_path,
            tmp_path,
            "1.3.5",
            delta=DeltaInfo("1.3.4", "delta", "b" * 64, 1),
        )
    assert mocked.call_count == 1


def test_complete_package_without_delta(tmp_path, monkeypatch):
    full = tmp_path / "full.zip"
    distribution(full, "1.3.5")
    monkeypatch.setattr(preparation, "download_archive", Mock(return_value=full))
    result = preparation.prepare_update(
        "full", "a" * 64, tmp_path / "updates", tmp_path, "1.3.5"
    )
    assert (result / "SIGEH.exe").read_bytes() == b"1.3.5"


@pytest.mark.parametrize(
    "done,total,elapsed,transferred,expected",
    [
        (0, 0, 0, 0, "0.0 MB"),
        (1024, 2048, 1, 1024, "0.0 MB / 0.0 MB · 1 KB/s · 0:01 restantes"),
        (2048, 2048, 1, 1024, "0.0 MB / 0.0 MB · 1 KB/s"),
        (1024, 0, 1, 1024, "0.0 MB · 1 KB/s"),
        (0, 100, -1, -1, "0.0 MB / 0.0 MB"),
    ],
)
def test_progress_handles_empty_complete_and_unknown_length(
    done, total, elapsed, transferred, expected
):
    assert preparation.format_progress(done, total, elapsed, transferred) == expected


@pytest.mark.parametrize("rollback", [False, True])
def test_incremental_payload_uses_existing_updater_preservation_and_rollback(
    tmp_path, monkeypatch, rollback
):
    import updater

    _, install, _ = delta_fixture(tmp_path)
    originals = {
        "settings.json": b'{"hospital":"PRUEBA"}',
        "turnos_config.json": b'{"start":"08:00"}',
        "assets/logo.jpg": b"HOSPITAL LOGO",
        "_internal/data/users.sqlite": b"SYNTHETIC USERS",
        "_internal/data/pacientes.db": b"SYNTHETIC HISTORY",
    }
    for name, value in originals.items():
        target = install / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(value)
    payload = assemble_delta(
        tmp_path / "delta.zip", install, tmp_path / "staging", "1.3.5", "1.3.4"
    )
    manifest = tmp_path / "install.json"
    manifest.write_text(
        json.dumps(
            {
                "product": "SIGEH",
                "version": "1.3.5",
                "install_dir": str(install),
                "payload_dir": str(payload),
            }
        )
    )
    monkeypatch.setattr(updater, "update_storage_root", lambda: tmp_path / "updates")
    monkeypatch.setattr(updater.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(updater, "health_check_install", lambda _install: not rollback)
    monkeypatch.setattr(updater, "start_launcher", Mock())
    assert updater.apply_update(manifest, 0) == int(rollback)
    assert all(
        (install / name).read_bytes() == value for name, value in originals.items()
    )
    assert (install / "SIGEH.exe").read_bytes() == (b"1.3.4" if rollback else b"1.3.5")
