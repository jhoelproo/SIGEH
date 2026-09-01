import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from database_config import write_bundled_database_url
from release_packaging import prepare_release, sha256_file


def _write_distribution(root: Path) -> Path:
    root.mkdir()
    (root / "SIGEH.exe").write_bytes(b"launcher")
    (root / "CALCULOS_QT.exe").write_bytes(b"main")
    internal = root / "_internal"
    internal.mkdir()
    (root / "version_config.json").write_text("{}", encoding="utf-8")
    (internal / "version_config.json").write_text("{}", encoding="utf-8")
    return root


def test_internal_release_requires_explicit_authenticated_bundle(tmp_path: Path):
    dist = _write_distribution(tmp_path / "dist")
    updater = tmp_path / "updater.exe"
    updater.write_bytes(b"updater")

    with pytest.raises(ValueError, match="backend_bundle"):
        prepare_release(
            dist,
            updater,
            tmp_path / "release",
            version="1.0.10",
            internal_deployment=True,
        )


def test_public_release_cannot_receive_backend_bundle(tmp_path: Path):
    dist = _write_distribution(tmp_path / "dist")
    updater = tmp_path / "updater.exe"
    updater.write_bytes(b"updater")
    bundle = tmp_path / "database_url.bundle"
    write_bundled_database_url(bundle, "postgresql://user:pass@central/hospital")

    with pytest.raises(ValueError, match="internal_deployment"):
        prepare_release(
            dist,
            updater,
            tmp_path / "release",
            version="1.0.10",
            backend_bundle=bundle,
        )


def test_internal_release_injects_bootstrap_only_into_private_archive(tmp_path: Path):
    dist = _write_distribution(tmp_path / "dist")
    updater = tmp_path / "updater.exe"
    updater.write_bytes(b"updater")
    bundle = tmp_path / "database_url.bundle"
    write_bundled_database_url(bundle, "postgresql://user:pass@central/hospital")

    result = prepare_release(
        dist,
        updater,
        tmp_path / "release",
        version="1.0.10",
        internal_deployment=True,
        backend_bundle=bundle,
    )

    assert result["archive"].name == "SIGEH-1.0.10-internal-windows-x64.zip"
    assert not (dist / "_internal" / "database_url.bundle").exists()
    with ZipFile(result["archive"]) as archive:
        names = set(archive.namelist())
        assert "SIGEH/_internal/database_url.bundle" in names
        assert (
            archive.read("SIGEH/_internal/database_url.bundle") == bundle.read_bytes()
        )
        assert not any(
            name.endswith((".db", ".sqlite", ".log", ".env")) for name in names
        )

    manifest = json.loads(result["manifest"].read_text(encoding="utf-8"))
    assert manifest["distribution_channel"] == "internal"
    assert manifest["backend_bootstrap"] == "portable_bundle"
    assert manifest["published_at"] == manifest["packaged_at"]
    assert manifest["sha256"] == sha256_file(result["archive"])
    component_names = set(manifest["components"])
    assert component_names == {"SIGEH.exe", "CALCULOS_QT.exe", "SIGEH_Updater.exe"}

    files = json.loads(result["file_manifest"].read_text(encoding="utf-8"))
    listed = {item["relative_path"] for item in files["files"]}
    assert listed == names
    assert all(len(item["sha256"]) == 64 for item in files["files"])
