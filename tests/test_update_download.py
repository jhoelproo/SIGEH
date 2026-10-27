"""Interrupted transfers retain verified-channel bytes without touching an install."""

import hashlib
from unittest.mock import Mock

import pytest
import requests

from update_download import download_archive
from sigeh_update import UpdateError


def response(data, status=200, headers=None, error=None):
    result = Mock(
        status_code=status, headers=headers or {"Content-Length": str(len(data))}
    )
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    result.raise_for_status.side_effect = (
        requests.HTTPError(str(status)) if status >= 400 else None
    )

    def chunks(_size):
        yield data
        if error:
            raise error

    result.iter_content.side_effect = chunks
    return result


def test_retry_after_restart_resumes_partial_bytes(tmp_path):
    data = b"abcdef"
    digest = hashlib.sha256(data).hexdigest()
    get = Mock(return_value=response(b"abc", error=requests.ConnectionError("offline")))
    with pytest.raises(requests.ConnectionError):
        download_archive("https://example.test/a", digest, tmp_path, get=get)
    get.return_value = response(
        b"def", 206, {"Content-Range": "bytes 3-5/6", "Content-Length": "3"}
    )
    progress = Mock()
    result = download_archive(
        "https://example.test/a", digest, tmp_path, get=get, progress=progress
    )
    assert result.read_bytes() == data
    assert get.call_args.kwargs["headers"]["Range"] == "bytes=3-"
    assert progress.call_args.args == (6, 6)


def test_ignoring_range_restarts_instead_of_appending(tmp_path):
    digest = hashlib.sha256(b"complete").hexdigest()
    (tmp_path / f"{digest}.part").write_bytes(b"partial")
    get = Mock(return_value=response(b"complete"))
    assert (
        download_archive("url", digest, tmp_path, get=get).read_bytes() == b"complete"
    )


@pytest.mark.parametrize(
    "content_range", ["bytes 0-2/6", "bytes 3-8/6", "garbage", "bytes 3-5/*"]
)
def test_bad_range_is_rejected(tmp_path, content_range):
    digest = "a" * 64
    (tmp_path / f"{digest}.part").write_bytes(b"abc")
    get = Mock(return_value=response(b"def", 206, {"Content-Range": content_range}))
    with pytest.raises(UpdateError):
        download_archive("url", digest, tmp_path, get=get)
    assert (tmp_path / f"{digest}.part").read_bytes() == b"abc"


def test_cached_complete_archive_does_not_download_again(tmp_path):
    data = b"cached"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(data)
    get = Mock(side_effect=AssertionError("unexpected download"))
    assert download_archive("url", digest, tmp_path, get=get).read_bytes() == data


def test_checksum_mismatch_discards_partial(tmp_path):
    digest = "a" * 64
    with pytest.raises(UpdateError, match="SHA-256"):
        download_archive(
            "url", digest, tmp_path, get=Mock(return_value=response(b"wrong"))
        )
    assert not (tmp_path / f"{digest}.part").exists()


def test_incomplete_transfer_keeps_partial_for_next_start(tmp_path):
    digest = "a" * 64
    get = Mock(return_value=response(b"abc", headers={"Content-Length": "6"}))
    with pytest.raises(UpdateError, match="incompleta"):
        download_archive("url", digest, tmp_path, get=get)
    assert (tmp_path / f"{digest}.part").read_bytes() == b"abc"


@pytest.mark.parametrize("checksum", ["", "../escape", "A" * 64, "a" * 63, "g" * 64])
def test_invalid_checksum_cannot_select_cache_path(tmp_path, checksum):
    with pytest.raises(UpdateError):
        download_archive("url", checksum, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_finished_partial_is_promoted_without_network(tmp_path):
    data = b"finished before restart"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / f"{digest}.part").write_bytes(data)
    result = download_archive(
        "url", digest, tmp_path, get=Mock(side_effect=AssertionError)
    )
    assert result.read_bytes() == data
    assert not (tmp_path / f"{digest}.part").exists()


def test_invalid_complete_cache_is_replaced(tmp_path):
    digest = hashlib.sha256(b"good").hexdigest()
    (tmp_path / f"{digest}.zip").write_bytes(b"bad")
    result = download_archive(
        "url", digest, tmp_path, get=Mock(return_value=response(b"good"))
    )
    assert result.read_bytes() == b"good"


def test_unsatisfiable_range_restarts_once(tmp_path):
    digest = hashlib.sha256(b"good").hexdigest()
    (tmp_path / f"{digest}.part").write_bytes(b"too many bytes")
    failed = response(b"", 416)
    http_response = requests.Response()
    http_response.status_code = 416
    failed.raise_for_status.side_effect = requests.HTTPError(response=http_response)
    get = Mock(side_effect=[failed, response(b"good")])
    assert download_archive("url", digest, tmp_path, get=get).read_bytes() == b"good"
    assert "Range" not in get.call_args.kwargs["headers"]


@pytest.mark.parametrize("status", [None, 403])
def test_other_http_errors_keep_partial(tmp_path, status):
    digest = "a" * 64
    path = tmp_path / f"{digest}.part"
    path.write_bytes(b"partial")
    failed = response(b"")
    http_response = requests.Response() if status else None
    if http_response is not None:
        http_response.status_code = status
    failed.raise_for_status.side_effect = requests.HTTPError(response=http_response)
    with pytest.raises(requests.HTTPError):
        download_archive("url", digest, tmp_path, get=Mock(return_value=failed))
    assert path.read_bytes() == b"partial"


@pytest.mark.parametrize(
    "status,headers", [(201, {}), (206, {"Content-Range": "bytes 0-1/3"})]
)
def test_unexpected_status_or_truncated_range_rejected(tmp_path, status, headers):
    with pytest.raises(UpdateError):
        download_archive(
            "url",
            "a" * 64,
            tmp_path,
            get=Mock(return_value=response(b"ab", status, headers)),
        )


def test_unknown_length_and_empty_keepalive_chunks(tmp_path):
    data = b"abcdef"
    digest = hashlib.sha256(data).hexdigest()
    stream = response(data, headers={"Other": "value"})
    stream.iter_content.side_effect = lambda _size: iter([b"", b"abc", b"", b"def"])
    assert (
        download_archive(
            "url", digest, tmp_path, get=Mock(return_value=stream)
        ).read_bytes()
        == data
    )
