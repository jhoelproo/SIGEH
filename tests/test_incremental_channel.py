"""Optional delta metadata never changes validation of the full fallback package."""

import json

import pytest

import sigeh_update as updates
from tests.test_sigeh_update import _release
from tests.test_release_packaging_fresh_install import _write_distribution
from release_packaging import prepare_release


def manifest_with_delta():
    data = _release(version="9.0.0")
    name = f"SIGEH-9.0.0-from-{updates.APP_VERSION}-delta.zip"
    url = f"https://github.com/{updates.GITHUB_REPOSITORY}/releases/download/v9.0.0/{name}"
    data["assets"].append({"name": name, "browser_download_url": url})
    release = updates.parse_release(data)
    manifest = {
        "product": "SIGEH",
        "version": "9.0.0",
        "asset": release.archive_name,
        "sha256": "a" * 64,
        "entrypoint": "SIGEH.exe",
        "updater": "SIGEH_Updater.exe",
        "published_at": "2026-10-27",
        "minimum_supported_version": "1.0.0",
        "deltas": [
            {
                "from_version": updates.APP_VERSION,
                "asset": name,
                "sha256": "b" * 64,
                "size": 123,
            }
        ],
    }
    return data, manifest, url


def test_verified_channel_selects_only_matching_delta(monkeypatch):
    data, manifest, url = manifest_with_delta()
    release = updates.parse_release(data)
    monkeypatch.setattr(updates, "_load_manifest", lambda *_args: manifest)
    monkeypatch.setattr(
        updates,
        "_request_bytes",
        lambda *_args, **_kw: f"{'a' * 64} *{release.archive_name}".encode(),
    )
    payload = updates.resolve_release_payload(release)
    assert payload.sha256 == "a" * 64
    assert payload.delta == updates.DeltaInfo(updates.APP_VERSION, url, "b" * 64, 123)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing",
        "foreign",
        "old_version",
        "bad_hash",
        "bad_size",
        "wrong_name",
        "not_list",
        "not_dict",
        "negative_size",
        "bool_size",
    ],
)
def test_invalid_optional_delta_uses_full_download(mutation):
    data, manifest, _ = manifest_with_delta()
    row = manifest["deltas"][0]
    if mutation == "missing":
        data["assets"].pop()
    elif mutation == "foreign":
        data["assets"][-1]["browser_download_url"] = "https://foreign.test/delta.zip"
    elif mutation == "not_list":
        manifest["deltas"] = {}
    elif mutation == "not_dict":
        manifest["deltas"] = [None]
    else:
        key, value = {
            "old_version": ("from_version", "0.0.1"),
            "bad_hash": ("sha256", "bad"),
            "bad_size": ("size", "123"),
            "wrong_name": ("asset", "wrong.zip"),
            "negative_size": ("size", -1),
            "bool_size": ("size", True),
        }[mutation]
        row[key] = value
    assert updates._select_delta(manifest, updates.parse_release(data)) is None


def test_packaging_emits_full_and_optional_delta(tmp_path):
    dist = _write_distribution(tmp_path / "dist")
    updater = tmp_path / "updater.exe"
    updater.write_bytes(b"updater")
    previous = prepare_release(dist, updater, tmp_path / "previous", version="1.3.4")
    (dist / "CALCULOS_QT.exe").write_bytes(b"updated main")
    current = prepare_release(
        dist,
        updater,
        tmp_path / "current",
        version="1.3.5",
        base_archive=previous["archive"],
    )
    manifest = json.loads(current["manifest"].read_text())
    assert manifest["deltas"][0]["asset"] == current["delta"].name
    assert manifest["deltas"][0]["sha256"] == updates.sha256_file(current["delta"])
    assert current["archive"].is_file()


def test_internal_package_cannot_generate_public_delta(tmp_path):
    with pytest.raises(ValueError, match="público"):
        prepare_release(
            tmp_path,
            tmp_path,
            tmp_path,
            internal_deployment=True,
            backend_bundle=tmp_path,
            base_archive=tmp_path,
        )
