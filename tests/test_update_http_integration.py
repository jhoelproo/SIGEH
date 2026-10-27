"""Real HTTP interruption and resume, using only an isolated loopback server."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from update_download import CHUNK_SIZE, download_archive


def test_real_http_interruption_resumes_after_a_new_invocation(tmp_path):
    data = bytes(range(256)) * (CHUNK_SIZE * 3 // 256)
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested = self.headers.get("Range")
            calls.append(requested)
            start = (
                int(requested.removeprefix("bytes=").removesuffix("-"))
                if requested
                else 0
            )
            self.send_response(206 if requested else 200)
            self.send_header("Content-Length", str(len(data) - start))
            if requested:
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}"
                )
            self.end_headers()
            self.wfile.write(data[start:] if requested else data[: 2 * CHUNK_SIZE])
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    checksum = hashlib.sha256(data).hexdigest()
    url = f"http://127.0.0.1:{server.server_port}/archive.zip"
    try:
        with pytest.raises(requests.RequestException):
            download_archive(url, checksum, tmp_path)
        assert (tmp_path / f"{checksum}.part").stat().st_size == 2 * CHUNK_SIZE
        complete = download_archive(url, checksum, tmp_path)
        assert complete.read_bytes() == data
        assert calls == [None, f"bytes={2 * CHUNK_SIZE}-"]
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)
