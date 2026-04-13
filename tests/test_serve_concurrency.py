"""Verify the HTTP server handles concurrent requests in parallel.

Regression test for the swap from `HTTPServer` (single-threaded) to
`ThreadingHTTPServer` in `start_server()`.
"""
from __future__ import annotations

import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _SlowHandler(BaseHTTPRequestHandler):
    """Handler that sleeps for 200ms — fast enough for tests, slow enough
    that serial handling of 5 requests would take ~1s."""

    SLEEP_SECONDS = 0.2

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        time.sleep(self.SLEEP_SECONDS)
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: A002 — silence access logs
        return


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _hit(port: int) -> int:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def test_threading_http_server_handles_requests_in_parallel():
    port = _free_port()
    server = ThreadingHTTPServer(("127.0.0.1", port), _SlowHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    # Warm the listener.
    assert _hit(port) == 200

    n = 5
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=n) as pool:
        statuses = list(pool.map(_hit, [port] * n))
    elapsed = time.perf_counter() - start

    server.shutdown()
    server.server_close()

    assert all(code == 200 for code in statuses), statuses
    # Serial handling would take >= n * SLEEP_SECONDS (5 * 0.2 = 1.0s).
    # Parallel should comfortably finish under 2x a single request.
    assert elapsed < 2 * _SlowHandler.SLEEP_SECONDS, (
        f"requests appear serialized: {elapsed:.3f}s for {n} parallel requests"
    )


def test_serve_module_uses_threading_http_server():
    """Smoke check: start_server constructs a ThreadingHTTPServer, not the
    single-threaded HTTPServer."""
    import inspect

    from localsmartz import serve

    src = inspect.getsource(serve.start_server)
    assert "ThreadingHTTPServer" in src, src
