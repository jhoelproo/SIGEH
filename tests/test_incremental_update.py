"""Incremental releases reconstruct the exact target and retain rollback support."""

import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from release_delta import create_delta
from update_payload import assemble_delta, extract_distribution
from sigeh_update import UpdateError


def distribution(path, version, *, extra=None):
    files = {
        "SIGEH.exe": version.encode(),
        "CALCULOS_QT.exe": version.encode(),
        "SIGEH_Updater.exe": b"updater",
        "_internal/runtime.dll": b"unchanged runtime",
        "assets/logo.jpg": b"default logo",
        "version_config.json": json.dumps(
            {"product": "SIGEH", "version": version}
        ).encode(),
    }
    files.update(extra or {})
    with ZipFile(path, "w") as archive:
        for name, data in files.items():
            archive.writestr("SIGEH/" + name, data)
    return files


def delta_fixture(tmp_path):
    base, target = tmp_path / "base.zip", tmp_path / "target.zip"
    old = distribution(base, "1.3.4", extra={"_internal/obsolete.dll": b"old"})
    new = distribution(target, "1.3.5", extra={"_internal/new.dll": b"new"})
    install = tmp_path / "install"
    for name, data in old.items():
        dest = install / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
    metadata = create_delta(target, base, tmp_path / "delta.zip")
    return metadata, install, new


def test_patch_contains_only_changes_and_reconstructs_exact_distribution(tmp_path):
    metadata, install, expected = delta_fixture(tmp_path)
    assert metadata["from_version"] == "1.3.4"
    with ZipFile(tmp_path / "delta.zip") as archive:
        assert "SIGEH/_internal/runtime.dll" not in archive.namelist()
        assert "SIGEH/_internal/new.dll" in archive.namelist()
    payload = assemble_delta(
        tmp_path / "delta.zip", install, tmp_path / "staging", "1.3.5", "1.3.4"
    )
    actual = {
        p.relative_to(payload).as_posix(): p.read_bytes()
        for p in payload.rglob("*")
        if p.is_file()
    }
    assert actual == expected
    assert not (payload / "_internal/obsolete.dll").exists()
    assert (install / "_internal/obsolete.dll").exists()


def test_changed_local_runtime_is_not_trusted(tmp_path):
    _, install, _ = delta_fixture(tmp_path)
    (install / "_internal/runtime.dll").write_bytes(b"corrupted")
    with pytest.raises(UpdateError, match="local"):
        assemble_delta(
            tmp_path / "delta.zip", install, tmp_path / "stage", "1.3.5", "1.3.4"
        )


def test_custom_logo_does_not_force_a_full_download(tmp_path):
    _, install, expected = delta_fixture(tmp_path)
    (install / "assets/logo.jpg").write_bytes(b"custom hospital logo")
    with ZipFile(tmp_path / "delta.zip") as archive:
        assert "SIGEH/assets/logo.jpg" in archive.namelist()
    payload = assemble_delta(
        tmp_path / "delta.zip", install, tmp_path / "staging", "1.3.5", "1.3.4"
    )
    assert (payload / "assets/logo.jpg").read_bytes() == expected["assets/logo.jpg"]
    assert (install / "assets/logo.jpg").read_bytes() == b"custom hospital logo"


@pytest.mark.parametrize(
    "member",
    [
        "../escape",
        "SIGEH/../../escape",
        "SIGEH/C:/escape",
        "SIGEH/evil\\escape",
        "SIGEH/CON",
        "SIGEH/a.",
    ],
)
def test_distribution_rejects_unsafe_windows_paths(tmp_path, member):
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as stream:
        stream.writestr(member, b"bad")
    with pytest.raises(UpdateError):
        extract_distribution(archive, tmp_path / "staging")
    assert not (tmp_path / "escape").exists()


def test_full_package_extracts_to_a_separate_complete_payload(tmp_path):
    archive = tmp_path / "full.zip"
    expected = distribution(archive, "1.3.5")
    payload = extract_distribution(archive, tmp_path / "staging")
    assert all(
        (payload / name).read_bytes() == value for name, value in expected.items()
    )


def test_incremental_rejects_wrong_installed_version(tmp_path):
    _, install, _ = delta_fixture(tmp_path)
    with pytest.raises(UpdateError, match="versión"):
        assemble_delta(
            tmp_path / "delta.zip", install, tmp_path / "stage", "1.3.5", "1.0.0"
        )


@pytest.mark.parametrize(
    "files",
    [
        None,
        [],
        [None],
        [{"relative_path": 12}],
        [{"relative_path": "SIGEH/a", "size": -1, "sha256": "a" * 64}],
        [{"relative_path": "SIGEH/a", "size": 0, "sha256": "bad"}],
        [{"relative_path": "SIGEH/a", "size": False, "sha256": "a" * 64}],
    ],
)
def test_invalid_incremental_file_index(files):
    from update_payload import manifest_files

    with pytest.raises(UpdateError):
        manifest_files({"files": files})


