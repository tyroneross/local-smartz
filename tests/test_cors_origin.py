"""SEC-002: state-changing methods (POST/PUT/DELETE) must reject cross-origin
requests whose Origin host isn't localhost/127.0.0.1/::1, and never send
Access-Control-Allow-Origin: * on those responses. Requests carrying no
Origin header (native app URLSession, curl) always proceed."""
from __future__ import annotations

import json
import threading
from http.client import HTTPConnection

import pytest


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


@pytest.fixture
def server():
    from http.server import HTTPServer
    from localsmartz.serve import LocalSmartzHandler

    srv = HTTPServer(("127.0.0.1", 0), LocalSmartzHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield port
    srv.shutdown()
    # Also close the listening socket (not just stop serve_forever) — this
    # test file spins up many short-lived servers per run, and leaving the
    # fd open (SO_REUSEADDR lets the next ephemeral-port bind succeed anyway)
    # was observed to cause rare cross-test connection resets.
    srv.server_close()
    thread.join(timeout=2.0)


def _request(port: int, method: str, path: str, *, origin: str | None = None,
             payload: dict | None = None) -> tuple[int, dict, dict]:
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {"Content-Type": "application/json"}
    if origin is not None:
        headers["Origin"] = origin
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    status = resp.status
    raw = resp.read()
    resp_headers = dict(resp.getheaders())
    conn.close()
    try:
        parsed_body = json.loads(raw.decode("utf-8")) if raw else {}
    except json.JSONDecodeError:
        parsed_body = {}
    return status, parsed_body, resp_headers


# ── POST /api/settings — the exemplar state-changing route ────────────────

def test_post_settings_evil_origin_403_no_change(server):
    from localsmartz import global_config

    status, body, headers = _request(
        server, "POST", "/api/settings",
        origin="https://evil.example",
        payload={"local_only": True},
    )
    assert status == 403
    assert body == {"error": "forbidden_origin"}
    assert global_config.get("local_only") is False
    # Never send ACAO:* (or the evil origin) on a rejected write.
    assert headers.get("Access-Control-Allow-Origin") != "*"
    assert headers.get("Access-Control-Allow-Origin") != "https://evil.example"


def test_post_settings_localhost_origin_allowed(server):
    status, body, headers = _request(
        server, "POST", "/api/settings",
        origin="http://localhost:3000",
        payload={"local_only": True},
    )
    assert status == 200
    assert body["local_only"] is True
    assert headers.get("Access-Control-Allow-Origin") == "http://localhost:3000"


def test_post_settings_127_0_0_1_origin_allowed(server):
    status, body, headers = _request(
        server, "POST", "/api/settings",
        origin="http://127.0.0.1:8080",
        payload={"local_only": True},
    )
    assert status == 200
    assert body["local_only"] is True


def test_post_settings_no_origin_allowed(server):
    """Native app URLSession / curl never send Origin — always proceed."""
    status, body, headers = _request(
        server, "POST", "/api/settings",
        origin=None,
        payload={"local_only": True},
    )
    assert status == 200
    assert body["local_only"] is True
    # No-Origin path keeps the permissive header (native clients ignore CORS).
    assert headers.get("Access-Control-Allow-Origin") == "*"


# ── PUT / DELETE also gated ─────────────────────────────────────────────────

def test_put_forbidden_origin_403(server):
    status, body, _ = _request(
        server, "PUT", "/api/agents/analyzer/prompt",
        origin="https://evil.example",
        payload={"prompt": "x"},
    )
    assert status == 403
    assert body == {"error": "forbidden_origin"}


def test_delete_forbidden_origin_403(server):
    status, body, _ = _request(
        server, "DELETE", "/api/models?name=foo",
        origin="https://evil.example",
    )
    assert status == 403
    assert body == {"error": "forbidden_origin"}


# ── OPTIONS preflight mirrors the same gate ─────────────────────────────────

def test_options_preflight_forbidden_origin_403(server):
    status, _, _ = _request(server, "OPTIONS", "/api/settings", origin="https://evil.example")
    assert status == 403


def test_options_preflight_localhost_allowed(server):
    status, _, headers = _request(server, "OPTIONS", "/api/settings", origin="http://localhost:5173")
    assert status == 204
    assert headers.get("Access-Control-Allow-Origin") == "http://localhost:5173"


# ── GET is unaffected (read-only, keeps legacy behavior) ────────────────────

def test_get_keeps_wildcard_cors(server):
    status, _, headers = _request(server, "GET", "/api/settings", origin="https://evil.example")
    assert status == 200
    assert headers.get("Access-Control-Allow-Origin") == "*"