def test_case_collisions_and_maximum_manifest_entries():
    from update_payload import manifest_files

    row = {"relative_path": "SIGEH/a", "size": 0, "sha256": "a" * 64}
    with pytest.raises(UpdateError):
        manifest_files({"files": [row, dict(row, relative_path="SIGEH/A")]})
    with pytest.raises(UpdateError):
        manifest_files({"files": [row] * 20001})


@pytest.mark.parametrize(
    "kind", ["duplicate", "symlink", "missing_runtime", "missing_executable"]
)
def test_full_distribution_rejects_invalid_layout_and_cleans_staging(tmp_path, kind):
    from zipfile import ZipInfo

    archive_path = tmp_path / "bad.zip"
    with ZipFile(archive_path, "w") as archive:
        for name in ("SIGEH.exe", "CALCULOS_QT.exe", "SIGEH_Updater.exe"):
            if kind != "missing_executable" or name != "SIGEH.exe":
                archive.writestr("SIGEH/" + name, b"program")
        if kind != "missing_runtime":
            archive.writestr("SIGEH/_internal/a.dll", b"runtime")
        if kind == "duplicate":
            archive.writestr("SIGEH/sigeh.exe", b"duplicate")
        if kind == "symlink":
            info = ZipInfo("SIGEH/link")
            info.external_attr = 0o120777 << 16
            archive.writestr(info, b"../outside")
        archive.writestr("SIGEH/directory/", b"")
    staging = tmp_path / "stage"
    with pytest.raises(UpdateError):
        extract_distribution(archive_path, staging)
    assert list(staging.iterdir()) == []


def rewrite_delta(path, transform, extra=None):
    from update_payload import DELTA_MANIFEST

    with ZipFile(path) as archive:
        contents = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(contents[DELTA_MANIFEST])
    contents[DELTA_MANIFEST] = json.dumps(transform(manifest)).encode()
    contents.update(extra or {})
    with ZipFile(path, "w") as archive:
        for name, content in contents.items():
            archive.writestr(name, content)


@pytest.mark.parametrize(
    "kind",
    [
        "extra",
        "size",
        "hash",
        "local_hash",
        "local_missing",
        "not_object",
        "format",
        "too_large",
    ],
)
def test_reconstruction_rejects_corruption_and_cleans_staging(
    tmp_path, monkeypatch, kind
):
    import update_payload

    _, install, _ = delta_fixture(tmp_path)
    path = tmp_path / "delta.zip"
    if kind == "extra":
        rewrite_delta(path, lambda data: data, {"SIGEH/unlisted.dll": b"bad"})
    elif kind in ("size", "hash"):

        def mutate(data):
            row = next(
                row
                for row in data["files"]
                if row["relative_path"] == "SIGEH/SIGEH.exe"
            )
            row["size" if kind == "size" else "sha256"] = (
                999 if kind == "size" else "0" * 64
            )
            return data

        rewrite_delta(path, mutate)
    elif kind == "local_hash":
        (install / "_internal/runtime.dll").write_bytes(b"incorrect runtime")
    elif kind == "local_missing":
        (install / "_internal/runtime.dll").unlink()
    elif kind == "too_large":
        monkeypatch.setattr(update_payload, "MAX_MANIFEST_BYTES", 1)
    else:
        rewrite_delta(
            path,
            (lambda _data: [])
            if kind == "not_object"
            else lambda data: dict(data, format=99),
        )
    stage = tmp_path / "stage"
    with pytest.raises(UpdateError):
        assemble_delta(path, install, stage, "1.3.5", "1.3.4")
    assert list(stage.iterdir()) == []


def test_local_copy_cannot_follow_a_path_outside_install(tmp_path):
    from update_payload import _copy_local_file

    install = tmp_path / "install"
    install.mkdir()
    (tmp_path / "outside").write_bytes(b"value")
    with pytest.raises(UpdateError):
        _copy_local_file(install, Path("../outside"), tmp_path / "copy", {"size": 5})


@pytest.mark.parametrize("kind", ["wrong_product", "older_target"])
def test_delta_builder_rejects_wrong_product_and_non_increasing_version(tmp_path, kind):
    base, target = tmp_path / "base.zip", tmp_path / "target.zip"
    distribution(base, "1.3.4")
    distribution(target, "1.3.3" if kind == "older_target" else "1.3.5")
    if kind == "wrong_product":
        with ZipFile(base, "w") as archive:
            archive.writestr("SIGEH/version_config.json", '{"product":"OTHER"}')
    with pytest.raises(UpdateError):
        create_delta(target, base, tmp_path / "delta.zip")
